import logging
import asyncio
import random
import os
import threading
import sqlite3
from fastapi import FastAPI
import uvicorn
import warnings

warnings.filterwarnings("ignore")

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from datetime import datetime

# ================= FASTAPI =================
web_app = FastAPI()

@web_app.get("/")
def home():
    return {"status": "bot ishlayapti"}

def run_web():
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(web_app, host="0.0.0.0", port=port, log_level="error")

# ================= LOGGING =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7966505221:AAHEUj82be8yTNnmfKhbpTz9CqiSR75SAx4")
SUPER_ADMIN_ID = int(os.environ.get("SUPER_ADMIN_ID", "8165064673"))
SOURCE_CHANNEL_ID = int(os.environ.get("SOURCE_CHANNEL_ID", "-1002815082886"))

# DB ni persistent joyda saqlash (Render disk yoki lokal)
DB_PATH = os.environ.get("DB_PATH", "/data/bot_database.db")
# Agar /data mavjud bo'lmasa, joriy papkada saqla
if not os.path.exists(os.path.dirname(DB_PATH)):
    DB_PATH = "bot_database.db"

# ==================== GLOBAL STATE ====================
auto_post_running = False
auto_import_running = False

# ==================== DATABASE ====================

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            name TEXT DEFAULT '',
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS movies (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            caption TEXT NOT NULL,
            file_id TEXT NOT NULL UNIQUE,
            file_type TEXT NOT NULL,
            duration INTEGER DEFAULT 0,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            name TEXT DEFAULT '',
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS required_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL,
            channel_link TEXT NOT NULL,
            channel_title TEXT DEFAULT 'Kanal'
        );

        CREATE TABLE IF NOT EXISTS post_channel (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            channel_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS news_channel (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            channel_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS imported_messages (
            message_id INTEGER PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS auto_post_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            is_running INTEGER DEFAULT 0,
            current_index INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)

    # Yangi ustunlarni qo'shish (eski DB uchun)
    migrations = [
        "ALTER TABLE movies ADD COLUMN duration INTEGER DEFAULT 0",
        "ALTER TABLE movies ADD COLUMN file_id_hash TEXT DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS idx_movies_file_id ON movies(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_movies_duration ON movies(duration)",
        "CREATE INDEX IF NOT EXISTS idx_imported_messages ON imported_messages(message_id)",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass

    conn.commit()
    conn.close()
    logger.info(f"✅ DB initialized: {DB_PATH}")

# --- Users ---
def db_add_user(user_id, username, name):
    try:
        conn = get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, name) VALUES (?,?,?)",
            (user_id, username or "", name or "")
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"db_add_user xato: {e}")

def db_get_all_users():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_user_count():
    conn = get_conn()
    c = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return c

# --- Movies ---
def db_add_movie(code, name, caption, file_id, file_type, duration=0):
    try:
        conn = get_conn()
        # file_id takrorlanmasin
        existing = conn.execute("SELECT code FROM movies WHERE file_id=?", (file_id,)).fetchone()
        if existing:
            conn.close()
            return False  # Allaqachon bor
        conn.execute(
            "INSERT OR REPLACE INTO movies (code, name, caption, file_id, file_type, duration) VALUES (?,?,?,?,?,?)",
            (code, name, caption, file_id, file_type or "video", duration)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"db_add_movie xato: {e}")
        return False

def db_get_movie(code):
    conn = get_conn()
    row = conn.execute("SELECT * FROM movies WHERE code=?", (code,)).fetchone()
    conn.close()
    return dict(row) if row else None

def db_movie_exists(code):
    return db_get_movie(code) is not None

def db_file_id_exists(file_id):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM movies WHERE file_id=?", (file_id,)).fetchone()
    conn.close()
    return row is not None

def db_delete_movie(code):
    conn = get_conn()
    conn.execute("DELETE FROM movies WHERE code=?", (code,))
    conn.commit()
    conn.close()

def db_movie_count():
    conn = get_conn()
    c = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    conn.close()
    return c

def db_get_all_movies():
    # Kod tartibida (1, 2, 3...) — message_id bo'yicha
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM movies ORDER BY CAST(code AS INTEGER) ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_get_movies_for_autopost(offset=0, limit=50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM movies WHERE duration >= 600 ORDER BY CAST(code AS INTEGER) ASC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_get_long_movies(min_duration=600):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM movies WHERE duration >= ? ORDER BY CAST(code AS INTEGER) ASC",
        (min_duration,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# --- Admins ---
def db_add_admin(user_id, name=""):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO admins (user_id, name) VALUES (?,?)",
        (user_id, name)
    )
    conn.commit()
    conn.close()

def db_remove_admin(user_id):
    conn = get_conn()
    conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def db_is_admin(user_id):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row is not None

def db_get_all_admins():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM admins").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# --- Required Channels ---
def db_add_required_channel(channel_id, channel_link, channel_title):
    conn = get_conn()
    conn.execute(
        "INSERT INTO required_channels (channel_id, channel_link, channel_title) VALUES (?,?,?)",
        (channel_id, channel_link, channel_title)
    )
    conn.commit()
    conn.close()

def db_remove_required_channel(ch_id):
    conn = get_conn()
    conn.execute("DELETE FROM required_channels WHERE id=?", (ch_id,))
    conn.commit()
    conn.close()

def db_get_required_channels():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM required_channels").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# --- Post Channel ---
def db_set_post_channel(channel_id):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO post_channel (id, channel_id) VALUES (1,?)",
        (channel_id,)
    )
    conn.commit()
    conn.close()

def db_get_post_channel():
    conn = get_conn()
    row = conn.execute("SELECT channel_id FROM post_channel WHERE id=1").fetchone()
    conn.close()
    return row['channel_id'] if row else None

def db_remove_post_channel():
    conn = get_conn()
    conn.execute("DELETE FROM post_channel WHERE id=1")
    conn.commit()
    conn.close()

# --- News Channel ---
def db_set_news_channel(channel_id):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO news_channel (id, channel_id) VALUES (1,?)",
        (channel_id,)
    )
    conn.commit()
    conn.close()

