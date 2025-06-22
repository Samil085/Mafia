import os
import json
import random
import asyncio
import threading
import time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext, MessageHandler, Filters
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Constants
MIN_PLAYERS = 3
MAX_PLAYERS = 8
NIGHT_DURATION = 30  # seconds
DAY_DURATION = 45   # seconds
VOTE_DURATION = 15  # seconds
WIN_REWARD = 20
LOSE_REWARD = 10

# Game roles with emojis and descriptions
ROLES = {
    'don_mafia': {
        'name': '🕴 Don Mafia',
        'description': 'Bütün mafiyaların başçısıdır. Gecə olduğu zaman bir oyunçunu seçərək öldürə bilər.',
        'is_active': True
    },
    'mafia': {
        'name': '🤵🏻 Mafia',
        'description': 'Don mafia öldüyü zaman Don mafia roluna keçər. Gecə olduğu zaman Don mafianın əmri ilə hərəkət edər.',
        'is_active': True
    },
    'doctor': {
        'name': '👨🏻‍⚕️ Hekim',
        'description': 'İlk gecə istəsə özünü xilas edə bilər. Hər gecə bir oyunçunu seçərək onu ölümdən qoruya bilər.',
        'is_active': True
    },
    'detective': {
        'name': '🕵🏻‍♂️ Komisar Katani',
        'description': 'Gecə olduğu zaman bir oyunçunu yoxlaya və ya silahını çəkərək vura bilər.',
        'is_active': True
    },
    'citizen': {
        'name': '👫 Vətəndaş',
        'description': 'Heç bir aktiv rolu yoxdur. Gecə danışa bilməz. Səhər müzakirə edərək mafiyanı tapıb asdıra bilər.',
        'is_active': False
    },
    'crazy': {
        'name': '🧌 Dəli',
        'description': 'Bu rola sahib olan oyunçu oyun başladığı zaman random rollardan birini verər. Ancaq hekim və komisar rollarını ala bilər.',
        'is_active': False
    }
}

# Role categories
ROLE_CATEGORIES = {
    'citizens': ['doctor', 'detective', 'citizen', 'crazy'],
    'mafia': ['don_mafia', 'mafia']
}

class UserData:
    def __init__(self, user_id):
        self.user_id = user_id
        self.games_played = 0
        self.games_won = 0
        self.total_money = 0
        self.load_data()

    def load_data(self):
        file_path = f"data/users/{self.user_id}.json"
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.games_played = data.get('games_played', 0)
                self.games_won = data.get('games_won', 0)
                self.total_money = data.get('total_money', 0)

    def save_data(self):
        os.makedirs("data/users", exist_ok=True)
        file_path = f"data/users/{self.user_id}.json"
        data = {
            'games_played': self.games_played,
            'games_won': self.games_won,
            'total_money': self.total_money
        }
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def add_game_result(self, won):
        self.games_played += 1
        if won:
            self.games_won += 1
            self.total_money += WIN_REWARD
        else:
            self.total_money += LOSE_REWARD
        self.save_data()

