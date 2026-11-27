# Kino kodlari Telegram bot

Admin botga kod va nom bilan video yuboradi. Bot kanalga videodan olingan preview rasmini nomi va kodi bilan joylaydi; to‘liq video kanalga yuborilmaydi. Foydalanuvchi kodni botga yuborishi yoki kanaldagi **Botda olish** tugmasini bosishi mumkin.

Botdan foydalanishdan oldin foydalanuvchi katalog kanaliga a’zo bo‘lishi kerak. Bot a’zolikni tekshiradi va a’zo bo‘lmaganlarga kanalga qo‘shilish tugmasini ko‘rsatadi.

## Sayt katalogiga avtomatik chiqarish

Bot `/add` bilan yangi kino qo‘shganda yoki `/part` bilan qism qo‘shganda, sayt repository’sidagi `movies.json` faylini yangilaydi. `/delete` saytdagi yozuvni ham o‘chiradi. Kanalga post yuborish jarayoni o‘zgarishsiz qoladi. Saytdagi tugma filmni botda olish uchun ochadi; video fayl Telegram `file_id` orqali botda saqlanadi.

### Mahalliy serverda tekshirish

Bot va Live Server bir kompyuterda ishlasa, `.env` faylida `SITE_LOCAL_MOVIES_PATH=../movies.json` va `SITE_LOCAL_POSTERS_PATH=../posters` bo‘lsin. Botni qayta ishga tushiring. Bot bazasidagi mavjud kinolar saytga yoziladi; botni ochiq qoldirsangiz, yangi `/add 123` dan keyin sayt katalogi 15 soniya ichida yangilanadi.

Bot boshqa serverda ishlasa, bu mahalliy yo‘l yordam bermaydi: masofadagi server kompyuteringizdagi faylni o‘zgartira olmaydi. Unda GitHub’ga yozadigan quyidagi ulash sozlamalarini ishlating.

Ulash uchun:

1. Sayt fayllarini (shu jumladan yangilangan bo‘sh `movies.json` faylini) sayt repository’sining `main` branch root papkasiga yuklang. GitHub Pages ham `main` branch va `/` papkadan chiqarilsin.
2. GitHub’da fine-grained access token yarating. Uni faqat sayt repository’siga cheklab, **Contents: Read and write** huquqini bering.
3. Bot ishlayotgan hostdagi environment variables’ga quyidagilarni kiriting:
   - `SITE_GITHUB_REPO` — `egasi/repository` ko‘rinishida.
   - `SITE_GITHUB_TOKEN` — maxfiy token.
   - `SITE_GITHUB_BRANCH` — sayt branch’i, odatda `main`.
   - `SITE_MOVIES_PATH` — `movies.json`.
   - `SITE_POSTERS_PATH` — `posters`.
4. Botni qayta deploy qiling. Keyingi `/add` buyrug‘ida sayt repository’siga commit yoziladi va GitHub Pages yangilanadi.

Tokenni kodga yozmang yoki chatga yubormang. Railway’da bu qiymatlarni service’ning **Variables** bo‘limiga kiriting. Boshqa hostda uning secret/environment variables bo‘limidan foydalaning.

### Render’da sozlash

Render’dagi bot kompyuteringizda ishlamaydi; `SITE_LOCAL_MOVIES_PATH` kabi mahalliy fayl yo‘li Render’da sayt faylingizga olib bormaydi. Avval sayt fayllarini GitHub repository’ga yuklab, GitHub Pages’ni yoqing. Keyin:

