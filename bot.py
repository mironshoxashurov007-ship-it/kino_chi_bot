"""Telegram movie-code bot (Render 24/7 polling + health HTTP server)."""

import asyncio
import logging
import os
import re
import sqlite3
from html import escape
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", "").strip()
ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip().isdigit()
}
DB_PATH = Path(os.getenv("DB_PATH", "movies.db"))
PORT = int(os.getenv("PORT", "10000"))
CODE_RE = re.compile(r"^\d{1,20}$")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS movies (
            code TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            title TEXT,
            added_by INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    return connection


def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id in ADMIN_IDS)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    channel_line = f"\nKino kodlari kanali: {CHANNEL_URL}" if CHANNEL_URL else ""
    await update.effective_message.reply_text(
        "Salom! Kino kodini yuboring, men kinoni topib beraman."
        f"{channel_line}\n\nMasalan: 122\n"
        "Katalog: /catalog\nNom bo‘yicha qidirish: /search kino nomi\n"
        "Tasodifiy kino: /random"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Buyruqlar:\n"
        "/catalog — kinolar ro‘yxati\n"
        "/search nom — kino nomidan qidirish\n"
        "/latest — oxirgi qo‘shilgan kinolar\n"
        "/random — tasodifiy kino\n"
        "/id — Telegram ID’ingiz\n\n"
        "Yoki kino kodini yuboring — bot videoni jo‘natadi.\n"
        "Admin: videoni captionida `/add 122`, keyingi qatorda kino nomi bilan yuboring."
    )
    await update.effective_message.reply_text(text)


async def catalog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with connect_db() as db:
        rows = db.execute(
            "SELECT code, title FROM movies ORDER BY CAST(code AS INTEGER), code LIMIT 25"
        ).fetchall()
    if not rows:
        await update.effective_message.reply_text("Katalog hozircha bo‘sh.")
        return

    lines = ["🎬 Kinolar katalogi:"]
    for code, title in rows:
        label = title.strip() if title and title.strip() else "Nomsiz kino"
        lines.append(f"• {label[:100]} — kod: {code}")
    if len(rows) == 25:
        lines.append("\nYana kinolar bo‘lishi mumkin. Nom bo‘yicha /search ishlating.")
    await update.effective_message.reply_text("\n".join(lines))


async def search_movies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    if not query:
        await update.effective_message.reply_text("Qidiruv: /search kino nomi")
        return

    with connect_db() as db:
        rows = db.execute(
            "SELECT code, title FROM movies WHERE title IS NOT NULL AND title != '' "
            "ORDER BY created_at DESC"
        ).fetchall()
    needle = query.casefold()
    matches = [
        (code, title)
        for code, title in rows
        if needle in title.casefold()
    ][:20]
    if not matches:
        await update.effective_message.reply_text("Bu nomga mos kino topilmadi.")
        return
    lines = [f"🔎 ‘{query[:100]}’ bo‘yicha topildi:"]
    lines.extend(f"• {title[:100]} — kod: {code}" for code, title in matches)
    await update.effective_message.reply_text("\n".join(lines))


async def latest_movies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with connect_db() as db:
        rows = db.execute(
            "SELECT code, title FROM movies ORDER BY created_at DESC, code DESC LIMIT 10"
        ).fetchall()
    if not rows:
        await update.effective_message.reply_text("Hali kino qo‘shilmagan.")
        return
    lines = ["🆕 Oxirgi qo‘shilgan kinolar:"]
    lines.extend(
        f"• {title.strip() if title and title.strip() else 'Nomsiz kino'} — kod: {code}"
        for code, title in rows
    )
    await update.effective_message.reply_text("\n".join(lines))


async def random_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with connect_db() as db:
        row = db.execute(
            "SELECT code, file_id, title FROM movies ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
    if not row:
        await update.effective_message.reply_text("Katalog hozircha bo‘sh.")
        return
    code, file_id, title = row
    caption = f"🎲 Tasodifiy kino: {title}\nKod: {code}" if title else f"🎲 Kino kodi: {code}"
    await send_movie(update.effective_message, file_id, caption)


async def show_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user:
        await update.effective_message.reply_text(f"Telegram ID’ingiz: `{user.id}`", parse_mode=ParseMode.MARKDOWN)


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.effective_message.reply_text("Bu buyruq faqat admin uchun.")
        return
    with connect_db() as db:
        count = db.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
        recent = db.execute(
            "SELECT code, title FROM movies ORDER BY created_at DESC, code DESC LIMIT 1"
        ).fetchone()
    recent_line = (
        f"\nOxirgi kino: {recent[1] or 'Nomsiz kino'} — kod {recent[0]}"
        if recent
        else ""
    )
    await update.effective_message.reply_text(f"📊 Bazada {count} ta kino bor.{recent_line}")


async def add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not is_admin(update):
        await message.reply_text("Bu buyruq faqat adminlar uchun.")
        return
    match = re.match(r"^/add(?:@\w+)?\s+(\d{1,20})(?:\s|$)", message.caption or "")
    if not match:
        await message.reply_text("Ishlatish: /add 122 (kinoni shu xabar bilan birga yuboring).")
        return
    if not message.video:
        await message.reply_text("/add kod buyrug‘ini video xabar captioniga yozing.")
        return

    code = match.group(1)
    title = (
        message.caption.splitlines()[1].strip()[:700]
        if message.caption and len(message.caption.splitlines()) > 1
        else ""
    )
    with connect_db() as db:
        db.execute(
            "INSERT INTO movies(code, file_id, title, added_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET file_id=excluded.file_id, "
            "title=excluded.title, added_by=excluded.added_by, created_at=CURRENT_TIMESTAMP",
            (code, message.video.file_id, title, update.effective_user.id),
        )

    if CHANNEL_ID:
        channel_caption = "🎬"
        if title:
            channel_caption += f" <b>{escape(title)}</b>"
        channel_caption += f"\n🔢 Kino kodi: <code>{code}</code>"
        channel_caption += "\n\nKinoni olish uchun kodni botga yuboring."
        try:
            # Use Telegram's video preview frame as a still photo. Never publish
            # the original video to the catalog channel.
            poster = message.video.thumbnail
            if poster:
                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=poster.file_id,
                    caption=channel_caption,
                    parse_mode=ParseMode.HTML,
                )
            else:
                # Some video messages have no preview image; keep the code
                # visible without accidentally posting the full movie.
                await context.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=channel_caption,
                    parse_mode=ParseMode.HTML,
                )
        except Exception:
            logger.exception("Could not publish code %s to channel", code)
            await message.reply_text(
                f"Kino {code} saqlandi, lekin kanalga e’lon chiqmay qoldi. "
                "Bot kanal admini ekanini va CHANNEL_ID to‘g‘riligini tekshiring."
            )
            return
    else:
        await message.reply_text("Eslatma: CHANNEL_ID sozlanmagan, kanalga e’lon yuborilmadi.")
        return

    if message.video.thumbnail:
        await message.reply_text(f"✅ Kino {code} saqlandi. Kanalga rasmini va kodini joyladim.")
    else:
        await message.reply_text(
            f"✅ Kino {code} saqlandi. Telegram bu videoga preview rasmi bermadi, "
            "shuning uchun kanalga kodli matn joylandi."
        )