def db_get_news_channel():
    conn = get_conn()
    row = conn.execute("SELECT channel_id FROM news_channel WHERE id=1").fetchone()
    conn.close()
    return row['channel_id'] if row else None

def db_remove_news_channel():
    conn = get_conn()
    conn.execute("DELETE FROM news_channel WHERE id=1")
    conn.commit()
    conn.close()

# --- Imported Messages ---
def db_is_message_imported(message_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM imported_messages WHERE message_id=?", (message_id,)
    ).fetchone()
    conn.close()
    return row is not None

def db_mark_message_imported(message_id):
    try:
        conn = get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO imported_messages (message_id) VALUES (?)",
            (message_id,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"db_mark_message_imported xato: {e}")

def db_get_max_imported_id():
    conn = get_conn()
    row = conn.execute("SELECT MAX(message_id) FROM imported_messages").fetchone()
    conn.close()
    return row[0] or 0

# --- Auto Post State ---
def db_get_auto_post_state():
    conn = get_conn()
    row = conn.execute("SELECT * FROM auto_post_state WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else {'is_running': 0, 'current_index': 0}

def db_set_auto_post_running(is_running, current_index=0):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO auto_post_state (id, is_running, current_index) VALUES (1,?,?)",
        (int(is_running), current_index)
    )
    conn.commit()
    conn.close()

def db_update_auto_post_index(index):
    conn = get_conn()
    conn.execute(
        "UPDATE auto_post_state SET current_index=? WHERE id=1",
        (index,)
    )
    conn.commit()
    conn.close()

# ==================== HELPERS ====================

def is_admin(user_id):
    return user_id == SUPER_ADMIN_ID or db_is_admin(user_id)

def is_super_admin(user_id):
    return user_id == SUPER_ADMIN_ID

def format_duration(seconds):
    if not seconds or seconds == 0:
        return "Noma'lum"
    h = seconds // 3600
    m = (seconds % 3600) // 20
    s = seconds % 20
    if h > 0:
        return f"{h} soat {m} daqiqa"
    elif m > 0:
        return f"{m} daqiqa {s} sekund"
    else:
        return f"{s} sekund"

def generate_movie_caption(name, duration=0, code=""):
    dur_text = format_duration(duration) if duration > 0 else "—"
    caption = (
        f"🎬 <b>{name}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⏱ <b>Davomiyligi:</b> {dur_text}\n"
        f"🌟 <b>Reyting:</b> ⭐⭐⭐⭐⭐\n"
        f"🎭 <b>Janri:</b> Kino\n"
        f"🌐 <b>Til:</b> O'zbek tilida\n"
    )
    if code:
        caption += f"🔑 <b>Kod:</b> <code>{code}</code>\n"
    caption += (
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"📲 <b>Botdan olish uchun:</b> @tarjimakinolarbizdabot"
    )
    return caption

# ==================== YANGILIK ====================

STATIC_NEWS_POOL = [
    {
        "title": "\"DUNE: Part Three\" rasmiylashtirildi",
        "body": "Warner Bros studiyasi \"Dune\" trilogiyasining uchinchi qismini rasman tasdiqladi. Film 2026-yilda ekranlarga chiqishi kutilmoqda.\n\nByudjet: 200 mln dollar.\nRejissyor: Denis Villeneuve.",
        "source": "Variety"
    },
    {
        "title": "\"Avengers: Doomsday\" — yangi treyler chiqdi",
        "body": "Marvel Studios'ning eng kutilgan filmi \"Avengers: Doomsday\" ning rasmiy treyler chiqdi. Kinofilm 2026-yil may oyida premyera qiladi.",
        "source": "Marvel.com"
    },
    {
        "title": "\"Mission Impossible 8\" jahon bo'ylab $400 mln yig'di",
        "body": "Tom Cruise'ning so'nggi filmi \"Mission: Impossible — The Final Reckoning\" bir haftada $400 million kassa yig'di.",
        "source": "Box Office Mojo"
    },
    {
        "title": "Netflix yangi o'zbek kontenti uchun shartnoma imzoladi",
        "body": "Netflix platformasi O'zbekiston kinematografistlari bilan hamkorlik shartnomasi imzoladi. 2025-yildan boshlab bir qancha o'zbek filmlari xalqaro platformaga qo'shiladi.",
        "source": "UzReport"
    },
    {
        "title": "\"Interstellar 2\" — Nolan yangi loyiha ustida ishlayapti",
        "body": "Christopher Nolan \"Interstellar\" filmining davomini rejalashtirmoqda degan mish-mishlar tarqaldi.",
        "source": "The Hollywood Reporter"
    },
    {
        "title": "\"Gladiator II\" yilning eng yaxshi filmi deb topildi",
        "body": "Ridley Scott'ning \"Gladiator II\" filmi 2024-yilning eng yaxshi aksiyonli filmi sifatida tan olindi. Jahon kassasi — $700 million.",
        "source": "IMDb"
    },
    {
        "title": "\"Spider-Man 4\" — yangi aktyor tasdiqlandi",
        "body": "Marvel Studios yangi Spider-Man filmiga bosh aktyor sifatida Tom Holland'ni yana tasdiqladi. Suratga olish 2025-yil kuzida boshlanishi rejalashtirilgan.",
        "source": "Deadline"
    },
    {
        "title": "\"The Batman 2\" — Pattinson yana Batman rolida",
        "body": "Robert Pattinson \"The Batman Part II\" filmida yana Bruce Wayne rolini o'ynaydi. Film 2026-yil oktyabrda ekranlarga chiqishi kutilmoqda.",
        "source": "DC Studios"
    },
]