1. Bot kodidagi bu o‘zgarishlarni botning GitHub repository’siga yuboring. Render’da **Manual Deploy → Deploy latest commit** ni bosing yoki avtomatik deploy tugashini kuting.
2. Render Dashboard → bot service → **Environment** bo‘limida `SITE_GITHUB_REPO=egasi/repository` qo‘shing.
3. `SITE_GITHUB_TOKEN` ga faqat sayt repository’siga **Contents: Read and write** huquqi berilgan GitHub token kiriting.
4. `SITE_GITHUB_BRANCH=main`, `SITE_MOVIES_PATH=movies.json`, `SITE_POSTERS_PATH=posters` qiymatlarini qo‘shing. Render’da `SITE_LOCAL_MOVIES_PATH` o‘rnatilmagan bo‘lsin.
5. O‘zgarishlarni saqlab, Render service’ni qayta ishga tushiring. Bot ishga tushganda bazasidagi mavjud kinolarni, keyinchalik `/add` bilan qo‘shilgan kinolarni saytga yuboradi.

Bot va sayt fayllarini alohida GitHub repository’larda saqlash qulay. Tokenni `.env` fayliga yozib commit qilmang; faqat Render’ning **Environment** bo‘limiga kiriting.

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
3. Railway dashboard’da bot service’ni tanlab, Variables bo‘limida `BOT_TOKEN`, `CHANNEL_ID`, `CHANNEL_URL`, `ADMIN_IDS` va yuqoridagi `SITE_GITHUB_*` qiymatlarini kiriting. `.env` faylini deploy’ga qo‘shmang.
4. Service’ga volume qo‘shib, mount path’ni `/data` qilib belgilang. Bot kino kodlarini `/data/movies.db` faylida saqlaydi; volume bo‘lmasa deploy paytida baza yo‘qolishi mumkin.
5. Deploy logs’da `Application started` ko‘ringandan keyin bot 24/7 ishlaydi. Laptopni va VS Code’ni ochiq qoldirish shart emas.

## Kino qo‘shish

Admin bot chatini ochib, birinchi qismni caption bilan yuboradi. Kodni admin o‘zi tanlaydi. Kino haqidagi ma’lumotlar ham shu captionda bo‘ladi:

```text
/add 122
Nomi: O‘zbekcha kino nomi
Janr: Sarguzasht
Yil: 2024
Davlat: O‘zbekiston
Tavsif: Qisqacha mazmuni shu yerda.
```

Keyingi qismlarni alohida video qilib yuboring va har birining captioniga `/part 122` deb yozing. Bot ularni yuborilgan tartibda raqamlaydi. Kanalda faqat videodan olingan preview rasmi (yoki preview bo‘lmasa matnli e’lon) va kino ma’lumotlari chiqadi; kino videosi kanalga yuborilmaydi. Kanal posti qism soni o‘zgarganda yangilanadi.

Foydalanuvchi botga `122` yuborsa, avval kino ma’lumotlari, so‘ng barcha qismlar tartib bilan keladi. `/info 122` faqat ma’lumotlarni ko‘rsatadi. Admin `/delete 122` bilan kinoni va uning barcha qismlarini o‘chirishi mumkin. Mavjud kodga `/add` bilan qayta yuborish eski qismlarni almashtiradi; yangi qism qo‘shish uchun doim `/part` ishlating.

## Bot buyruqlari

- `/catalog` — kinolar ro‘yxati va kodlari.
- `/info 122` — kino nomi, janri, yili, davlati, tavsifi va qism soni.
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
- A’zolik tekshiruvi ishlashi uchun `CHANNEL_ID` va `CHANNEL_URL` to‘g‘ri bo‘lishi, bot esa kanalda admin bo‘lishi kerak.
- Bot faqat admin ID’lari `ADMIN_IDS` ichida bo‘lgan foydalanuvchilargina `/add` va `/delete` qila oladi.
- Admin birinchi videoni `/add kod` captioni bilan, davomlarini esa `/part kod` captioni bilan yuboradi. Kino to‘liq bo‘lib, ko‘p qismlarga ajratilgan bo‘lsa, qismlarni kerakli tartibda yuboring.
- Bot Telegram’da mavjud video `file_id` qiymatini saqlaydi, shuning uchun fayl server diskiga yuklanmaydi. Shu `file_id` shu bot tokeni doirasida ishlaydi.
