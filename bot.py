"""Telegram movie-code bot (Render 24/7 polling + health HTTP server)."""

import asyncio
import base64
import json
import logging
import os
import re
import sqlite3
from html import escape
from pathlib import Path
from urllib.parse import quote

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
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
SITE_GITHUB_REPO = os.getenv("SITE_GITHUB_REPO", "").strip()
SITE_GITHUB_TOKEN = os.getenv("SITE_GITHUB_TOKEN", "").strip()
SITE_GITHUB_BRANCH = os.getenv("SITE_GITHUB_BRANCH", "main").strip()
SITE_MOVIES_PATH = os.getenv("SITE_MOVIES_PATH", "movies.json").strip().strip("/")
SITE_POSTERS_PATH = os.getenv("SITE_POSTERS_PATH", "posters").strip().strip("/")
CODE_RE = re.compile(r"^\d{1,20}$")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
BOT_DIR = Path(__file__).resolve().parent
SITE_LOCAL_MOVIES_PATH = os.getenv("SITE_LOCAL_MOVIES_PATH", "").strip()
SITE_LOCAL_POSTERS_PATH = os.getenv("SITE_LOCAL_POSTERS_PATH", "../posters").strip()


def local_site_paths() -> tuple[Path, Path]:
    movies_path = Path(SITE_LOCAL_MOVIES_PATH)
    posters_path = Path(SITE_LOCAL_POSTERS_PATH)
    if not movies_path.is_absolute():
        movies_path = (BOT_DIR / movies_path).resolve()
    if not posters_path.is_absolute():
        posters_path = (BOT_DIR / posters_path).resolve()
    return movies_path, posters_path


def read_local_catalog(path: Path) -> list[dict]:
    if not path.exists():
        return []
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(catalog, list):
        raise ValueError(f"{path} must contain a JSON list")
    return catalog


def write_local_catalog(path: Path, catalog: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def sync_movie_to_local_site(
    code: str, bot_username: str | None, poster_bytes: bytes | None = None
) -> None:
    movies_path, posters_dir = local_site_paths()
    with connect_db() as db:
        row = db.execute(
            "SELECT title, genre, year, country, description FROM movies WHERE code = ?",
            (code,),
        ).fetchone()
        part_count = db.execute(
            "SELECT COUNT(*) FROM movie_parts WHERE code = ?", (code,)
        ).fetchone()[0]

    catalog = read_local_catalog(movies_path)
    previous = next(
        (item for item in catalog if isinstance(item, dict) and str(item.get("code", "")) == code),
        {},
    )
    catalog = [
        item for item in catalog
        if not (isinstance(item, dict) and str(item.get("code", "")) == code)
    ]

    if row:
        title, genre, year, country, description = row
        poster_url = previous.get("poster", "")
        if poster_bytes:
            posters_dir.mkdir(parents=True, exist_ok=True)
            poster_file = posters_dir / f"{code}.jpg"
            poster_file.write_bytes(poster_bytes)
            poster_url = Path(os.path.relpath(poster_file, movies_path.parent)).as_posix()
        catalog.insert(0, {
            "code": code,
            "title": title or f"Kino {code}",
            "year": int(year) if year and year.isdigit() else "",
            "genre": genre or "Kino",
            "rating": previous.get("rating", "—"),
            "duration": f"{part_count} qism" if part_count > 1 else "",
            "poster": poster_url,
            "description": description or (f"Davlat: {country}" if country else ""),
            "watchUrl": f"https://t.me/{bot_username}?start={code}" if bot_username else "",
        })

    write_local_catalog(movies_path, catalog)


def sync_all_movies_to_local_site(bot_username: str | None) -> None:
    movies_path, _ = local_site_paths()
    catalog = read_local_catalog(movies_path)
    with connect_db() as db:
        rows = db.execute(
            "SELECT m.code, m.title, m.genre, m.year, m.country, m.description, "
            "(SELECT COUNT(*) FROM movie_parts p WHERE p.code = m.code) "
            "FROM movies m ORDER BY m.created_at DESC, m.rowid DESC"
        ).fetchall()

    for code, title, genre, year, country, description, part_count in reversed(rows):
        previous = next(
            (item for item in catalog if isinstance(item, dict) and str(item.get("code", "")) == str(code)),
            {},
        )
        catalog = [
            item for item in catalog
            if not (isinstance(item, dict) and str(item.get("code", "")) == str(code))
        ]
        catalog.insert(0, {
            "code": str(code),
            "title": title or f"Kino {code}",
            "year": int(year) if year and year.isdigit() else "",
            "genre": genre or "Kino",
            "rating": previous.get("rating", "—"),
            "duration": f"{part_count} qism" if part_count > 1 else "",
            "poster": previous.get("poster", ""),
            "description": description or (f"Davlat: {country}" if country else ""),
            "watchUrl": f"https://t.me/{bot_username}?start={code}" if bot_username else "",
        })
    write_local_catalog(movies_path, catalog)


async def github_json_request(method: str, url: str, **kwargs):
    """Make one GitHub Contents API request without logging credentials."""
    if not SITE_GITHUB_TOKEN:
        raise RuntimeError("SITE_GITHUB_TOKEN is not configured")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {SITE_GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    headers.update(kwargs.pop("headers", {}))
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, headers=headers, **kwargs) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                detail = payload.get("message", "GitHub API request failed") if isinstance(payload, dict) else "GitHub API request failed"
                raise RuntimeError(f"GitHub API returned {response.status}: {detail}")
            return payload


def github_contents_url(path: str) -> str:
    repo = quote(SITE_GITHUB_REPO, safe="/")
    encoded_path = quote(path, safe="/")
    return f"https://api.github.com/repos/{repo}/contents/{encoded_path}"


async def put_github_file(path: str, content: bytes, message: str) -> None:
    url = github_contents_url(path)
    params = {"ref": SITE_GITHUB_BRANCH}
    sha = None
    try:
        existing = await github_json_request("GET", url, params=params)
        sha = existing.get("sha")
    except RuntimeError as error:
        if "returned 404" not in str(error):
            raise
    body = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": SITE_GITHUB_BRANCH,
    }
    if sha:
        body["sha"] = sha
    await github_json_request("PUT", url, json=body)