async def delete_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not is_admin(update):
        await message.reply_text("Bu buyruq faqat adminlar uchun.")
        return
    if len(context.args) != 1 or not CODE_RE.fullmatch(context.args[0]):
        await message.reply_text("Ishlatish: /delete 122")
        return
    with connect_db() as db:
        cursor = db.execute("DELETE FROM movies WHERE code = ?", (context.args[0],))
    await message.reply_text(
        "Kino o‘chirildi." if cursor.rowcount else "Bu kod bo‘yicha kino topilmadi."
    )


async def send_movie(message, file_id: str, caption: str | None = None) -> None:
    try:
        await message.reply_video(
            video=file_id,
            caption=caption[:1024] if caption else None,
            supports_streaming=True,
        )
    except Exception:
        logger.exception("Could not send a movie")
        await message.reply_text("Kinoni yuborishda xatolik yuz berdi. Keyinroq urinib ko‘ring.")


async def find_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    code = (message.text or "").strip()
    if not CODE_RE.fullmatch(code):
        await message.reply_text("Kino kodini raqam bilan yuboring. Masalan: 122")
        return
    with connect_db() as db:
        row = db.execute("SELECT file_id, title FROM movies WHERE code = ?", (code,)).fetchone()
    if not row:
        await message.reply_text("Bu kod bo‘yicha kino topilmadi. Kodni tekshirib qayta yuboring.")
        return
    file_id, title = row
    await send_movie(message, file_id, title)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled bot error", exc_info=context.error)


async def health_check(request: web.Request) -> web.Response:
    return web.Response(text="OK - Movie bot is running")


async def run_bot_and_server() -> None:
    if not TOKEN:
        raise SystemExit("BOT_TOKEN .env faylida ko‘rsatilmagan")
    if not ADMIN_IDS:
        raise SystemExit("ADMIN_IDS .env faylida kamida bitta Telegram user ID bo‘lishi kerak")

    web_app = web.Application()
    web_app.router.add_get("/", health_check)
    web_app.router.add_get("/health", health_check)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("HTTP health server started on port %s", PORT)

    tg_app = Application.builder().token(TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("help", help_command))
    tg_app.add_handler(CommandHandler("catalog", catalog))
    tg_app.add_handler(CommandHandler("search", search_movies))
    tg_app.add_handler(CommandHandler("latest", latest_movies))
    tg_app.add_handler(CommandHandler("random", random_movie))
    tg_app.add_handler(CommandHandler("id", show_user_id))
    tg_app.add_handler(CommandHandler("stats", admin_stats))
    tg_app.add_handler(CommandHandler("delete", delete_movie))
    tg_app.add_handler(MessageHandler(filters.VIDEO & filters.CaptionRegex(r"^/add(?:@\w+)?\s+"), add_movie))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, find_movie))
    tg_app.add_error_handler(error_handler)

    await tg_app.initialize()
    await tg_app.bot.set_my_commands(
        [
            BotCommand("start", "Botni boshlash"),
            BotCommand("help", "Yordam va buyruqlar"),
            BotCommand("catalog", "Kinolar katalogi"),
            BotCommand("search", "Kino nomidan qidirish"),
            BotCommand("latest", "Oxirgi kinolar"),
            BotCommand("random", "Tasodifiy kino"),
            BotCommand("id", "Telegram ID’ingiz"),
            BotCommand("stats", "Admin: statistika"),
            BotCommand("delete", "Admin: kinoni o‘chirish"),
        ]
    )
    await tg_app.start()
    await tg_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Movie bot is running")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        await runner.cleanup()


def main() -> None:
    asyncio.run(run_bot_and_server())


if __name__ == "__main__":
    main()