class MafiaGame:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.players = {}  # {user_id: {'name': name, 'role': role}}
        self.game_started = False
        self.phase = None  # 'night' or 'day'
        self.admin_id = None
        self.night_actions = {}  # {user_id: {'target_id': target_id, 'action': action}}
        self.day_number = 1
        self.phase_timer = None
        self.bot = None
        self.votes = {}  # {voter_id: target_id}
        self.winners = []  # List to store winning team
        self.save_game_state()

    def set_bot(self, bot):
        self.bot = bot

    def start_phase_timer(self, phase, duration):
        if self.phase_timer:
            self.phase_timer.cancel()
        
        def timer_callback():
            if phase == 'night':
                self.process_night_actions()
            elif phase == 'day':
                self.end_day()
            elif phase == 'vote':
                self.end_vote()
        
        self.phase_timer = threading.Timer(duration, timer_callback)
        self.phase_timer.start()

    def end_night(self):
        if self.bot:
            # Send morning message
            morning_message = self.generate_morning_message()
            self.bot.send_message(
                chat_id=self.chat_id,
                text=morning_message,
                parse_mode='HTML'
            )
            # Start day phase
            self.phase = 'day'
            self.start_phase_timer('day', DAY_DURATION)
            self.save_game_state()

    def end_day(self):
        if self.bot:
            # Start voting phase
            self.phase = 'vote'
            vote_message = (
                "İndi səs vermə vaxtıdır!\n"
                "Kimin mafiya olduğunu düşünürsünüz?"
            )
            self.bot.send_message(chat_id=self.chat_id, text=vote_message)
            
            # Send vote keyboard to each player
            for user_id, player in self.players.items():
                if 'is_dead' not in player:
                    try:
                        keyboard = self.generate_vote_keyboard(user_id)
                        self.bot.send_message(
                            chat_id=user_id,
                            text="Səs vermək üçün bir oyunçu seçin:",
                            reply_markup=keyboard
                        )
                    except Exception as e:
                        print(f"Error sending vote keyboard to user {user_id}: {e}")
            
            self.start_phase_timer('vote', VOTE_DURATION)
            self.save_game_state()

    def end_vote(self):
        if self.bot:
            # Process any remaining votes
            self.process_voting_results()
            # Note: start_next_night will be called after voting results are processed

    def add_player(self, user_id, name):
        if len(self.players) >= MAX_PLAYERS:
            return False, "Oyun artıq maksimum oyunçu sayına çatıb!"
        if user_id in self.players:
            return False, "Siz artıq qeydiyyatdan keçmisiniz!"
        self.players[user_id] = {'name': name, 'role': None}
        self.save_game_state()
        return True, "Qeydiyyat uğurla tamamlandı!"

    def start_game(self, admin_id):
        if len(self.players) < MIN_PLAYERS:
            return False, f"Minimum {MIN_PLAYERS} oyunçu lazımdır!"
        self.admin_id = admin_id
        self.game_started = True
        self.assign_roles()
        self.phase = 'night'
        self.start_phase_timer('night', NIGHT_DURATION)
        self.save_game_state()
        return True, self.generate_game_start_message()

    def generate_role_message(self, user_id):
        player = self.players[user_id]
        role = ROLES[player['role']]
        
        message = (
            f"Sizin rolunuz: {role['name']}\n\n"
            f"{role['description']}\n"
        )
        
        if role['is_active']:
            message += "\nSeciminizi edin:"
            
        return message

    def generate_player_selection_keyboard(self, user_id, action_type=None):
        player = self.players[user_id]
        role = player['role']
        
        # For detective, first show action selection
        if role == 'detective' and not action_type:
            keyboard = [
                [
                    InlineKeyboardButton("Yoxla", callback_data="detective_check"),
                    InlineKeyboardButton("Silahını çək", callback_data="detective_shoot")
                ]
            ]
            return InlineKeyboardMarkup(keyboard)
        
        # Filter out players based on role
        available_players = []
        for target_id, target in self.players.items():
            if target_id != user_id:  # Can't select self
                if role in ['don_mafia', 'mafia']:
                    # Mafia can't see other mafia members
                    if target['role'] not in ROLE_CATEGORIES['mafia']:
                        available_players.append((target_id, target['name']))
                else:
                    available_players.append((target_id, target['name']))
        
        keyboard = []
        for target_id, name in available_players:
            keyboard.append([InlineKeyboardButton(name, callback_data=f"select_{target_id}")])
        
        return InlineKeyboardMarkup(keyboard)

    def process_night_action(self, user_id, target_id, action=None):
        player = self.players[user_id]
        role = player['role']
        
        if role == 'don_mafia':
            self.night_actions[user_id] = {'target_id': target_id, 'action': 'kill'}
            return f"🕴 Don qurbanı seçdi.."
        elif role == 'doctor':
            self.night_actions[user_id] = {'target_id': target_id, 'action': 'heal'}
            return f"👨🏻‍⚕️ Həkim gecə növbəsinə çıxdı.."
        elif role == 'detective':
            if action == 'check':
                self.night_actions[user_id] = {'target_id': target_id, 'action': 'check'}
                return f"🕵🏻‍♂️ Komissar yaramazları axtarmağa getdi!"
            else:  # shoot
                self.night_actions[user_id] = {'target_id': target_id, 'action': 'shoot'}
                return f"🕵🏻‍♂️ Komissar silahını çəkdi"

    def generate_morning_message(self):
        # Process night actions
        killed_players = []
        healed_players = []
        
        for user_id, action in self.night_actions.items():
            target_id = action['target_id']
            if action['action'] == 'kill':
                killed_players.append(target_id)
            elif action['action'] == 'heal':
                healed_players.append(target_id)
        
        # Remove healed players from killed list
        killed_players = [p for p in killed_players if p not in healed_players]
        
        # Generate night results message
        night_results = []
        for killed_id in killed_players:
            killer_role = None
            for user_id, action in self.night_actions.items():
                if action['target_id'] == killed_id and action['action'] == 'kill':
                    killer_role = ROLES[self.players[user_id]['role']]['name']
                    break
            
            if killer_role:
                night_results.append(
                    f"{ROLES[self.players[killed_id]['role']]['name']} gecə öldürüldü. "
                    f"Onun öldürən {killer_role} idi."
                )
        
        for healed_id in healed_players:
            if healed_id in killed_players:
                night_results.append(
                    f"{ROLES[self.players[healed_id]['role']]['name']} gecə ölümlə üzləşdi "
                    f"ancaq {ROLES['doctor']['name']} iş başında idi, o ölmədi."
                )
        
        # Generate player list with HTML links
        player_list = "\n".join([
            f"{i+1}. <a href='tg://user?id={user_id}'>{player['name']}</a>"
            for i, (user_id, player) in enumerate(self.players.items())
        ])
        
        # Count remaining roles
        role_counts = {'citizens': 0, 'mafia': 0}
        for player in self.players.values():
            if player['role'] in ROLE_CATEGORIES['citizens']:
                role_counts['citizens'] += 1
            elif player['role'] in ROLE_CATEGORIES['mafia']:
                role_counts['mafia'] += 1
        
        message = (
            f"{self.chat_id}, Sabahın Xeyir!!\n"
            "Günəş, səkilərdə gecə tökülən qanı qurudaraq, çıxır........\n"
            f"☀️Gün: {self.day_number}\n\n"
        )
        
        if night_results:
            message += "\n".join(night_results) + "\n\n"
        
        message += (
            f"Sağ qalan oyunçular:\n{player_list}\n\n"
            "Onlardan:\n\n"
            f"👫Dinc Sakinlər - {role_counts['citizens']}\n"
            "---------\n"
            f"👥Mafiyalar - {role_counts['mafia']}\n\n"
            f"🎪 Cəmi: {len(self.players)} nəfər\n\n"
            "İndi gecənin nəticələrini müzakirə etmək, səbəb və təsirləri anlamaq vaxtıdır......"
        )
        
        return message

    def generate_game_start_message(self):
        # Count roles and collect assigned roles
        role_counts = {'citizens': 0, 'mafia': 0}
        assigned_roles = {'citizens': set(), 'mafia': set()}
        
        for player in self.players.values():
            if player['role'] in ROLE_CATEGORIES['citizens']:
                role_counts['citizens'] += 1
                assigned_roles['citizens'].add(player['role'])
            elif player['role'] in ROLE_CATEGORIES['mafia']:
                role_counts['mafia'] += 1
                assigned_roles['mafia'].add(player['role'])

        # Generate player list with HTML links
        player_list = "\n".join([
            f"{i+1}. <a href='tg://user?id={user_id}'>{player['name']}</a>"
            for i, (user_id, player) in enumerate(self.players.items())
        ])

        # Generate role list for assigned roles only
        citizen_roles = [ROLES[role]['name'] for role in assigned_roles['citizens']]
        mafia_roles = [ROLES[role]['name'] for role in assigned_roles['mafia']]

        message = (
            "Mafia Combat Oyunu Başladı\n\n"
            "Gecə düşür!\n"
            "Yalnız cəsarətlilər və qorxmazlar şəhər küçələrinə çıxırlar. "
            "Səhər başlarını saymağa çalışacağıq...\n\n"
            f"Sağ qalan oyunçular:\n{player_list}\n\n"
            "Onlardan:\n\n"
            f"👫Dinc Sakinlər - {role_counts['citizens']}\n"
            f" {' '.join(citizen_roles)}\n"
            "---------\n"
            f"👥Mafiyalar - {role_counts['mafia']}\n"
            f" {' '.join(mafia_roles)}\n\n"
            f"🎪 Cəmi: {len(self.players)} nəfər"
        )
        return message

    def assign_roles(self):
        available_roles = []
        # Add roles based on player count
        if len(self.players) >= 3:
            available_roles.extend(['don_mafia', 'doctor', 'detective'])
        if len(self.players) >= 4:
            available_roles.append('mafia')
        if len(self.players) >= 5:
            available_roles.append('crazy')
        
        # Fill remaining slots with citizens
        remaining_slots = len(self.players) - len(available_roles)
        available_roles.extend(['citizen'] * remaining_slots)
        
        # Shuffle and assign roles
        random.shuffle(available_roles)
        for (user_id, player), role in zip(self.players.items(), available_roles):
            player['role'] = role

    def save_game_state(self):
        data_dir = 'data'
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
        
        file_path = os.path.join(data_dir, f'game_{self.chat_id}.json')
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump({
                'chat_id': self.chat_id,
                'players': self.players,
                'game_started': self.game_started,
                'phase': self.phase,
                'admin_id': self.admin_id,
                'night_actions': self.night_actions,
                'day_number': self.day_number,
                'votes': self.votes
            }, f, ensure_ascii=False, indent=4)

    @classmethod
    def load_game_state(cls, chat_id):
        file_path = os.path.join('data', f'game_{chat_id}.json')
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                game = cls(chat_id)
                game.players = data['players']
                game.game_started = data['game_started']
                game.phase = data['phase']
                game.admin_id = data['admin_id']
                game.night_actions = data.get('night_actions', {})
                game.day_number = data.get('day_number', 1)
                game.votes = data.get('votes', {})
                return game
        return None

    def generate_vote_keyboard(self, voter_id):
        # Filter out dead players and voter
        available_players = []
        for target_id, player in self.players.items():
            if target_id != voter_id and 'is_dead' not in player:
                available_players.append((target_id, player['name']))
        
        keyboard = []
        for target_id, name in available_players:
            keyboard.append([InlineKeyboardButton(name, callback_data=f"vote_{target_id}")])
        
        return InlineKeyboardMarkup(keyboard)

    def process_vote(self, voter_id, target_id):
        self.votes[voter_id] = target_id
        self.save_game_state()
        
        # Check if all alive players have voted
        alive_players = [p for p in self.players if 'is_dead' not in self.players[p]]
        if len(self.votes) == len(alive_players):
            self.process_voting_results()

    def process_voting_results(self):
        # Count votes
        vote_counts = {}
        for target_id in self.votes.values():
            vote_counts[target_id] = vote_counts.get(target_id, 0) + 1
        
        if not vote_counts:
            message = "Oyuncular qərar verə bilmədilər, heç kim asılmadı."
            self.bot.send_message(chat_id=self.chat_id, text=message)
            self.start_next_night()
            return
        
        # Find player with most votes
        max_votes = max(vote_counts.values())
        candidates = [p for p, v in vote_counts.items() if v == max_votes]
        
        if len(candidates) > 1:
            message = "Oyuncular qərar verə bilmədilər, heç kim asılmadı."
            self.bot.send_message(chat_id=self.chat_id, text=message)
            self.start_next_night()
            return
        
        # Single candidate with most votes
        target_id = candidates[0]
        target_name = self.players[target_id]['name']
        
        # Create confirmation message
        message = f"{target_name} asmaq istədiyinizə əminsiniz?"
        keyboard = [
            [
                InlineKeyboardButton(f"As {max_votes}", callback_data=f"hang_{target_id}"),
                InlineKeyboardButton(f"Asma {max_votes}", callback_data="no_hang")
            ]
        ]
        self.bot.send_message(
            chat_id=self.chat_id,
            text=message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    def hang_player(self, target_id):
        self.players[target_id]['is_dead'] = True
        target_name = self.players[target_id]['name']
        target_role = ROLES[self.players[target_id]['role']]['name']
        
        message = f"{target_name} ({target_role}) asıldı!"
        self.bot.send_message(chat_id=self.chat_id, text=message)
        
        # Check if game is over
        if self.check_game_end():
            return
        
        self.start_next_night()

    def check_game_end(self):
        # Count remaining players by role
        mafia_count = 0
        citizen_count = 0
        
        for player in self.players.values():
            if 'is_dead' not in player:
                if player['role'] in ROLE_CATEGORIES['mafia']:
                    mafia_count += 1
                else:
                    citizen_count += 1
        
        if mafia_count == 0:
            message = "🎉 Mülki sakinlər qalib gəldi! Mafiyalar məğlub oldu!"
            self.bot.send_message(chat_id=self.chat_id, text=message)
            self.game_started = False
            self.winners = ['citizens']
            self.distribute_rewards()
            self.reset_game()
            return True
        
        if mafia_count >= citizen_count:
            message = "🎭 Mafiyalar qalib gəldi! Şəhər onların əlində!"
            self.bot.send_message(chat_id=self.chat_id, text=message)
            self.game_started = False
            self.winners = ['mafia']
            self.distribute_rewards()
            self.reset_game()
            return True
        
        return False

    def start_next_night(self):
        self.phase = 'night'
        self.day_number += 1
        self.votes = {}  # Reset votes
        self.night_actions = {}  # Reset night actions
        
        night_message = (
            "Gecə düşür!\n"
            "Yalnız cəsarətlilər və qorxmazlar şəhər küçələrinə çıxırlar..."
        )
        self.bot.send_message(chat_id=self.chat_id, text=night_message)
        
        # Send role selection to active players
        for user_id, player in self.players.items():
            if 'is_dead' not in player and ROLES[player['role']]['is_active']:
                try:
                    keyboard = self.generate_player_selection_keyboard(user_id)
                    self.bot.send_message(
                        chat_id=user_id,
                        text="Seciminizi edin:",
                        reply_markup=keyboard
                    )
                except Exception as e:
                    print(f"Error sending role selection to user {user_id}: {e}")
        
        self.start_phase_timer('night', NIGHT_DURATION)
        self.save_game_state()

    def process_night_actions(self):
        if self.bot:
            # Process commissioner's check
            for user_id, action in self.night_actions.items():
                if self.players[user_id]['role'] == 'commissioner':
                    target_id = action['target_id']
                    target_name = self.players[target_id]['name']
                    target_role = self.players[target_id]['role']
                    role_name = ROLES[target_role]['name']
                    try:
                        self.bot.send_message(
                            chat_id=user_id,
                            text=f"{target_name} {role_name}dir"
                        )
                    except Exception as e:
                        print(f"Error sending commissioner check result to user {user_id}: {e}")

            # Process doctor's save
            saved_players = set()
            for user_id, action in self.night_actions.items():
                if self.players[user_id]['role'] == 'doctor':
                    saved_players.add(action['target_id'])

            # Process mafia's kill
            killed_players = set()
            for user_id, action in self.night_actions.items():
                if self.players[user_id]['role'] in ROLE_CATEGORIES['mafia']:
                    target_id = action['target_id']
                    if target_id not in saved_players:
                        killed_players.add(target_id)
                        self.players[target_id]['is_dead'] = True

            # Send morning message
            morning_message = "Səhər oldu! Gecə hadisələri:\n\n"
            
            if killed_players:
                killed_names = [self.players[p]['name'] for p in killed_players]
                morning_message += f"Gecə {', '.join(killed_names)} öldürüldü.\n"
            else:
                morning_message += "Bu gecə heç kim ölmədi.\n"
            
            self.bot.send_message(chat_id=self.chat_id, text=morning_message)
            
            # Check if game is over
            if self.check_game_end():
                return
            
            # Start day phase
            self.phase = 'day'
            self.start_phase_timer('day', DAY_DURATION)
            self.save_game_state()

    def distribute_rewards(self):
        for user_id, player in self.players.items():
            user_data = UserData(user_id)
            won = False
            
            if 'mafia' in self.winners and player['role'] in ROLE_CATEGORIES['mafia']:
                won = True
            elif 'citizens' in self.winners and player['role'] not in ROLE_CATEGORIES['mafia']:
                won = True
            
            user_data.add_game_result(won)
            
            # Send reward message to player
            reward = WIN_REWARD if won else LOSE_REWARD
            try:
                self.bot.send_message(
                    chat_id=user_id,
                    text=f"Oyun bitdi! {'Qalib' if won else 'Məğlub'} oldunuz.\n"
                         f"Mükafat: {reward} dollar\n"
                         f"Ümumi balansınız: {user_data.total_money} dollar"
                )
            except Exception as e:
                print(f"Error sending reward message to user {user_id}: {e}")

    def reset_game(self):
        # Save game history
        history_file = f"data/game_history_{self.chat_id}.json"
        game_data = {
            'timestamp': datetime.now().isoformat(),
            'players': self.players,
            'winners': self.winners,
            'day_number': self.day_number
        }
        
        if os.path.exists(history_file):
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
        else:
            history = []
        
        history.append(game_data)
        
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=4)
        
        # Reset current game file
        self.players = {}
        self.game_started = False
        self.phase = None
        self.admin_id = None
        self.night_actions = {}
        self.day_number = 1
        self.votes = {}
        self.winners = []
        self.save_game_state()

