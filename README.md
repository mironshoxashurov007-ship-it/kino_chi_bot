# Kino kodlari Telegram bot

Admin botga kod va nom bilan video yuboradi. Bot kanalga videodan olingan preview rasmini nomi va kodi bilan joylaydi; to‘liq video kanalga yuborilmaydi. Foydalanuvchi kodni botga yuborishi yoki kanaldagi **Botda olish** tugmasini bosishi mumkin.

## Ishga tushirish

1. [@BotFather](https://t.me/BotFather) orqali bot yarating va token oling.
2. Kanal yarating, botni kanalga admin qilib qo‘shing. Unga xabar yuborish huquqi kerak.
3. Telegram’da `userinfobot` kabi bot orqali o‘z Telegram user ID’ingizni aniqlang.
4. `.env.example` faylidan `.env` nusxa olib, `BOT_TOKEN`, `CHANNEL_ID`, `CHANNEL_URL` va `ADMIN_IDS` qiymatlarini to‘ldiring.
5. Python 3.10 yoki yangiroq versiyada:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   python bot.py
   ```

Bot ishlayotgan kompyuter yoki server doim yoqilgan bo‘lishi kerak.

## 24/7 Railway serveriga joylash

Loyiha Railway CLI bilan to‘g‘ridan-to‘g‘ri joylashga tayyor. Railway akkauntida Hobby rejasi oyiga kamida $5; resurs sarfi limitdan oshsa qo‘shimcha to‘lov bo‘lishi mumkin. Persistent volume esa ishlatilgan hajm bo‘yicha alohida hisoblanadi. [Railway narxlari](https://docs.railway.com/pricing) va [volume narxlari](https://docs.railway.com/volumes/reference)ni ko‘ring.

1. Terminalni loyiha papkasida oching va Railway CLI o‘rnating:

   ```bash
   brew install railway
   ```

2. `railway up` buyrug‘ini bajaring. Brauzerda Railway akkauntiga kiring va loyiha yaratishni tasdiqlang. Bu buyruq ushbu papkani Railway’ga yuklaydi va deploy qiladi.
3. Railway dashboard’da bot service’ni tanlab, Variables bo‘limida `BOT_TOKEN`, `CHANNEL_ID`, `CHANNEL_URL`, `ADMIN_IDS` qiymatlarini kiriting. `.env` faylini deploy’ga qo‘shmang.
4. Service’га volume қўшиб, mount path’ни `/data` қилиб белгиланг. Bot kino kodlari bazasini `/data/movies.db` da saqlaydi; volume bo‘lmasa deploy paytida baza yo‘qolishi mumkin.
5. Deploy logs’da `Application started` ko‘ringandan keyin bot 24/7 ishlaydi. Laptopni va VS Code’ni ochiq qoldirish shart emas.

## Kino qo‘shish

Admin bot chatini ochib, kino videosini caption bilan yuboradi. Captionning birinchi qatorida admin tanlagan kod va ikkinchi qatorda kino nomi bo‘ladi; masalan:

```text
/add 122
O‘zbekcha kino nomi
```

Bot videoni SQLite bazasiga yozadi va kanalga preview rasmini kino nomi hamda `122` kodi bilan joylaydi. Mavjud kod qayta ishlatilsa, saqlangan video yangilanadi va kanalda yangi rasmli post chiqadi. Agar Telegram video uchun preview bermasa, bot kodli matnli post chiqaradi. Kino nomi ixtiyoriy.

Foydalanuvchi botga `122` yuborib videoni oladi. Admin `/delete 122` bilan kodni bazadan o‘chirishi mumkin.

## Bot buyruqlari

- `/catalog` — kinolar ro‘yxati va kodlari.
- `/search matn` — kino nomidan qidirish. Masalan: `/search sarguzasht`.
- `/latest` — oxirgi qo‘shilgan 10 ta kino.
- `/random` — katalogdan tasodifiy kino yuborish.
- `/id` — Telegram user ID’ingizni ko‘rsatish.
- `/stats` — admin uchun bazadagi kinolar soni va oxirgi qo‘shilgan kino.
- `/delete 122` — admin uchun kodli kinoni bazadan va yangi kanal postidan o‘chirish.
- `/delete_latest` — admin uchun oxirgi qo‘shilgan kinoni o‘chirish; bot tasdiqlashni so‘raydi.
- `/help` — buyruqlar bo‘yicha yordam.

Eski, kanal post ID’si saqlanmagan kinolarni o‘chirganda kanal postini qo‘lda o‘chirish kerak bo‘lishi mumkin.

## Eslatmalar

- Foydalanuvchilar botni avval bir marta ochib `/start` bosishi kerak; Telegram botlari foydalanuvchiga o‘zlari birinchi bo‘lib xabar yubora olmaydi.
- Botning kanalga e’lon yozishi uchun `CHANNEL_ID` to‘g‘ri bo‘lishi, bot esa kanal admini bo‘lishi zarur.
- Bot faqat admin ID’lari `ADMIN_IDS` ichida bo‘lgan foydalanuvchilargina `/add` va `/delete` qila oladi.
- Bot Telegram’da mavjud video `file_id` qiymatini saqlaydi, shuning uchun fayl server diskiga yuklanmaydi. Shu `file_id` shu bot tokeni doirasida ishlaydi.