async def sync_movie_to_site(
    code: str, bot_username: str | None, poster_bytes: bytes | None = None
) -> bool:
    """Publish bot catalog metadata to the static site repository."""
    if SITE_LOCAL_MOVIES_PATH:
        try:
            sync_movie_to_local_site(code, bot_username, poster_bytes)
            logger.info("Synced movie %s to local website catalog", code)
            return True
        except Exception:
            logger.exception("Could not sync movie %s to local website", code)
            return False
    if not SITE_GITHUB_REPO or not SITE_GITHUB_TOKEN:
        logger.info("Website sync skipped; SITE_GITHUB_REPO or SITE_GITHUB_TOKEN is not set")
        return False
    try:
        with connect_db() as db:
            row = db.execute(
                "SELECT title, genre, year, country, description FROM movies WHERE code = ?",
                (code,),
            ).fetchone()
            part_count = db.execute(
                "SELECT COUNT(*) FROM movie_parts WHERE code = ?", (code,)
            ).fetchone()[0]
        owner, repo_name = SITE_GITHUB_REPO.split("/", 1)
        poster_url = (
            f"https://raw.githubusercontent.com/{owner}/{repo_name}/"
            f"{quote(SITE_GITHUB_BRANCH, safe='')}/{SITE_POSTERS_PATH}/{code}.jpg"
        )
        if poster_bytes and row:
            await put_github_file(
                f"{SITE_POSTERS_PATH}/{code}.jpg",
                poster_bytes,
                f"Add poster for movie {code}",
            )
        catalog_url = github_contents_url(SITE_MOVIES_PATH)
        existing = await github_json_request(
            "GET", catalog_url, params={"ref": SITE_GITHUB_BRANCH}
        )
        catalog = json.loads(base64.b64decode(existing["content"]).decode("utf-8"))
        if not isinstance(catalog, list):
            raise ValueError(f"{SITE_MOVIES_PATH} must contain a JSON list")
        catalog = [item for item in catalog if not (isinstance(item, dict) and str(item.get("code", "")) == code)]
        if row:
            title, genre, year, country, description = row
            previous = next(
                (item for item in json.loads(base64.b64decode(existing["content"]).decode("utf-8"))
                 if isinstance(item, dict) and str(item.get("code", "")) == code),
                {},
            )
            movie = {
                "code": code,
                "title": title or f"Kino {code}",
                "year": int(year) if year and year.isdigit() else "",
                "genre": genre or "Kino",
                "rating": previous.get("rating", "—"),
                "duration": f"{part_count} qism" if part_count > 1 else "",
                "poster": poster_url if poster_bytes else previous.get("poster", ""),
                "description": description or (f"Davlat: {country}" if country else ""),
                "watchUrl": f"https://t.me/{bot_username}?start={code}" if bot_username else "",
            }
            catalog.insert(0, movie)
        await put_github_file(
            SITE_MOVIES_PATH,
            (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            f"Publish movie {code} to site catalog",
        )
        logger.info("Synced movie %s to website catalog", code)
        return True
    except Exception:
        logger.exception("Could not sync movie %s to website", code)
        return False


async def sync_all_movies_to_site(bot_username: str | None) -> None:
    """Refresh existing bot entries in the website catalog on startup."""
    if SITE_LOCAL_MOVIES_PATH:
        try:
            sync_all_movies_to_local_site(bot_username)
            logger.info("Synced existing movies to local website catalog")
        except Exception:
            logger.exception("Could not sync existing bot movies to local website")
        return
    if not SITE_GITHUB_REPO or not SITE_GITHUB_TOKEN:
        return
    try:
        with connect_db() as db:
            rows = db.execute(
                "SELECT m.code, m.title, m.genre, m.year, m.country, m.description, "
                "(SELECT COUNT(*) FROM movie_parts p WHERE p.code = m.code) "
                "FROM movies m ORDER BY m.created_at DESC, m.rowid DESC"
            ).fetchall()
        catalog_url = github_contents_url(SITE_MOVIES_PATH)
        existing = await github_json_request(
            "GET", catalog_url, params={"ref": SITE_GITHUB_BRANCH}
        )
        catalog = json.loads(base64.b64decode(existing["content"]).decode("utf-8"))
        if not isinstance(catalog, list):
            raise ValueError(f"{SITE_MOVIES_PATH} must contain a JSON list")
        bot_codes = {str(row[0]) for row in rows}
        catalog = [
            item for item in catalog
            if not (isinstance(item, dict) and str(item.get("code", "")) in bot_codes)
        ]
        previous_by_code = {
            str(item.get("code", "")): item
            for item in json.loads(base64.b64decode(existing["content"]).decode("utf-8"))
            if isinstance(item, dict)
        }
        for code, title, genre, year, country, description, part_count in rows:
            previous = previous_by_code.get(str(code), {})
            catalog.append({
                "code": str(code),
                "title": title or f"Kino {code}",
                "year": int(year) if year and year.isdigit() else "",
                "genre": genre or "Kino",
                "rating": previous.get("rating", "—"),
                "duration": f"{part_count} qism" if part_count > 1 else "",
                "poster": previous.get("poster", ""),
                "description": description or (f"Davlat: {country}" if country else ""),
                "watchUrl": f"https://t.me/{bot_username}?start={code}" if bot_username else "",
            })
        await put_github_file(
            SITE_MOVIES_PATH,
            (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            "Sync bot movie catalog to website",
        )
        logger.info("Synced %s existing bot movies to website", len(rows))
    except Exception:
        logger.exception("Could not sync existing bot movies to website")


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS movies (
            code TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            title TEXT,
            genre TEXT,
            year TEXT,
            country TEXT,
            description TEXT,
            added_by INTEGER NOT NULL,
            channel_message_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(movies)")}
    migrations = {
        "channel_message_id": "INTEGER",
        "genre": "TEXT",
        "year": "TEXT",
        "country": "TEXT",
        "description": "TEXT",
    }
    for name, column_type in migrations.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE movies ADD COLUMN {name} {column_type}")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS movie_parts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            part_no INTEGER NOT NULL,
            file_id TEXT NOT NULL,
            UNIQUE(code, part_no),
            FOREIGN KEY(code) REFERENCES movies(code) ON DELETE CASCADE
        )"""
    )
    connection.execute(
        "INSERT OR IGNORE INTO movie_parts(code, part_no, file_id) "
        "SELECT code, 1, file_id FROM movies"
    )
    return connection


def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id in ADMIN_IDS)


async def is_channel_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
    if member.status in {"member", "administrator", "creator"}:
        return True
    return member.status == "restricted" and bool(getattr(member, "is_member", False))


def join_keyboard() -> InlineKeyboardMarkup | None:
    buttons = []
    if CHANNEL_URL:
        buttons.append(InlineKeyboardButton("📢 Kanalga qo‘shilish", url=CHANNEL_URL))
    buttons.append(
        InlineKeyboardButton("✅ A’zo bo‘ldim", callback_data="check_channel_membership")
    )
    return InlineKeyboardMarkup([[button] for button in buttons])


async def show_join_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "Botdan foydalanish uchun avval kanalga a’zo bo‘ling, so‘ng «A’zo bo‘ldim» tugmasini bosing."
    query = update.callback_query
    if query:
        await query.answer()
        if query.message:
            await query.edit_message_text(text, reply_markup=join_keyboard())
        return
    if update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=join_keyboard())


async def membership_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not CHANNEL_ID or not update.effective_user:
        return
    query = update.callback_query
    if query and query.data == "check_channel_membership":
        return
    try:
        if await is_channel_member(update.effective_user.id, context):
            return
    except Exception:
        logger.exception("Could not check channel membership for user %s", update.effective_user.id)
        if query:
            await query.answer("A’zolikni tekshirib bo‘lmadi. Birozdan keyin urinib ko‘ring.", show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text(
                "Kanal a’zoligini tekshira olmadim. Birozdan keyin qayta urinib ko‘ring."
            )
        raise ApplicationHandlerStop
    await show_join_prompt(update, context)
    raise ApplicationHandlerStop


async def check_channel_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    try:
        subscribed = await is_channel_member(query.from_user.id, context)
    except Exception:
        logger.exception("Could not verify channel membership for user %s", query.from_user.id)
        await query.answer("Tekshiruvda xatolik bo‘ldi. Qayta urinib ko‘ring.", show_alert=True)
        return

    if subscribed:
        await query.answer("A’zolik tasdiqlandi.")
        if query.message:
            await query.edit_message_text("✅ A’zolik tasdiqlandi. Endi kino kodini yuboring.")
    else:
        await query.answer("Avval kanalga qo‘shiling.", show_alert=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args and CODE_RE.fullmatch(context.args[0]):
        await deliver_code(update.effective_message, context.args[0])
        return
    channel_line = f"\nKino kodlari kanali: {CHANNEL_URL}" if CHANNEL_URL else ""
    await update.effective_message.reply_text(
        "Salom! Kino kodini yuboring, men kinoni topib beraman."
        f"{channel_line}\n\nMasalan: 122\n"
        "Katalog: /catalog\nMa’lumot: /info 122\nNom bo‘yicha qidirish: /search kino nomi\n"
        "Tasodifiy kino: /random"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Buyruqlar:\n"
        "/catalog — kinolar ro‘yxati\n"
        "/info kod — kino haqida ma’lumot\n"
        "/search nom — kino nomidan qidirish\n"
        "/latest — oxirgi qo‘shilgan kinolar\n"
        "/random — tasodifiy kino\n"
        "/id — Telegram ID’ingiz\n"
        "Admin: /stats, /delete 122, /delete_latest\n\n"
        "Yoki kino kodini yuboring — bot videoni jo‘natadi.\n"
        "Admin: birinchi videoni `/add 122` bilan, keyingi qismlarni `/part 122` bilan yuboring.\n"
        "Birinchi video captioniga Nomi, Janr, Yil, Davlat va Tavsif qatorlarini qo‘shishingiz mumkin."
    )
    await update.effective_message.reply_text(text)


def parse_movie_metadata(caption: str) -> dict[str, str]:
    metadata = {
        "title": "",
        "genre": "",
        "year": "",
        "country": "",
        "description": "",
    }
    aliases = {
        "nomi": "title",
        "kino nomi": "title",
        "nom": "title",
        "title": "title",
        "janr": "genre",
        "genre": "genre",
        "yil": "year",
        "year": "year",
        "davlat": "country",
        "mamlakat": "country",
        "country": "country",
        "tavsif": "description",
        "malumot": "description",
        "ma'lumot": "description",
        "ma’lumot": "description",
        "syujet": "description",
        "description": "description",
    }
    description_lines = []
    for line in (caption.splitlines()[1:] if caption else []):
        line = line.strip()
        if not line:
            continue
        label, separator, value = line.partition(":")
        field = aliases.get(label.strip().casefold()) if separator else None
        if field:
            value = value.strip()
            if field == "description":
                description_lines.append(value)
            else:
                metadata[field] = value
        elif not metadata["title"]:
            metadata["title"] = line
        else:
            description_lines.append(line)

    metadata["title"] = metadata["title"][:250]
    metadata["genre"] = metadata["genre"][:120]
    metadata["year"] = metadata["year"][:20]
    metadata["country"] = metadata["country"][:120]
    metadata["description"] = "\n".join(description_lines)[:1200]
    return metadata


def make_channel_caption(code: str, metadata: dict[str, str], part_count: int) -> str:
    title = metadata.get("title") or "Nomsiz kino"
    lines = [f"🎬 <b>{escape(title)}</b>", f"🔢 Kino kodi: <code>{code}</code>"]
    details = []
    if metadata.get("genre"):
        details.append(f"🎭 {escape(metadata['genre'])}")
    if metadata.get("year"):
        details.append(f"📅 {escape(metadata['year'])}")
    if metadata.get("country"):
        details.append(f"🌍 {escape(metadata['country'])}")
    if details:
        lines.append(" · ".join(details))
    lines.append(f"🎞 Qismlar: {part_count}")
    lines.append("\nKinoni olish uchun kodni yuboring yoki tugmani bosing.")
    return "\n".join(lines)


def movie_keyboard(bot_username: str | None, code: str) -> InlineKeyboardMarkup | None:
    if not bot_username:
        return None
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "🎬 Botda olish",
                url=f"https://t.me/{bot_username}?start={code}",
            )
        ]]
    )


def format_movie_info(code: str, title: str | None, genre: str | None,
                      year: str | None, country: str | None,
                      description: str | None, part_count: int) -> str:
    lines = [f"🎬 {title.strip() if title and title.strip() else 'Nomsiz kino'}", f"🔢 Kod: {code}"]
    if genre:
        lines.append(f"🎭 Janr: {genre}")
    if year:
        lines.append(f"📅 Yil: {year}")
    if country:
        lines.append(f"🌍 Davlat: {country}")
    lines.append(f"🎞 Qismlar: {part_count}")
    if description:
        lines.extend(["", f"📝 {description}"])
    return "\n".join(lines)


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


async def movie_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not CODE_RE.fullmatch(context.args[0]):
        await update.effective_message.reply_text("Ishlatish: /info 122")
        return
    code = context.args[0]
    with connect_db() as db:
        row = db.execute(
            "SELECT title, genre, year, country, description FROM movies WHERE code = ?",
            (code,),
        ).fetchone()
        part_count = db.execute(
            "SELECT COUNT(*) FROM movie_parts WHERE code = ?", (code,)
        ).fetchone()[0]
    if not row:
        await update.effective_message.reply_text("Bu kod bo‘yicha kino topilmadi.")
        return
    await update.effective_message.reply_text(
        format_movie_info(code, *row, part_count)
    )


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
        row = db.execute("SELECT code FROM movies ORDER BY RANDOM() LIMIT 1").fetchone()
    if not row:
        await update.effective_message.reply_text("Katalog hozircha bo‘sh.")
        return
    await deliver_code(update.effective_message, row[0])


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
    metadata = parse_movie_metadata(message.caption or "")
    with connect_db() as db:
        previous = db.execute(
            "SELECT channel_message_id FROM movies WHERE code = ?", (code,)
        ).fetchone()
    if CHANNEL_ID and previous and previous[0]:
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=previous[0])
        except Exception:
            logger.exception("Could not replace old channel post for movie %s", code)

    with connect_db() as db:
        db.execute(
            "INSERT INTO movies(code, file_id, title, genre, year, country, description, added_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET file_id=excluded.file_id, "
            "title=excluded.title, genre=excluded.genre, year=excluded.year, "
            "country=excluded.country, description=excluded.description, "
            "added_by=excluded.added_by, "
            "channel_message_id=NULL, created_at=CURRENT_TIMESTAMP",
            (
                code,
                message.video.file_id,
                metadata["title"],
                metadata["genre"],
                metadata["year"],
                metadata["country"],
                metadata["description"],
                update.effective_user.id,
            ),
        )
        db.execute("DELETE FROM movie_parts WHERE code = ?", (code,))
        db.execute(
            "INSERT INTO movie_parts(code, part_no, file_id) VALUES (?, 1, ?)",
            (code, message.video.file_id),
        )

    channel_message_id = None
    poster_bytes = None
    if CHANNEL_ID:
        channel_caption = make_channel_caption(code, metadata, 1)
        reply_markup = movie_keyboard(context.bot.username, code)
        try:
            # Use Telegram's video preview frame as a still photo. Never publish
            # the original video to the catalog channel. Telegram thumbnail
            # file_ids cannot be reused directly, so upload the downloaded
            # preview bytes as a new photo.
            poster = message.video.thumbnail
            if poster:
                try:
                    poster_file = await context.bot.get_file(poster.file_id)
                    poster_bytes = await poster_file.download_as_bytearray()
                    posted_message = await context.bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=InputFile(bytes(poster_bytes), filename=f"movie-{code}.jpg"),
                        caption=channel_caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                    )
                except Exception:
                    logger.exception("Could not upload preview image for code %s; posting text", code)
                    posted_message = await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=channel_caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                    )
            else:
                # Some video messages have no preview image; keep the code
                # visible without accidentally posting the full movie.
                posted_message = await context.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=channel_caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            channel_message_id = posted_message.message_id
            with connect_db() as db:
                db.execute(
                    "UPDATE movies SET channel_message_id = ? WHERE code = ?",
                    (channel_message_id, code),
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

    site_synced = await sync_movie_to_site(
        code, context.bot.username, bytes(poster_bytes) if poster_bytes else None
    )
    site_note = (
        " Sayt katalogiga ham qo‘shildi."
        if site_synced
        else " Saytga qo‘shilmadi: bot serverida SITE_GITHUB_REPO va SITE_GITHUB_TOKEN sozlamalarini kiriting."
    )

    if message.video.thumbnail:
        await message.reply_text(
            f"✅ {metadata['title'] or 'Kino'} (kod: {code}) saqlandi.{site_note} "
            "Bu 1-qism. Davomlarini shu kod bilan /part captionida yuboring."
        )
    else:
        await message.reply_text(
            f"✅ Kino (kod: {code}) saqlandi.{site_note} Telegram bu videoga preview bermadi, "
            "kanalga ma’lumotli matn joylandi. Davomlarini /part bilan yuboring."
        )


async def add_movie_part(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not is_admin(update):
        await message.reply_text("Bu buyruq faqat adminlar uchun.")
        return
    match = re.match(r"^/part(?:@\w+)?\s+(\d{1,20})(?:\s|$)", message.caption or "")
    if not match:
        await message.reply_text("Ishlatish: keyingi videoni captionida /part 122 deb yuboring.")
        return
    if not message.video:
        await message.reply_text("/part kod buyrug‘ini keyingi video captioniga yozing.")
        return

    code = match.group(1)
    with connect_db() as db:
        movie = db.execute(
            "SELECT title, genre, year, country, description, channel_message_id "
            "FROM movies WHERE code = ?",
            (code,),
        ).fetchone()
        if not movie:
            await message.reply_text(
                f"{code} kodi topilmadi. Avval birinchi qismini /add bilan yuboring."
            )
            return
        part_no = db.execute(
            "SELECT COALESCE(MAX(part_no), 0) + 1 FROM movie_parts WHERE code = ?",
            (code,),
        ).fetchone()[0]
        db.execute(
            "INSERT INTO movie_parts(code, part_no, file_id) VALUES (?, ?, ?)",
            (code, part_no, message.video.file_id),
        )
        part_count = db.execute(
            "SELECT COUNT(*) FROM movie_parts WHERE code = ?", (code,)
        ).fetchone()[0]

    title, genre, year, country, description, channel_message_id = movie
    if CHANNEL_ID and channel_message_id:
        caption = make_channel_caption(
            code,
            {
                "title": title or "",
                "genre": genre or "",
                "year": year or "",
                "country": country or "",
            },
            part_count,
        )
        keyboard = movie_keyboard(context.bot.username, code)
        try:
            await context.bot.edit_message_caption(
                chat_id=CHANNEL_ID,
                message_id=channel_message_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception:
            try:
                await context.bot.edit_message_text(
                    chat_id=CHANNEL_ID,
                    message_id=channel_message_id,
                    text=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
            except Exception:
                logger.exception("Could not update catalog count for movie %s", code)

    site_synced = await sync_movie_to_site(code, context.bot.username)

    label = title or f"Kino {code}"
    await message.reply_text(
        f"✅ {label}: {part_no}-qism qo‘shildi. Jami {part_count} ta qism bo‘ldi. "
        + ("Sayt yangilandi." if site_synced else "Sayt yangilanmadi; GitHub sync sozlamalarini tekshiring.")
    )


async def delete_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not is_admin(update):
        await message.reply_text("Bu buyruq faqat adminlar uchun.")
        return
    if len(context.args) != 1 or not CODE_RE.fullmatch(context.args[0]):
        await message.reply_text("Ishlatish: /delete 122")
        return
    result = await remove_movie_record(context.args[0], context)
    await message.reply_text(result)


async def delete_latest_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not is_admin(update):
        await message.reply_text("Bu buyruq faqat admin uchun.")
        return

    with connect_db() as db:
        row = db.execute(
            "SELECT code, title FROM movies "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if not row:
            await message.reply_text("O‘chirish uchun kino topilmadi.")
            return
        code, title = row
    label = title.strip() if title and title.strip() else "Nomsiz kino"
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🗑 Ha, o‘chirish", callback_data=f"delete_latest:{code}"),
            InlineKeyboardButton("Bekor qilish", callback_data="delete_latest:cancel"),
        ]]
    )
    await message.reply_text(
        f"Oxirgi qo‘shilgan kino o‘chirilsinmi?\n{label} (kod: {code})",
        reply_markup=keyboard,
    )


async def remove_movie_record(code: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    with connect_db() as db:
        row = db.execute(
            "SELECT title, channel_message_id FROM movies WHERE code = ?", (code,)
        ).fetchone()
        if not row:
            return "Bu kod bo‘yicha kino topilmadi."
        title, channel_message_id = row
        db.execute("DELETE FROM movies WHERE code = ?", (code,))

    label = title.strip() if title and title.strip() else "Nomsiz kino"
    result = ""
    if CHANNEL_ID and channel_message_id:
        try:
            await context.bot.delete_message(
                chat_id=CHANNEL_ID,
                message_id=channel_message_id,
            )
            result = f"🗑 Kino o‘chirildi: {label} (kod: {code})."
        except Exception:
            logger.exception("Could not delete channel post for movie %s", code)
            result = f"Kino bazadan o‘chirildi: {label} (kod: {code}), lekin kanal postini o‘chira olmadim."
    else:
        result = f"Kino bazadan o‘chirildi: {label} (kod: {code}). Eski kanal postini qo‘lda o‘chiring."
    site_synced = await sync_movie_to_site(code, context.bot.username)
    if not site_synced:
        result += " Sayt katalogi yangilanmadi; GitHub sync sozlamalarini tekshiring."
    return result


async def confirm_delete_latest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(update):
        await query.edit_message_text("Bu amal faqat admin uchun.")
        return
    data = query.data or ""
    if data == "delete_latest:cancel":
        await query.edit_message_text("O‘chirish bekor qilindi.")
        return
    code = data.removeprefix("delete_latest:")
    if not CODE_RE.fullmatch(code):
        await query.edit_message_text("Buyruq noto‘g‘ri.")
        return
    with connect_db() as db:
        latest = db.execute(
            "SELECT code FROM movies ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    if not latest or latest[0] != code:
        await query.edit_message_text(
            "Katalog o‘zgargan ekan. Eng oxirgi kinoni qayta aniqlash uchun /delete_latest yuboring."
        )
        return
    result = await remove_movie_record(code, context)
    await query.edit_message_text(result)


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


async def deliver_code(message, code: str) -> None:
    if not CODE_RE.fullmatch(code):
        await message.reply_text("Kino kodini raqam bilan yuboring. Masalan: 122")
        return
    with connect_db() as db:
        row = db.execute(
            "SELECT file_id, title, genre, year, country, description "
            "FROM movies WHERE code = ?",
            (code,),
        ).fetchone()
        parts = db.execute(
            "SELECT part_no, file_id FROM movie_parts WHERE code = ? ORDER BY part_no",
            (code,),
        ).fetchall()
    if not row:
        await message.reply_text("Bu kod bo‘yicha kino topilmadi. Kodni tekshirib qayta yuboring.")
        return
    file_id, title, genre, year, country, description = row
    if not parts:
        parts = [(1, file_id)]
    await message.reply_text(
        format_movie_info(code, title, genre, year, country, description, len(parts))
    )
    for part_no, part_file_id in parts:
        if len(parts) > 1:
            caption = f"{title or 'Kino'} — {part_no}-qism / {len(parts)}"
        else:
            caption = title or f"Kino kodi: {code}"
        await send_movie(message, part_file_id, caption)


async def find_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    await deliver_code(message, (message.text or "").strip())


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled bot error", exc_info=context.error)


async def health_check(request: web.Request) -> web.Response:
    return web.Response(text="OK - Movie bot is running")


async def run_bot_and_server() -> None:
    if not TOKEN:
        raise SystemExit("BOT_TOKEN .env faylida ko‘rsatilmagan")
    if not ADMIN_IDS:
        raise SystemExit("ADMIN_IDS .env faylida kamida bitta Telegram user ID bo‘lishi kerak")

    # Create or migrate the SQLite schema before accepting updates.
    with connect_db():
        pass

    web_app = web.Application()
    web_app.router.add_get("/", health_check)
    web_app.router.add_get("/health", health_check)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("HTTP health server started on port %s", PORT)

    tg_app = Application.builder().token(TOKEN).build()
    tg_app.add_handler(TypeHandler(Update, membership_guard), group=-1)
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("help", help_command))
    tg_app.add_handler(CommandHandler("catalog", catalog))
    tg_app.add_handler(CommandHandler("info", movie_info))
    tg_app.add_handler(CommandHandler("search", search_movies))
    tg_app.add_handler(CommandHandler("latest", latest_movies))
    tg_app.add_handler(CommandHandler("random", random_movie))
    tg_app.add_handler(CommandHandler("id", show_user_id))
    tg_app.add_handler(CommandHandler("stats", admin_stats))
    tg_app.add_handler(CommandHandler("delete", delete_movie))
    tg_app.add_handler(CommandHandler("delete_latest", delete_latest_movie))
    tg_app.add_handler(
        CallbackQueryHandler(
            confirm_delete_latest,
            pattern=r"^delete_latest:(?:\d{1,20}|cancel)$",
        )
    )
    tg_app.add_handler(
        CallbackQueryHandler(check_channel_membership, pattern=r"^check_channel_membership$")
    )
    tg_app.add_handler(MessageHandler(filters.VIDEO & filters.CaptionRegex(r"^/add(?:@\w+)?\s+"), add_movie))
    tg_app.add_handler(MessageHandler(filters.VIDEO & filters.CaptionRegex(r"^/part(?:@\w+)?\s+"), add_movie_part))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, find_movie))
    tg_app.add_error_handler(error_handler)

    await tg_app.initialize()
    await sync_all_movies_to_site(tg_app.bot.username)
    await tg_app.bot.set_my_commands(
        [
            BotCommand("start", "Botni boshlash"),
            BotCommand("help", "Yordam va buyruqlar"),
            BotCommand("catalog", "Kinolar katalogi"),
            BotCommand("info", "Kino haqida ma’lumot"),
            BotCommand("search", "Kino nomidan qidirish"),
            BotCommand("latest", "Oxirgi kinolar"),
            BotCommand("random", "Tasodifiy kino"),
            BotCommand("id", "Telegram ID’ingiz"),
            BotCommand("stats", "Admin: statistika"),
            BotCommand("delete", "Admin: kinoni o‘chirish"),
            BotCommand("delete_latest", "Admin: oxirgi kinoni o‘chirish"),
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
