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
from telegram import Update
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
        f"{channel_line}\n\nMasalan: 122"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Kino olish uchun kodni yuboring.\n\n"
        "Admin uchun: botga video yuboring va captioniga `/add 122` yozing."
    )
    await update.effective_message.reply_text(text)


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
            await context.bot.send_video(
                chat_id=CHANNEL_ID,
                video=message.video.file_id,
                caption=channel_caption,
                parse_mode=ParseMode.HTML,
                supports_streaming=True,
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

    await message.reply_text(f"✅ Kino {code} saqlandi va kanalga e’lon qilindi.")


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
    caption = title[:1024] if title else None
    try:
        await message.reply_video(video=file_id, caption=caption, supports_streaming=True)
    except Exception:
        logger.exception("Could not send movie for code %s", code)
        await message.reply_text("Kinoni yuborishda xatolik yuz berdi. Keyinroq urinib ko‘ring.")


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
    tg_app.add_handler(CommandHandler("delete", delete_movie))
    tg_app.add_handler(MessageHandler(filters.VIDEO & filters.CaptionRegex(r"^/add(?:@\w+)?\s+"), add_movie))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, find_movie))
    tg_app.add_error_handler(error_handler)

    await tg_app.initialize()
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