def get_random_news() -> str:
    news = random.choice(STATIC_NEWS_POOL)
    date_str = datetime.now().strftime("%d.%m.%Y")
    text = (
        f"🎬 <b>{news['title']}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{news['body']}\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"📰 <i>Manba: {news['source']}</i>\n"
        f"📅 <i>{date_str}</i>\n\n"
        f"🔔 Yangi kinolar uchun: @tarjimakinolarbizdabot"
    )
    return text

# ==================== ADMIN PANEL KEYBOARD ====================

def get_admin_panel_keyboard(user_id):
    is_super = is_super_admin(user_id)

    keyboard = [
        [
            InlineKeyboardButton("➕ Kino Qo'shish", callback_data="ap:add_movie"),
            InlineKeyboardButton("🗑 Kino O'chirish", callback_data="ap:delete_movie"),
        ],
    ]

    if is_super:
        keyboard.append([
            InlineKeyboardButton("👑 Admin Qo'shish", callback_data="ap:add_admin"),
            InlineKeyboardButton("🚫 Admin O'chirish", callback_data="ap:remove_admin"),
        ])

    keyboard += [
        [InlineKeyboardButton("📢 Obuna Kanal Qo'shish", callback_data="ap:add_channel")],
        [InlineKeyboardButton("❌ Obuna Kanal O'chirish", callback_data="ap:remove_channel")],
        [
            InlineKeyboardButton("📣 Post Kanal", callback_data="ap:set_post_channel"),
            InlineKeyboardButton("📰 Yangilik Kanal", callback_data="ap:set_news_channel"),
        ],
        [
            InlineKeyboardButton("🗑 Post Kanal O'chi...", callback_data="ap:del_post_channel"),
            InlineKeyboardButton("🗑 Yangilik Kanal O'chi...", callback_data="ap:del_news_channel"),
        ],
        [InlineKeyboardButton("📨 Reklama Yuborish", callback_data="ap:broadcast")],
        [InlineKeyboardButton("📰 Hozir Yangilik Yuborish", callback_data="ap:send_news")],
        [InlineKeyboardButton("🚀 Barcha Kinolarni Joylash (Auto Post)", callback_data="ap:start_autopost")],
        [InlineKeyboardButton("⏹ Auto Postni To'xtatish", callback_data="ap:stop_autopost")],
        [InlineKeyboardButton("📥 Kanal Tarixini Import", callback_data="ap:import_history")],
        [InlineKeyboardButton("📊 Statistika", callback_data="ap:stats")],
    ]

    return InlineKeyboardMarkup(keyboard)

async def send_admin_panel(target, user_id, edit=False):
    movies_count = db_movie_count()
    users_count = db_user_count()
    admins = db_get_all_admins()
    channels = db_get_required_channels()
    post_ch = db_get_post_channel() or "—"
    news_ch = db_get_news_channel() or "—"
    long_movies = len(db_get_long_movies(600))
    status = "✅ Ishlayapti" if auto_post_running else "🔴 To'xtatilgan"
    import_status = "✅ Ishlayapti" if auto_import_running else "⏸ To'xtatilgan"

    text = (
        f"👑 <b>Super Admin Panel</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users_count}</b>\n"
        f"🎬 Jami kinolar: <b>{movies_count}</b>\n"
        f"🔥 10+ daqiqalik: <b>{long_movies}</b>\n"
        f"👮 Adminlar: <b>{len(admins)}</b>\n"
        f"📢 Obuna kanallari: <b>{len(channels)}</b>\n"
        f"📣 Post kanal: <code>{post_ch}</code>\n"
        f"📰 Yangilik kanali: <code>{news_ch}</code>\n"
        f"🤖 Auto post: {status}\n"
        f"📥 Import: {import_status}\n"
        f"📡 Manba kanal: <code>{SOURCE_CHANNEL_ID}</code>"
    )

    keyboard = get_admin_panel_keyboard(user_id)

    try:
        if edit and hasattr(target, 'edit_message_text'):
            await target.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        elif hasattr(target, 'reply_text'):
            await target.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        elif hasattr(target, 'message'):
            await target.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"send_admin_panel xato: {e}")

# ==================== SUBSCRIPTION CHECK ====================

async def check_subscriptions(bot, user_id):
    channels = db_get_required_channels()
    not_subscribed = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch['channel_id'], user_id)
            if member.status in ['left', 'kicked']:
                not_subscribed.append(ch)
        except Exception:
            not_subscribed.append(ch)
    return not_subscribed

async def send_subscription_message(update_or_query, context, not_subscribed, pending_code=None):
    text = (
        "⚠️ <b>Kinoni olish uchun quyidagi kanallarga obuna bo'ling!</b>\n\n"
        "📌 Obuna bo'lgach, <b>✅ Tekshirish</b> tugmasini bosing.\n"
    )
    keyboard = []
    for ch in not_subscribed:
        keyboard.append([InlineKeyboardButton(
            f"📢 {ch['channel_title']}",
            url=ch['channel_link']
        )])
    check_data = f"check_sub:{pending_code}" if pending_code else "check_sub:none"
    keyboard.append([InlineKeyboardButton("✅ Obuna bo'ldim — Tekshirish", callback_data=check_data)])
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if hasattr(update_or_query, 'message') and update_or_query.message:
            await update_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        elif hasattr(update_or_query, 'callback_query') and update_or_query.callback_query:
            await update_or_query.callback_query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"send_subscription_message xato: {e}")