# Global games dictionary
active_games = {}

def start_game_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Check if user is admin
    chat_member = context.bot.get_chat_member(chat_id, user_id)
    if chat_member.status not in ['creator', 'administrator']:
        update.message.reply_text("Bu əmri yalnız qrup yöneticiləri istifadə edə bilər!")
        return

    # Create or get existing game
    if chat_id not in active_games:
        active_games[chat_id] = MafiaGame(chat_id)
    
    game = active_games[chat_id]
    
    # Create registration message
    keyboard = [
        [InlineKeyboardButton("Oyuna qatıl", url=f"https://t.me/{context.bot.username}?start=join_{chat_id}")],
        [InlineKeyboardButton("Oyunu başlat", callback_data=f"start_{chat_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    player_list = ", ".join([player['name'] for player in game.players.values()]) or "Hələ heç kim qatılmayıb"
    
    message_text = (
        "Qeydiyyat başladı! Qatılmaq üçün tələs!\n\n"
        f"<b>Qatılanlar:</b> {player_list}"
    )
    
    update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='HTML')

def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    
    if query.data.startswith("start_"):
        chat_id = int(query.data.split("_")[1])
        game = active_games.get(chat_id)
        
        if game:
            game.set_bot(context.bot)  # Set bot instance for game
            success, message = game.start_game(query.from_user.id)
            
            # Create keyboard for game start message
            keyboard = [
                [InlineKeyboardButton("Rolunuza baxın", url=f"https://t.me/{context.bot.username}?start=role_{chat_id}")],
                [InlineKeyboardButton("Bota keçid", url=f"https://t.me/{context.bot.username}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            query.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
            
            if success:
                # Send role information to each player
                for user_id, player in game.players.items():
                    try:
                        role_message = game.generate_role_message(user_id)
                        context.bot.send_message(
                            chat_id=user_id,
                            text=role_message,
                            parse_mode='HTML'
                        )
                        
                        # If player has an active role, send selection keyboard
                        if ROLES[player['role']]['is_active']:
                            keyboard = game.generate_player_selection_keyboard(user_id)
                            context.bot.send_message(
                                chat_id=user_id,
                                text="Seciminizi edin:",
                                reply_markup=keyboard
                            )
                    except Exception as e:
                        print(f"Error sending role to user {user_id}: {e}")
    
    elif query.data.startswith("select_"):
        target_id = int(query.data.split("_")[1])
        user_id = query.from_user.id
        
        # Find the game this user is in
        game = None
        for g in active_games.values():
            if user_id in g.players:
                game = g
                break
        
        if game:
            result_message = game.process_night_action(user_id, target_id)
            # Send result to group
            context.bot.send_message(
                chat_id=game.chat_id,
                text=result_message
            )
            # Confirm to user
            query.message.reply_text("Seçiminiz qeydə alındı.")
    
    elif query.data.startswith(("detective_check", "detective_shoot")):
        user_id = query.from_user.id
        
        # Find the game this user is in
        game = None
        for g in active_games.values():
            if user_id in g.players:
                game = g
                break
        
        if game and game.players[user_id]['role'] == 'detective':
            action = query.data.split("_")[1]
            keyboard = game.generate_player_selection_keyboard(user_id, action)
            query.message.reply_text("İndi hədəf seçin:", reply_markup=keyboard)
    
    elif query.data.startswith(("check_", "shoot_")):
        action, target_id = query.data.split("_")
        target_id = int(target_id)
        user_id = query.from_user.id
        
        # Find the game this user is in
        game = None
        for g in active_games.values():
            if user_id in g.players:
                game = g
                break
        
        if game and game.players[user_id]['role'] == 'detective':
            result_message = game.process_night_action(user_id, target_id, action)
            # Send result to group
            context.bot.send_message(
                chat_id=game.chat_id,
                text=result_message
            )
            # Confirm to user
            query.message.reply_text("Seçiminiz qeydə alındı.")

    elif query.data.startswith("vote_"):
        target_id = int(query.data.split("_")[1])
        user_id = query.from_user.id
        
        # Find the game this user is in
        game = None
        for g in active_games.values():
            if user_id in g.players:
                game = g
                break
        
        if game and game.phase == 'vote':
            game.process_vote(user_id, target_id)
            query.message.reply_text("Səs verməniz qeydə alındı.")
    
    elif query.data.startswith("hang_"):
        target_id = int(query.data.split("_")[1])
        user_id = query.from_user.id
        
        # Find the game this user is in
        game = None
        for g in active_games.values():
            if user_id in g.players:
                game = g
                break
        
        if game:
            game.hang_player(target_id)
    
    elif query.data == "no_hang":
        user_id = query.from_user.id
        
        # Find the game this user is in
        game = None
        for g in active_games.values():
            if user_id in g.players:
                game = g
                break
        
        if game:
            message = "Oyuncular qərar verə bilmədilər, heç kim asılmadı."
            game.bot.send_message(chat_id=game.chat_id, text=message)
            game.start_next_night()

def start_command(update: Update, context: CallbackContext):
    if context.args and context.args[0].startswith("join_"):
        chat_id = int(context.args[0].split("_")[1])
        game = active_games.get(chat_id)
        
        if game and not game.game_started:
            success, message = game.add_player(
                update.effective_user.id,
                update.effective_user.full_name
            )
            update.message.reply_text(message)
        else:
            update.message.reply_text("Oyun artıq başladılıb və ya mövcud deyil!")
    
    elif context.args and context.args[0].startswith("role_"):
        chat_id = int(context.args[0].split("_")[1])
        game = active_games.get(chat_id)
        
        if game and update.effective_user.id in game.players:
            role_message = game.generate_role_message(update.effective_user.id)
            update.message.reply_text(role_message, parse_mode='HTML')
            
            # If player has an active role, send selection keyboard
            if ROLES[game.players[update.effective_user.id]['role']]['is_active']:
                keyboard = game.generate_player_selection_keyboard(update.effective_user.id)
                update.message.reply_text("Seciminizi edin:", reply_markup=keyboard)

def message_handler(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return
    
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    message_text = update.message.text
    
    # Check if message is from a game chat
    game = None
    for g in active_games.values():
        if g.chat_id == chat_id:
            game = g
            break
    
    if not game or not game.game_started:
        return
    
    # Allow messages from admins with ! prefix
    if message_text.startswith('!'):
        # Check if user is admin
        chat_member = context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status in ['creator', 'administrator']:
            return
        else:
            context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
            return
    
    # Delete all messages during night phase
    if game.phase == 'night':
        context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        return
    
    # During day phase, only allow messages from active players
    if game.phase == 'day':
        if user_id not in game.players or 'is_dead' in game.players[user_id]:
            context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
            context.bot.send_message(
                chat_id=chat_id,
                text="Oyunda olmadığınız üçün mesaj yaza bilmərsiniz",
                reply_to_message_id=update.message.message_id
            )
            return

def profile_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user_data = UserData(user_id)
    
    # Calculate win rate
    win_rate = (user_data.games_won / user_data.games_played * 100) if user_data.games_played > 0 else 0
    
    # Create profile message
    profile_message = (
        "📊 Profil Məlumatları\n\n"
        f"🎮 Oynanmış oyun: {user_data.games_played}\n"
        f"🏆 Qazanılmış oyun: {user_data.games_won}\n"
        f"📈 Qalibiyyət faizi: {win_rate:.1f}%\n"
        f"💰 Ümumi balans: {user_data.total_money} dollar\n"
    )
    
    update.message.reply_text(profile_message)

def end_game_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Find the game
    game = None
    for g in active_games.values():
        if g.chat_id == chat_id:
            game = g
            break
    
    if not game:
        update.message.reply_text("Bu qrupda aktiv oyun yoxdur.")
        return
    
    # Check if user is admin or game admin
    if user_id != game.admin_id:
        chat_member = context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status not in ['creator', 'administrator']:
            update.message.reply_text("Yalnız adminlər və oyun admini oyunu bitirə bilər.")
            return
    
    if not game.game_started:
        update.message.reply_text("Oyun hələ başlamayıb.")
        return
    
    # End the game
    game.game_started = False
    game.phase = None
    
    # Distribute rewards
    game.winners = ['citizens']  # Default to citizens winning
    game.distribute_rewards()
    
    # Reset the game
    game.reset_game()
    
    update.message.reply_text("Oyun bitdi! Bütün oyunçular mükafatlarını aldılar.")

def help_command(update: Update, context: CallbackContext):
    help_message = (
        "🎮 Mafia Bot Əmrləri:\n\n"
        "📝 Qeyd: Bəzi əmrlər yalnız adminlər və ya oyun admini tərəfindən istifadə edilə bilər."
    )
    
    update.message.reply_text(help_message)

def main():
    # Create the Updater and pass it your bot's token
    updater = Updater(os.getenv('TELEGRAM_BOT_TOKEN'), use_context=True)
    
    # Get the dispatcher to register handlers
    dp = updater.dispatcher
    
    # Add handlers
    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("join", start_command))
    dp.add_handler(CommandHandler("startgame", start_game_command))
    dp.add_handler(CommandHandler("profile", profile_command))
    dp.add_handler(CallbackQueryHandler(button_callback))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, message_handler))
    
    # Start the Bot
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main() 