# Mafia Game Bot

Telegram üçün Mafia oyun botu.

## Quraşdırma

1. Python 3.7 və ya daha yuxarı versiya quraşdırın
2. Lazımi paketləri quraşdırın:
```bash
pip install -r requirements.txt
```
3. `.env` faylı yaradın və Telegram Bot Token əlavə edin:
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

## İstifadə

1. Botu işə salın:
```bash
python mafia_bot.py
```

2. Qrupda `/game` əmrini istifadə edərək oyunu başladın
3. Oyuna qatılmaq üçün "Oyuna qatıl" düyməsini basın
4. Minimum 3 oyunçu qatıldıqdan sonra "Oyunu başlat" düyməsini basın

## Oyun qaydaları

- Minimum oyunçu sayı: 3
- Maksimum oyunçu sayı: 8
- Gecə müddəti: 30 saniyə
- Gündüz müzakirə müddəti: 45 saniyə
- Səs vermə müddəti: 15 saniyə

## Rollar

### Aktiv Rollar:
- Don Mafia - Bütün mafiyaların başçısıdır
- Mafia - Don mafia öldüyü zaman Mafiyalardan biri Don mafia roluna keçər
- Komisar Katani - Gecə olduğu zaman bir oyunçunu yoxlaya və ya silahını çəkərək vura bilər
- Hekim - İlk gecə istəsə özünü xilas edə bilər

### Passiv Rollar:
- Vətəndaş - Heç bir aktiv rolu yoxdur
- Dəli - Bu rola sahib olan oyunçu oyun başladığı zaman random rollardan birini verər 