async def send_movie_to_user(bot, chat_id, movie):
    caption = movie['caption']
    file_id = movie['file_id']
    # Faqat video
    try:
        await bot.send_video(chat_id=chat_id, video=file_id, caption=caption, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Kino yuborishda xato: {e}")
        try:
            await bot.send_message(chat_id=chat_id, text=f"❌ Videoni yuborishda xato yuz berdi.\n\nKod: {movie['code']}")
        except Exception:
            pass

# ==================== POST KANALGA ====================

async def post_to_channel(context, code, name, duration=0):
    channel_id = db_get_post_channel()
    if not channel_id:
        return
    try:
        bot_me = await context.bot.get_me()
        bot_username = bot_me.username

        keyboard = [[InlineKeyboardButton(
            f"🎬 {name} — Olish",
            url=f"https://t.me/{bot_username}?start={code}"
        )]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        dur_text = format_duration(duration) if duration > 0 else "—"
        full_text = (
            f"🎬 <b>{name}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"⏱ <b>Davomiyligi:</b> {dur_text}\n"
            f"🌐 <b>Til:</b> O'zbek tilida tarjima\n"
            f"🔑 <b>Kod:</b> <code>{code}</code>\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"👇 Kinoni olish uchun pastdagi tugmani bosing!"
        )

        await context.bot.send_message(
            chat_id=channel_id,
            text=full_text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Kanalga yuborishda xato: {e}")

# ==================== KANAL TARIXI IMPORT ====================
# Muammo: forward qilib o'chirish usuli juda sekin va xatoga moyil
# Yechim: Telegram getUpdates orqali emas, to'g'ridan forward qilib tekshiramiz
# Batch usulda: bir vaqtda 5ta parallel forward

async def import_channel_history(context: ContextTypes.DEFAULT_TYPE, status_msg_id=None, admin_chat_id=None):
    global auto_import_running

    logger.info("📥 Kanal tarixi import boshlandi (faqat 10+ daqiqalik videolar)...")

    imported_count = 0
    skipped_count = 0
    error_count = 0
    duplicate_count = 0

    async def update_status(current, total, force=False):
        if not status_msg_id or not admin_chat_id:
            return
        if not force and current % 100 != 0:
            return
        progress = min(100, int(current / max(total, 1) * 100))
        try:
            await context.bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=status_msg_id,
                text=(
                    f"📥 <b>Import jarayoni: {progress}%</b>\n\n"
                    f"✅ Saqlandi: <b>{imported_count}</b>\n"
                    f"🔄 Takrorlangan: <b>{duplicate_count}</b>\n"
                    f"⏭ O'tkazildi: <b>{skipped_count}</b>\n"
                    f"❌ Xato: <b>{error_count}</b>\n"
                    f"📊 Tekshirildi: {current}/{total}"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass

    try:
        # Max message ID ni aniqlash
        max_id = 1
        try:
            # Test xabar yuborib ID olamiz
            test_msg = await context.bot.send_message(
                chat_id=SOURCE_CHANNEL_ID,
                text="⚙️ Import tekshiruvi..."
            )
            max_id = test_msg.message_id
            await context.bot.delete_message(
                chat_id=SOURCE_CHANNEL_ID,
                message_id=test_msg.message_id
            )
            logger.info(f"Max message ID: {max_id}")
        except Exception as e:
            logger.warning(f"Max ID topishda xato: {e}. Default 10000 ishlatiladi.")
            max_id = 10000

        if status_msg_id and admin_chat_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=status_msg_id,
                    text=(
                        f"📥 <b>Import boshlandi</b>\n\n"
                        f"📊 Jami tekshiriladigan: ~{max_id} xabar\n"
                        f"🎥 Faqat 10+ daqiqalik videolar saqlanadi\n"
                        f"⏳ Kutib turing..."
                    ),
                    parse_mode="HTML"
                )
            except Exception:
                pass

        # Batch usulda import (5 parallel)
        BATCH_SIZE = 5
        DELAY_BETWEEN_REQUESTS = 0.3  # 300ms orasida

        for batch_start in range(1, max_id + 1, BATCH_SIZE):
            if not auto_import_running:
                logger.info("Import to'xtatildi.")
                break

            batch_end = min(batch_start + BATCH_SIZE, max_id + 1)
            tasks = []

            for msg_id in range(batch_start, batch_end):
                if db_is_message_imported(msg_id):
                    skipped_count += 1
                    continue
                tasks.append(process_single_message(context, msg_id, admin_chat_id))

            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        error_count += 1
                    elif result == "imported":
                        imported_count += 1
                    elif result == "duplicate":
                        duplicate_count += 1
                    elif result == "skipped":
                        skipped_count += 1
                    elif result == "error":
                        error_count += 1

            await update_status(batch_start, max_id)
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

    except Exception as e:
        logger.error(f"Import asosiy xatosi: {e}")
        if status_msg_id and admin_chat_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=status_msg_id,
                    text=f"❌ Import xatosi: {e}",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    auto_import_running = False
    logger.info(f"📥 Import yakunlandi: {imported_count} saqlandi, {duplicate_count} takror, {skipped_count} o'tkazildi")

    if status_msg_id and admin_chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=status_msg_id,
                text=(
                    f"✅ <b>Import yakunlandi!</b>\n\n"
                    f"🎬 Saqlandi: <b>{imported_count}</b> ta kino\n"
                    f"🔄 Takrorlangan: <b>{duplicate_count}</b>\n"
                    f"⏭ O'tkazildi: <b>{skipped_count}</b>\n"
                    f"❌ Xato: <b>{error_count}</b>\n"
                    f"📊 Jami kinolar: <b>{db_movie_count()}</b>"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass


async def process_single_message(context, msg_id, admin_chat_id):
    """Bitta xabarni qayta ishlash. Return: 'imported'|'duplicate'|'skipped'|'error'"""
    try:
        fwd = await context.bot.forward_message(
            chat_id=admin_chat_id,
            from_chat_id=SOURCE_CHANNEL_ID,
            message_id=msg_id,
            disable_notification=True
        )

        # Forward qilingan xabarni darhol o'chirish
        try:
            await context.bot.delete_message(
                chat_id=admin_chat_id,
                message_id=fwd.message_id
            )
        except Exception:
            pass

        # Faqat video bilan ishlash
        if not fwd.video:
            db_mark_message_imported(msg_id)
            return "skipped"

        video = fwd.video
        file_id = video.file_id
        duration = video.duration or 0

        # Qisqa videolarni o'tkazib yuborish (10 daqiqadan kam)
        if duration < 600:
            db_mark_message_imported(msg_id)
            return "skipped"

        # Takrorlangan file_id ni tekshirish
        if db_file_id_exists(file_id):
            db_mark_message_imported(msg_id)
            return "duplicate"

        # Kino nomini aniqlash
        caption_text = fwd.caption or ""
        lines = caption_text.strip().split("\n")
        name = lines[0].strip() if lines and lines[0].strip() else f"Kino #{msg_id}"
        # HTML teglarini tozalash
        name = name.replace("<", "&lt;").replace(">", "&gt;")

        code = str(msg_id)
        full_caption = generate_movie_caption(name, duration, code)

        success = db_add_movie(code, name, full_caption, file_id, "video", duration)
        db_mark_message_imported(msg_id)

        if success:
            logger.info(f"✅ Import: [{code}] {name} ({format_duration(duration)})")
            return "imported"
        else:
            return "duplicate"

    except Exception as e:
        err_str = str(e).lower()
        # Mavjud bo'lmagan xabarlar uchun — normal
        if any(x in err_str for x in ["not found", "can't be forwarded", "message_id_invalid"]):
            db_mark_message_imported(msg_id)
            return "skipped"
        logger.debug(f"Xabar {msg_id} xatosi: {e}")
        db_mark_message_imported(msg_id)
        return "error"

# ==================== AUTO-IMPORT (REAL-TIME) ====================

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return

    msg = update.channel_post

    if msg.chat.id != SOURCE_CHANNEL_ID:
        return

    # Faqat video
    if not msg.video:
        return

    message_id = msg.message_id

    if db_is_message_imported(message_id):
        return

    video = msg.video
    file_id = video.file_id
    duration = video.duration or 0

    # Qisqa videolarni o'tkazish
    if duration < 600:
        db_mark_message_imported(message_id)
        return

    # Takrorlangan file_id tekshirish
    if db_file_id_exists(file_id):
        db_mark_message_imported(message_id)
        return

    caption_text = msg.caption or ""
    lines = caption_text.strip().split("\n")
    name = lines[0].strip() if lines and lines[0].strip() else f"Kino #{message_id}"
    name = name.replace("<", "&lt;").replace(">", "&gt;")

    code = str(message_id)
    full_caption = generate_movie_caption(name, duration, code)

    success = db_add_movie(code, name, full_caption, file_id, "video", duration)
    db_mark_message_imported(message_id)

    if success:
        logger.info(f"✅ Real-time import: {name} (kod: {code}, davomiylik: {format_duration(duration)})")
        await post_to_channel(context, code, name, duration=duration)

# ==================== AUTO POST (har 1 daqiqada) ====================

async def auto_post_loop(bot):
    global auto_post_running

    channel_id = db_get_post_channel() or db_get_news_channel()

    if not channel_id:
        logger.warning("Auto post: kanal topilmadi!")
        auto_post_running = False
        return

    logger.info(f"🚀 Auto post boshlandi → {channel_id}")

    try:
        bot_me = await bot.get_me()
        bot_username = bot_me.username
    except Exception as e:
        logger.error(f"Bot ma'lumoti olinmadi: {e}")
        auto_post_running = False
        return

    # Oxirgi joylangan indexni olish
    state = db_get_auto_post_state()
    current_index = state.get('current_index', 0)

    while auto_post_running:
        try:
            movies = db_get_movies_for_autopost(offset=current_index, limit=1)

            if not movies:
                # Oxiriga yetdi, boshidan boshla
                current_index = 0
                db_update_auto_post_index(0)
                logger.info("Auto post: boshidan boshlandi")
                await asyncio.sleep(60)
                continue

            movie = movies[0]
            code = movie['code']
            name = movie['name']
            duration = movie.get('duration', 0)
            dur_text = format_duration(duration) if duration > 0 else "—"

            text = (
                f"🎬 <b>{name}</b>\n\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"⏱ <b>Davomiyligi:</b> {dur_text}\n"
                f"🌐 <b>Til:</b> O'zbek tilida\n"
                f"🔑 <b>Kod:</b> <code>{code}</code>\n"
                f"━━━━━━━━━━━━━━━━━\n\n"
                f"👇 Kinoni olish uchun pastdagi tugmani bosing!"
            )

            keyboard = [[InlineKeyboardButton(
                "🎬 Kinoni olish",
                url=f"https://t.me/{bot_username}?start={code}"
            )]]

            await bot.send_message(
                chat_id=channel_id,
                text=text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            current_index += 1
            db_update_auto_post_index(current_index)

        except Exception as e:
            logger.error(f"Auto post loop xato: {e}")

        # Har 1 daqiqada bitta kino
        await asyncio.sleep(60)

# ==================== CMD HANDLERS ====================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_add_user(user.id, user.username or "", user.full_name or "")

    if context.args:
        code = context.args[0].strip()
        movie = db_get_movie(code)
        if movie:
            not_subscribed = await check_subscriptions(context.bot, user.id)
            if not_subscribed:
                await send_subscription_message(update, context, not_subscribed, pending_code=code)
                return
            await send_movie_to_user(context.bot, update.effective_chat.id, movie)
            return
        else:
            await update.message.reply_text("❌ Bunday kino topilmadi.")
            return

    if is_admin(user.id):
        await send_admin_panel(update.message, user.id)
    else:
        await update.message.reply_text(
            "👋 <b>Salom! Kino botiga xush kelibsiz!</b>\n\n"
            "🎬 Kino kodini yuboring va filmni oling!\n\n"
            "📲 Kanal: @tarjimakinolarbizdabot",
            parse_mode="HTML"
        )

async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ruxsat yo'q.")
        return
    await send_admin_panel(update.message, update.effective_user.id)

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ruxsat yo'q.")
        return
    users = db_user_count()
    movies = db_movie_count()
    long_movies = len(db_get_long_movies(600))
    admins = len(db_get_all_admins())
    await update.message.reply_text(
        f"📊 <b>Statistika:</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users}</b>\n"
        f"🎬 Jami kinolar: <b>{movies}</b>\n"
        f"🔥 10+ daqiqalik: <b>{long_movies}</b>\n"
        f"👮 Adminlar: <b>{admins}</b>",
        parse_mode="HTML"
    )

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('pending_action', None)
    context.user_data.pop('pending_data', None)
    context.user_data.pop('pending_step', None)
    await update.message.reply_text("❌ Bekor qilindi.")
    if is_admin(update.effective_user.id):
        await send_admin_panel(update.message, update.effective_user.id)

# ==================== ADMIN PANEL CALLBACK HANDLER ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auto_post_running, auto_import_running

    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    # Subscription check
    if data.startswith("check_sub:"):
        code = data.split(":", 1)[1]
        not_subscribed = await check_subscriptions(context.bot, user_id)
        if not_subscribed:
            await query.message.reply_text("❌ Hali ham barcha kanallarga obuna bo'lmadingiz!")
            return

        if code and code != "none":
            movie = db_get_movie(code)
            if movie:
                await send_movie_to_user(context.bot, query.message.chat_id, movie)
            else:
                await query.message.reply_text("❌ Kino topilmadi.")
        else:
            await query.message.reply_text("✅ Obuna tasdiqlandi! Endi kino kodini yuboring.")
        return

    if not data.startswith("ap:"):
        return

    if not is_admin(user_id):
        await query.answer("❌ Ruxsat yo'q!", show_alert=True)
        return

    action = data[3:]

    # --- STATS ---
    if action == "stats":
        await send_admin_panel(query, user_id, edit=True)
        return

    # --- SEND NEWS ---
    if action == "send_news":
        news_channel = db_get_news_channel() or db_get_post_channel()
        if not news_channel:
            await query.answer("❌ Kanal o'rnatilmagan!", show_alert=True)
            return
        try:
            news_text = get_random_news()
            await context.bot.send_message(
                chat_id=news_channel,
                text=news_text,
                parse_mode="HTML"
            )
            await query.answer("✅ Yangilik yuborildi!", show_alert=True)
        except Exception as e:
            await query.answer(f"❌ Xato: {e}", show_alert=True)
        await send_admin_panel(query, user_id, edit=True)
        return

    # --- START AUTOPOST ---
    if action == "start_autopost":
        if auto_post_running:
            await query.answer("⚠️ Auto post allaqachon ishlayapti!", show_alert=True)
        else:
            channel_id = db_get_post_channel() or db_get_news_channel()
            if not channel_id:
                await query.answer("❌ Avval post kanal o'rnating!", show_alert=True)
                return
            auto_post_running = True
            db_set_auto_post_running(True)
            asyncio.create_task(auto_post_loop(context.bot))
            await query.answer("✅ Auto post yoqildi! Har 1 daqiqada 1 kino joylashadi.", show_alert=True)
        await send_admin_panel(query, user_id, edit=True)
        return

    # --- STOP AUTOPOST ---
    if action == "stop_autopost":
        auto_post_running = False
        db_set_auto_post_running(False)
        await query.answer("🛑 Auto post to'xtatildi!", show_alert=True)
        await send_admin_panel(query, user_id, edit=True)
        return

    # --- DELETE POST CHANNEL ---
    if action == "del_post_channel":
        db_remove_post_channel()
        await query.answer("✅ Post kanal o'chirildi!", show_alert=True)
        await send_admin_panel(query, user_id, edit=True)
        return

    # --- DELETE NEWS CHANNEL ---
    if action == "del_news_channel":
        db_remove_news_channel()
        await query.answer("✅ Yangilik kanali o'chirildi!", show_alert=True)
        await send_admin_panel(query, user_id, edit=True)
        return

    # --- IMPORT HISTORY ---
    if action == "import_history":
        if auto_import_running:
            await query.answer("⚠️ Import allaqachon ishlayapti!", show_alert=True)
            return

        await query.answer("📥 Import boshlandi!", show_alert=True)

        try:
            status_msg = await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "📥 <b>Kanal tarixi import boshlandi...</b>\n\n"
                    "🎥 Faqat 10+ daqiqalik videolar saqlanadi.\n"
                    "🔄 Takrorlangan medialar o'tkazib yuboriladi.\n"
                    "⏳ Bu jarayon bir necha daqiqa davom etishi mumkin..."
                ),
                parse_mode="HTML"
            )
            auto_import_running = True
            asyncio.create_task(
                import_channel_history(context, status_msg.message_id, user_id)
            )
        except Exception as e:
            await query.message.reply_text(f"❌ Import xatosi: {e}")

        await send_admin_panel(query, user_id, edit=True)
        return

    # --- REMOVE CHANNEL (show list) ---
    if action == "remove_channel":
        channels = db_get_required_channels()
        if not channels:
            await query.answer("❌ Hech qanday kanal yo'q!", show_alert=True)
            await send_admin_panel(query, user_id, edit=True)
            return
        keyboard = []
        for ch in channels:
            keyboard.append([InlineKeyboardButton(
                f"❌ {ch['channel_title']} ({ch['channel_id']})",
                callback_data=f"ap:rm_ch:{ch['id']}"
            )])
        keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="ap:stats")])
        try:
            await query.edit_message_text(
                "Qaysi kanalni o'chirish kerak?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:
            pass
        return

    if action.startswith("rm_ch:"):
        ch_id = int(action.split(":")[1])
        db_remove_required_channel(ch_id)
        await query.answer("✅ Kanal o'chirildi!", show_alert=True)
        await send_admin_panel(query, user_id, edit=True)
        return

    # --- Qolgan amallar uchun xabar so'rash ---
    action_prompts = {
        "add_movie": "🎬 Kino kodini kiriting (raqam):\n\n/cancel — bekor qilish",
        "delete_movie": "🗑 O'chirmoqchi bo'lgan kino kodini kiriting:\n\n/cancel — bekor qilish",
        "add_admin": "👑 Yangi admin Telegram ID sini kiriting:\n\n/cancel — bekor qilish",
        "remove_admin": "🚫 O'chirmoqchi bo'lgan admin ID sini kiriting:\n\n/cancel — bekor qilish",
        "add_channel": "📢 Kanal invite linkini kiriting\n(masalan: https://t.me/kanalim):\n\n/cancel — bekor qilish",
        "set_post_channel": "📣 Post kanal IDsini kiriting\n(masalan: -1001234567890):\n\n/cancel — bekor qilish",
        "set_news_channel": "📰 Yangilik kanal IDsini kiriting:\n\n/cancel — bekor qilish",
        "broadcast": "📨 Tarqatmoqchi bo'lgan xabarni kiriting:\n\n/cancel — bekor qilish",
    }

    if action in action_prompts:
        context.user_data['pending_action'] = action
        context.user_data['pending_data'] = {}
        context.user_data['pending_step'] = 0
        try:
            await query.message.reply_text(action_prompts[action])
        except Exception as e:
            logger.error(f"Action prompt xato: {e}")
        return

# ==================== PENDING ACTION HANDLER ====================

async def pending_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    text = update.message.text or ""

    if text.startswith("/"):
        return

    action = context.user_data.get('pending_action')
    if not action:
        await handle_movie_code(update, context)
        return

    step = context.user_data.get('pending_step', 0)
    data = context.user_data.get('pending_data', {})

    # ---- ADD MOVIE ----
    if action == "add_movie":
        if step == 0:
            data['code'] = text.strip()
            context.user_data['pending_data'] = data
            context.user_data['pending_step'] = 1
            await update.message.reply_text("Kino nomini kiriting:")
        elif step == 1:
            data['name'] = text.strip()
            context.user_data['pending_data'] = data
            context.user_data['pending_step'] = 2
            await update.message.reply_text("🎬 Kino videosini yuboring:")
        return

    # ---- DELETE MOVIE ----
    if action == "delete_movie":
        code = text.strip()
        if db_movie_exists(code):
            db_delete_movie(code)
            await update.message.reply_text(f"✅ Kino o'chirildi (kod: {code})")
        else:
            await update.message.reply_text("❌ Bunday kino topilmadi.")
        context.user_data.pop('pending_action', None)
        await send_admin_panel(update.message, user.id)
        return

    # ---- ADD ADMIN ----
    if action == "add_admin":
        if not is_super_admin(user.id):
            await update.message.reply_text("❌ Faqat super admin qo'sha oladi!")
            context.user_data.pop('pending_action', None)
            return
        try:
            uid = int(text.strip())
            db_add_admin(uid)
            await update.message.reply_text(f"✅ Admin qo'shildi: <code>{uid}</code>", parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ Noto'g'ri ID. Raqam kiriting.")
        context.user_data.pop('pending_action', None)
        await send_admin_panel(update.message, user.id)
        return

    # ---- REMOVE ADMIN ----
    if action == "remove_admin":
        if not is_super_admin(user.id):
            await update.message.reply_text("❌ Faqat super admin o'chira oladi!")
            context.user_data.pop('pending_action', None)
            return
        try:
            uid = int(text.strip())
            if uid == SUPER_ADMIN_ID:
                await update.message.reply_text("❌ Super adminni o'chirib bo'lmaydi!")
            else:
                db_remove_admin(uid)
                await update.message.reply_text(f"✅ Admin o'chirildi: <code>{uid}</code>", parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ Noto'g'ri ID.")
        context.user_data.pop('pending_action', None)
        await send_admin_panel(update.message, user.id)
        return

    # ---- ADD CHANNEL ----
    if action == "add_channel":
        if step == 0:
            data['link'] = text.strip()
            context.user_data['pending_data'] = data
            context.user_data['pending_step'] = 1
            await update.message.reply_text("Kanal nomini kiriting (masalan: Film Kanali):")
        elif step == 1:
            link = data['link']
            title = text.strip()
            # Username ni linkdan ajratish
            if "t.me/" in link:
                username = "@" + link.split("t.me/")[-1].split("/")[0].strip("+")
            else:
                username = link
            db_add_required_channel(username, link, title)
            await update.message.reply_text(
                f"✅ Kanal qo'shildi!\n"
                f"📌 Nom: {title}\n"
                f"🔗 Link: {link}\n"
                f"👤 ID: {username}"
            )
            context.user_data.pop('pending_action', None)
            await send_admin_panel(update.message, user.id)
        return

    # ---- SET POST CHANNEL ----
    if action == "set_post_channel":
        channel_id = text.strip()
        db_set_post_channel(channel_id)
        await update.message.reply_text(
            f"✅ Post kanal o'rnatildi: <code>{channel_id}</code>", parse_mode="HTML"
        )
        context.user_data.pop('pending_action', None)
        await send_admin_panel(update.message, user.id)
        return

    # ---- SET NEWS CHANNEL ----
    if action == "set_news_channel":
        channel_id = text.strip()
        db_set_news_channel(channel_id)
        await update.message.reply_text(
            f"✅ Yangilik kanali o'rnatildi: <code>{channel_id}</code>", parse_mode="HTML"
        )
        context.user_data.pop('pending_action', None)
        await send_admin_panel(update.message, user.id)
        return

    # ---- BROADCAST ----
    if action == "broadcast":
        users = db_get_all_users()
        sent = 0
        failed = 0
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'],
                    text=text,
                    parse_mode="HTML"
                )
                sent += 1
                await asyncio.sleep(0.05)  # Flood himoya
            except Exception:
                failed += 1
        await update.message.reply_text(
            f"✅ Reklama yuborildi!\n"
            f"📤 Yuborildi: {sent}\n"
            f"❌ Yuborilmadi: {failed}"
        )
        context.user_data.pop('pending_action', None)
        await send_admin_panel(update.message, user.id)
        return

    # Agar hech biri mos kelmasa
    await handle_movie_code(update, context)


async def pending_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    action = context.user_data.get('pending_action')

    if action != "add_movie":
        return

    step = context.user_data.get('pending_step', 0)
    if step != 2:
        return

    data = context.user_data.get('pending_data', {})
    msg = update.message

    # Faqat video qabul qilinadi
    if not msg.video:
        await update.message.reply_text("❌ Faqat video fayl yuboring!")
        return

    file_id = msg.video.file_id
    duration = msg.video.duration or 0

    code = data.get('code', str(int(datetime.now().timestamp())))
    name = data.get('name', 'Nomsiz kino')
    name = name.replace("<", "&lt;").replace(">", "&gt;")
    caption = generate_movie_caption(name, duration, code)

    # Takrorlangan file_id tekshirish
    if db_file_id_exists(file_id):
        await update.message.reply_text(
            "⚠️ Bu video allaqachon bazada mavjud!\n"
            "Boshqa video yuboring yoki /cancel bosing."
        )
        return

    success = db_add_movie(code, name, caption, file_id, "video", duration)

    if success:
        await update.message.reply_text(
            f"✅ Kino qo'shildi!\n"
            f"🔑 Kod: <code>{code}</code>\n"
            f"🎬 Nom: {name}\n"
            f"⏱ Davomiylik: {format_duration(duration)}",
            parse_mode="HTML"
        )
        await post_to_channel(context, code, name, duration=duration)
    else:
        await update.message.reply_text("❌ Kino qo'shishda xato yuz berdi.")

    context.user_data.pop('pending_action', None)
    context.user_data.pop('pending_data', None)
    context.user_data.pop('pending_step', None)

    await send_admin_panel(update.message, user.id)


async def handle_movie_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    code = update.message.text.strip()

    movie = db_get_movie(code)
    if not movie:
        await update.message.reply_text(
            "❌ Bunday kino topilmadi.\n\n"
            "🎬 Kino kodini to'g'ri yuboring yoki kanal orqali kodni toping.\n"
            "📲 @tarjimakinolarbizdabot"
        )
        return

    not_subscribed = await check_subscriptions(context.bot, user.id)
    if not_subscribed:
        await send_subscription_message(update, context, not_subscribed, pending_code=code)
        return

    await send_movie_to_user(context.bot, update.effective_chat.id, movie)

# ==================== STARTUP ====================

async def on_startup(app: Application):
    global auto_post_running

    logger.info("🤖 Bot ishga tushdi")
    logger.info(f"📁 DB joyi: {DB_PATH}")
    logger.info(f"👑 Super admin: {SUPER_ADMIN_ID}")
    logger.info(f"📡 Manba kanal: {SOURCE_CHANNEL_ID}")

    # Auto post holatini tekshirish
    state = db_get_auto_post_state()
    channel_id = db_get_post_channel() or db_get_news_channel()

    if channel_id and state.get('is_running', 0):
        auto_post_running = True
        db_set_auto_post_running(True)
        logger.info(f"🚀 Auto post qayta yoqildi (index: {state.get('current_index', 0)})")
        asyncio.create_task(auto_post_loop(app.bot))
    else:
        logger.info("ℹ️ Auto post o'chirilgan yoki kanal yo'q")

# ==================== MAIN ====================

def main():
    init_db()

    # Web server alohida thread da
    threading.Thread(target=run_web, daemon=True).start()
    logger.info("🌐 Web server thread boshlandi")

    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Callback handler
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Kanal postlari (real-time import) — faqat kanal xabarlari
    app.add_handler(MessageHandler(
        filters.ChatType.CHANNEL & filters.VIDEO,
        handle_channel_post
    ))

    # Admin: video yuklash (faqat video)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.VIDEO,
        pending_media_handler
    ))

    # Matn xabarlari (faqat private chat)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        pending_message_handler
    ))

    logger.info("▶️ Bot polling boshlandi...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        poll_interval=1.0,
        timeout=30
    )

if __name__ == "__main__":
    main()
