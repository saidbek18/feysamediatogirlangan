import logging
import asyncio
import sqlite3
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7966505221:AAHEUj82be8yTNnmfKhbpTz9CqiSR75SAx4"
SUPER_ADMIN_ID = 8165064673

# ==================== STATES ====================
(
    MOVIE_CODE, MOVIE_NAME, MOVIE_CAPTION, MOVIE_FILE,
    DELETE_CODE,
    ADD_ADMIN_ID, REMOVE_ADMIN_ID,
    ADD_SUB_CHANNEL_LINK, ADD_SUB_CHANNEL_TITLE,
    SET_POST_CHANNEL,
    ADV_MEDIA, ADV_CAPTION, ADV_FILE, ADV_BTN_NAME, ADV_BTN_URL,
    USER_WAITING,
) = range(16)

# ==================== DATABASE ====================

DB_PATH = "bot_database.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
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
            file_id TEXT NOT NULL,
            file_type TEXT NOT NULL,
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
    """)
    conn.commit()
    conn.close()

# --- Users ---
def db_add_user(user_id, username, name):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username, name) VALUES (?,?,?)",
        (user_id, username, name)
    )
    conn.commit()
    conn.close()

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
def db_add_movie(code, name, caption, file_id, file_type):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO movies (code,name,caption,file_id,file_type) VALUES (?,?,?,?,?)",
        (code, name, caption, file_id, file_type)
    )
    conn.commit()
    conn.close()

def db_get_movie(code):
    conn = get_conn()
    row = conn.execute("SELECT * FROM movies WHERE code=?", (code,)).fetchone()
    conn.close()
    return dict(row) if row else None

def db_movie_exists(code):
    return db_get_movie(code) is not None

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

# ==================== HELPERS ====================

def is_admin(user_id):
    return user_id == SUPER_ADMIN_ID or db_is_admin(user_id)

def is_super_admin(user_id):
    return user_id == SUPER_ADMIN_ID

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
    text = "⚠️ <b>Kinoni olish uchun quyidagi kanallarga obuna bo'ling:</b>\n\n"
    keyboard = []
    for ch in not_subscribed:
        keyboard.append([InlineKeyboardButton(
            f"📢 {ch['channel_title']}",
            url=ch['channel_link']
        )])
    check_data = f"check_sub:{pending_code}" if pending_code else "check_sub:none"
    keyboard.append([InlineKeyboardButton("✅ Obuna bo'ldim", callback_data=check_data)])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if hasattr(update_or_query, 'message') and update_or_query.message:
        await update_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    elif hasattr(update_or_query, 'callback_query') and update_or_query.callback_query:
        await update_or_query.callback_query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

async def send_movie_to_user(bot, chat_id, movie):
    caption = movie['caption']
    file_id = movie['file_id']
    file_type = movie['file_type']
    try:
        if file_type == "video":
            await bot.send_video(chat_id=chat_id, video=file_id, caption=caption, parse_mode="HTML")
        elif file_type == "photo":
            await bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption, parse_mode="HTML")
        elif file_type == "document":
            await bot.send_document(chat_id=chat_id, document=file_id, caption=caption, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Kino yuborishda xato: {e}")

# ==================== POST KANALGA FAQAT MATN ====================

async def post_to_channel(context, code, name, caption, file_id, file_type):
    """Kanalga faqat caption matnini (havola tugmasi bilan) yuboradi — video/fayl emas."""
    channel_id = db_get_post_channel()
    if not channel_id:
        return
    try:
        bot_me = await context.bot.get_me()
        bot_username = bot_me.username
        keyboard = [[InlineKeyboardButton(
            f"🎬 {code} | {name}",
            url=f"https://t.me/{bot_username}?start={code}"
        )]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        full_caption = f"🎬 <b>{name}</b>\n🔑 Kod: <code>{code}</code>\n\n{caption}"

        # Faqat matn yuboriladi — hech qanday media yo'q
        await context.bot.send_message(
            chat_id=channel_id,
            text=full_caption,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Kanalga yuborishda xato: {e}")

# ==================== MENUS ====================

async def show_super_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats_users = db_user_count()
    stats_movies = db_movie_count()
    admins_count = len(db_get_all_admins())
    channels_count = len(db_get_required_channels())
    post_ch = db_get_post_channel()
    post_ch_text = f"<code>{post_ch}</code>" if post_ch else "<i>O'rnatilmagan</i>"

    text = (
        f"👑 <b>Super Admin Panel</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{stats_users}</b>\n"
        f"🎬 Kinolar: <b>{stats_movies}</b>\n"
        f"👮 Adminlar: <b>{admins_count}</b>\n"
        f"📢 Obuna kanallari: <b>{channels_count}</b>\n"
        f"📡 Post kanal: {post_ch_text}"
    )
    keyboard = [
        [
            InlineKeyboardButton("➕ Kino Qo'shish", callback_data="add_movie"),
            InlineKeyboardButton("🗑 Kino O'chirish", callback_data="delete_movie")
        ],
        [
            InlineKeyboardButton("👮 Admin Qo'shish", callback_data="add_admin"),
            InlineKeyboardButton("🚫 Admin O'chirish", callback_data="remove_admin")
        ],
        [InlineKeyboardButton("📢 Obuna Kanal Qo'shish", callback_data="add_sub_channel")],
        [InlineKeyboardButton("❌ Obuna Kanal O'chirish", callback_data="remove_sub_channel")],
        [
            InlineKeyboardButton("📡 Post Kanal O'rnatish", callback_data="set_post_channel"),
            InlineKeyboardButton("🗑 Post Kanal O'chirish", callback_data="del_post_channel")
        ],
        [InlineKeyboardButton("📢 Reklama Yuborish", callback_data="send_adv")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode="HTML", reply_markup=reply_markup
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text, parse_mode="HTML", reply_markup=reply_markup
            )
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🎛 <b>Admin Panel</b>\n\nNimani qilmoqchisiz?"
    keyboard = [
        [InlineKeyboardButton("➕ Kino Qo'shish", callback_data="add_movie")],
        [InlineKeyboardButton("🗑 Kino O'chirish", callback_data="delete_movie")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode="HTML", reply_markup=reply_markup
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text, parse_mode="HTML", reply_markup=reply_markup
            )
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

async def show_user_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎬 <b>Kino Botiga Xush Kelibsiz!</b>\n\n"
        "🔍 Kino kodini yuboring va kinoni oling!\n\n"
        "📌 Misol: <code>1</code> yoki <code>123</code>"
    )
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode="HTML")
        except Exception:
            await update.callback_query.message.reply_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")

# ==================== START ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_add_user(user.id, user.username or "", user.first_name or "")

    if is_super_admin(user.id):
        await show_super_admin_menu(update, context)
        return ConversationHandler.END

    if is_admin(user.id):
        await show_admin_menu(update, context)
        return ConversationHandler.END

    # Deep link (kino kodi)
    if context.args:
        code = context.args[0]
        not_sub = await check_subscriptions(context.bot, user.id)
        if not_sub:
            context.user_data['pending_code'] = code
            await send_subscription_message(update, context, not_sub, pending_code=code)
            return USER_WAITING

        movie = db_get_movie(code)
        if movie:
            await send_movie_to_user(context.bot, user.id, movie)
        else:
            await update.message.reply_text("❌ Bu kodli kino topilmadi.")
        return USER_WAITING

    # Obuna tekshirish
    not_sub = await check_subscriptions(context.bot, user.id)
    if not_sub:
        context.user_data['pending_code'] = None
        await send_subscription_message(update, context, not_sub)
        return USER_WAITING

    await show_user_menu(update, context)
    return USER_WAITING

# ==================== USER HANDLER ====================

async def user_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if is_admin(user.id):
        if is_super_admin(user.id):
            await show_super_admin_menu(update, context)
        else:
            await show_admin_menu(update, context)
        return ConversationHandler.END

    not_sub = await check_subscriptions(context.bot, user.id)
    if not_sub:
        code = update.message.text.strip()
        context.user_data['pending_code'] = code
        await send_subscription_message(update, context, not_sub, pending_code=code)
        return USER_WAITING

    code = update.message.text.strip()
    movie = db_get_movie(code)
    if movie:
        await send_movie_to_user(context.bot, user.id, movie)
    else:
        await update.message.reply_text(
            f"❌ <b>{code}</b> kodli kino topilmadi.\n\nTo'g'ri kod yuboring.",
            parse_mode="HTML"
        )
    return USER_WAITING

# ==================== CALLBACK HANDLER ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    # ===== OBUNA TEKSHIRISH =====
    if data.startswith("check_sub:"):
        code = data.split(":", 1)[1]
        not_sub = await check_subscriptions(context.bot, user_id)
        if not_sub:
            keyboard = []
            for ch in not_sub:
                keyboard.append([InlineKeyboardButton(
                    f"📢 {ch['channel_title']}", url=ch['channel_link']
                )])
            keyboard.append([InlineKeyboardButton("✅ Obuna bo'ldim", callback_data=data)])
            try:
                await query.edit_message_text(
                    "⚠️ <b>Hali ham obuna bo'lmagan kanallar bor:</b>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception:
                pass
            return USER_WAITING

        if code and code != "none":
            movie = db_get_movie(code)
            if movie:
                try:
                    await query.delete_message()
                except Exception:
                    pass
                await send_movie_to_user(context.bot, user_id, movie)
            else:
                await query.edit_message_text("❌ Kino topilmadi.")
        else:
            try:
                await query.delete_message()
            except Exception:
                pass
            await context.bot.send_message(
                user_id,
                "🎬 <b>Kino Botiga Xush Kelibsiz!</b>\n\n"
                "🔍 Kino kodini yuboring va kinoni oling!\n\n"
                "📌 Misol: <code>1</code>",
                parse_mode="HTML"
            )
        return USER_WAITING

    # ===== ADMIN EMAS =====
    if not is_admin(user_id):
        await query.answer("❌ Sizda ruxsat yo'q!", show_alert=True)
        return

    # ===== ADMIN PANEL =====
    if data == "admin_panel":
        if is_super_admin(user_id):
            await show_super_admin_menu(update, context)
        else:
            await show_admin_menu(update, context)
        return ConversationHandler.END

    if data == "add_movie":
        await query.edit_message_text(
            "📝 <b>1-qadam:</b> Kino kodini yuboring:\n\n<i>Misol: 1, 25, movie1</i>",
            parse_mode="HTML"
        )
        return MOVIE_CODE

    if data == "delete_movie":
        await query.edit_message_text(
            "🗑 O'chirmoqchi bo'lgan kino <b>kodini</b> yuboring:",
            parse_mode="HTML"
        )
        return DELETE_CODE

    # ===== FAQAT SUPER ADMIN =====
    if not is_super_admin(user_id):
        await query.answer("❌ Bu funksiya faqat super admin uchun!", show_alert=True)
        return

    if data == "add_admin":
        await query.edit_message_text(
            "👮 Yangi admin <b>Telegram ID</b>sini yuboring:\n\n<i>Misol: 123456789</i>",
            parse_mode="HTML"
        )
        return ADD_ADMIN_ID

    if data == "remove_admin":
        admins = db_get_all_admins()
        if not admins:
            keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]
            await query.edit_message_text(
                "❌ Adminlar ro'yxati bo'sh.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return ConversationHandler.END
        text = "🚫 <b>Adminlar ro'yxati:</b>\n\n"
        for a in admins:
            text += f"• <code>{a['user_id']}</code> — {a['name'] or 'Nomsiz'}\n"
        text += "\nO'chirmoqchi bo'lgan admin <b>ID</b>sini yuboring:"
        await query.edit_message_text(text, parse_mode="HTML")
        return REMOVE_ADMIN_ID

    if data == "add_sub_channel":
        await query.edit_message_text(
            "📢 <b>Obuna kanali qo'shish</b>\n\n"
            "Kanal havolasini yuboring:\n<i>Misol: https://t.me/kanalim</i>",
            parse_mode="HTML"
        )
        return ADD_SUB_CHANNEL_LINK

    if data == "remove_sub_channel":
        channels = db_get_required_channels()
        if not channels:
            keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]
            await query.edit_message_text(
                "❌ Obuna kanallari yo'q.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return ConversationHandler.END
        keyboard = []
        for ch in channels:
            keyboard.append([InlineKeyboardButton(
                f"❌ {ch['channel_title']} ({ch['channel_id']})",
                callback_data=f"del_sub:{ch['id']}"
            )])
        keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")])
        await query.edit_message_text(
            "📢 <b>Qaysi kanalni o'chirmoqchisiz?</b>\n\nBosing:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    if data.startswith("del_sub:"):
        ch_id = int(data.split(":")[1])
        db_remove_required_channel(ch_id)
        keyboard = [[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]
        await query.edit_message_text(
            "✅ Obuna kanali o'chirildi!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    if data == "set_post_channel":
        await query.edit_message_text(
            "📡 <b>Post kanal o'rnatish</b>\n\n"
            "Kanal ID sini yuboring:\n"
            "<i>Misol: -1001234567890</i>\n\n"
            "💡 Kanal ID sini bilish uchun @userinfobot dan foydalaning.",
            parse_mode="HTML"
        )
        return SET_POST_CHANNEL

    if data == "del_post_channel":
        db_remove_post_channel()
        keyboard = [[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]
        await query.edit_message_text(
            "✅ Post kanal o'chirildi!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    # ===== REKLAMA =====
    if data == "send_adv":
        context.user_data.clear()
        context.user_data['adv'] = {}
        keyboard = [[InlineKeyboardButton("⏭ O'tkazib yuborish", callback_data="adv_skip_media")]]
        await query.edit_message_text(
            "📢 <b>Reklama yuborish</b>\n\n"
            "1️⃣ Rasm yoki video yuboring (ixtiyoriy):",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ADV_MEDIA

    if data == "adv_skip_media":
        context.user_data.setdefault('adv', {})['media'] = None
        context.user_data['adv']['media_type'] = None
        await query.edit_message_text(
            "2️⃣ Reklama matnini yuboring:\n<i>Bu majburiy!</i>",
            parse_mode="HTML"
        )
        return ADV_CAPTION

    if data == "adv_skip_file":
        context.user_data.setdefault('adv', {})['file'] = None
        context.user_data['adv']['file_type'] = None
        keyboard = [[InlineKeyboardButton("⏭ Tugmasiz yuborish", callback_data="adv_no_button")]]
        await query.edit_message_text(
            "4️⃣ Tugma nomini yuboring yoki o'tkazib yuboring:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ADV_BTN_NAME

    if data == "adv_no_button":
        context.user_data.setdefault('adv', {})['button_name'] = None
        context.user_data['adv']['button_url'] = None
        await do_send_adv(update, context)
        return ConversationHandler.END

    return ConversationHandler.END

# ==================== KINO QO'SHISH ====================

async def movie_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    code = update.message.text.strip()
    if db_movie_exists(code):
        await update.message.reply_text(
            f"⚠️ <b>{code}</b> kodli kino allaqachon bor!\nBoshqa kod kiriting:",
            parse_mode="HTML"
        )
        return MOVIE_CODE
    context.user_data['m_code'] = code
    await update.message.reply_text(
        f"✅ Kod: <code>{code}</code>\n\n2️⃣ Kino nomini yuboring:",
        parse_mode="HTML"
    )
    return MOVIE_NAME

async def movie_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    name = update.message.text.strip()
    context.user_data['m_name'] = name
    await update.message.reply_text(
        f"✅ Nom: <b>{name}</b>\n\n3️⃣ Caption (tavsif) yuboring:",
        parse_mode="HTML"
    )
    return MOVIE_CAPTION

async def movie_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data['m_caption'] = update.message.text.strip()
    await update.message.reply_text(
        "4️⃣ Kino faylini yuboring:\n<i>Video, rasm yoki hujjat</i>",
        parse_mode="HTML"
    )
    return MOVIE_FILE

async def movie_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    msg = update.message
    code = context.user_data.get('m_code')
    name = context.user_data.get('m_name')
    caption = context.user_data.get('m_caption')

    file_id = file_type = None
    if msg.video:
        file_id, file_type = msg.video.file_id, "video"
    elif msg.photo:
        file_id, file_type = msg.photo[-1].file_id, "photo"
    elif msg.document:
        file_id, file_type = msg.document.file_id, "document"
    else:
        await msg.reply_text("❌ Noto'g'ri fayl. Video, rasm yoki hujjat yuboring.")
        return MOVIE_FILE

    full_caption = f"{caption}\n\n<i>@tarjimakinolarbizdabot</i>"
    db_add_movie(code, name, full_caption, file_id, file_type)

    # Kanalga faqat matn yuboriladi (media emas)
    await post_to_channel(context, code, name, caption, file_id, file_type)

    keyboard = [[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]
    await msg.reply_text(
        f"✅ <b>Kino qo'shildi!</b>\n\n"
        f"🔑 Kod: <code>{code}</code>\n"
        f"📽 Nom: {name}\n"
        f"📁 Tur: {file_type}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data.clear()
    return ConversationHandler.END

# ==================== KINO O'CHIRISH ====================

async def delete_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    code = update.message.text.strip()
    movie = db_get_movie(code)
    keyboard = [[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]
    if not movie:
        await update.message.reply_text(
            f"❌ <b>{code}</b> kodli kino topilmadi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return DELETE_CODE
    db_delete_movie(code)
    await update.message.reply_text(
        f"✅ <b>{code}</b> — <b>{movie['name']}</b> o'chirildi!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

# ==================== ADMIN BOSHQARUV ====================

async def add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]
    try:
        aid = int(update.message.text.strip())
        if aid == SUPER_ADMIN_ID:
            await update.message.reply_text(
                "⚠️ Bu super admin!",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return ConversationHandler.END
        db_add_admin(aid)
        await update.message.reply_text(
            f"✅ <code>{aid}</code> admin qilindi!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Noto'g'ri ID. Raqam kiriting.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    return ConversationHandler.END

async def remove_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]
    try:
        aid = int(update.message.text.strip())
        if not db_is_admin(aid):
            await update.message.reply_text(
                f"❌ <code>{aid}</code> admin emas!",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return ConversationHandler.END
        db_remove_admin(aid)
        await update.message.reply_text(
            f"✅ <code>{aid}</code> admin o'chirildi!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Noto'g'ri ID format.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    return ConversationHandler.END

# ==================== OBUNA KANAL ====================

async def add_sub_channel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return ConversationHandler.END
    link = update.message.text.strip()
    context.user_data['sub_link'] = link
    await update.message.reply_text(
        "📢 Kanal nomini (sarlavhasini) yuboring:\n<i>Misol: Tarjima Kinolar</i>",
        parse_mode="HTML"
    )
    return ADD_SUB_CHANNEL_TITLE

async def add_sub_channel_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return ConversationHandler.END
    title = update.message.text.strip()
    link = context.user_data.get('sub_link', '')
    keyboard = [[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]

    if "t.me/" in link:
        username = "@" + link.split("t.me/")[-1].strip("/")
    else:
        username = link

    db_add_required_channel(username, link, title)
    await update.message.reply_text(
        f"✅ <b>{title}</b> obuna kanali qo'shildi!\n"
        f"🔗 Havola: {link}\n"
        f"🆔 ID: <code>{username}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data.clear()
    return ConversationHandler.END

# ==================== POST KANAL ====================

async def set_post_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return ConversationHandler.END
    channel_id = update.message.text.strip()
    keyboard = [[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]
    db_set_post_channel(channel_id)
    await update.message.reply_text(
        f"✅ Post kanal o'rnatildi!\n📡 ID: <code>{channel_id}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

# ==================== REKLAMA ====================

async def adv_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """1-qadam: Rasm YOKI Video qabul qiladi."""
    context.user_data.setdefault('adv', {})
    msg = update.message
    if msg.photo:
        context.user_data['adv']['media'] = msg.photo[-1].file_id
        context.user_data['adv']['media_type'] = 'photo'
    elif msg.video:
        context.user_data['adv']['media'] = msg.video.file_id
        context.user_data['adv']['media_type'] = 'video'
    else:
        await msg.reply_text(
            "❌ Iltimos rasm yoki video yuboring!\n"
            "Yoki o'tkazib yuborish tugmasini bosing.",
            parse_mode="HTML"
        )
        return ADV_MEDIA

    await msg.reply_text(
        "2️⃣ Reklama matnini yuboring:\n<i>Bu majburiy!</i>",
        parse_mode="HTML"
    )
    return ADV_CAPTION

async def adv_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault('adv', {})['caption'] = update.message.text
    keyboard = [[InlineKeyboardButton("⏭ O'tkazib yuborish", callback_data="adv_skip_file")]]
    await update.message.reply_text(
        "3️⃣ Fayl yoki hujjat yuboring (ixtiyoriy):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADV_FILE

async def adv_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault('adv', {})
    msg = update.message
    if msg.video:
        context.user_data['adv']['file'] = msg.video.file_id
        context.user_data['adv']['file_type'] = 'video'
    elif msg.document:
        context.user_data['adv']['file'] = msg.document.file_id
        context.user_data['adv']['file_type'] = 'document'
    else:
        await msg.reply_text(
            "❌ Iltimos fayl yoki hujjat yuboring!\n"
            "Yoki o'tkazib yuborish tugmasini bosing.",
            parse_mode="HTML"
        )
        return ADV_FILE

    keyboard = [[InlineKeyboardButton("⏭ Tugmasiz yuborish", callback_data="adv_no_button")]]
    await msg.reply_text(
        "4️⃣ Tugma nomini yuboring yoki o'tkazib yuboring:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADV_BTN_NAME

async def adv_btn_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    context.user_data.setdefault('adv', {})['button_name'] = name
    await update.message.reply_text("5️⃣ Tugma havolasini yuboring (URL):")
    return ADV_BTN_URL

async def adv_btn_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault('adv', {})['button_url'] = update.message.text.strip()
    await do_send_adv(update, context)
    return ConversationHandler.END

async def do_send_adv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    adv = context.user_data.get('adv', {})
    users = db_get_all_users()
    caption = adv.get('caption', '')

    # 1-qadam media (rasm/video)
    media_id = adv.get('media')
    media_type = adv.get('media_type')

    # 3-qadam fayl (video/hujjat)
    file_id = adv.get('file')
    file_type = adv.get('file_type')

    btn_name = adv.get('button_name')
    btn_url = adv.get('button_url')

    reply_markup = None
    if btn_name and btn_url:
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(btn_name, url=btn_url)]])

    msg = update.message if update.message else update.callback_query.message
    status = await msg.reply_text(f"📢 Yuborilmoqda... 0/{len(users)}")

    sent = failed = 0
    for i, user in enumerate(users):
        try:
            uid = user['user_id']
            if media_id and media_type == 'photo':
                await context.bot.send_photo(
                    chat_id=uid, photo=media_id, caption=caption,
                    parse_mode="HTML", reply_markup=reply_markup
                )
            elif media_id and media_type == 'video':
                await context.bot.send_video(
                    chat_id=uid, video=media_id, caption=caption,
                    parse_mode="HTML", reply_markup=reply_markup
                )
            elif file_id and file_type == 'video':
                await context.bot.send_video(
                    chat_id=uid, video=file_id, caption=caption,
                    parse_mode="HTML", reply_markup=reply_markup
                )
            elif file_id and file_type == 'document':
                await context.bot.send_document(
                    chat_id=uid, document=file_id, caption=caption,
                    parse_mode="HTML", reply_markup=reply_markup
                )
            else:
                await context.bot.send_message(
                    chat_id=uid, text=caption,
                    parse_mode="HTML", reply_markup=reply_markup
                )
            sent += 1
        except Exception:
            failed += 1

        if (i + 1) % 30 == 0:
            try:
                await status.edit_text(f"📢 Yuborilmoqda... {i+1}/{len(users)}")
            except Exception:
                pass
        await asyncio.sleep(0.05)

    keyboard = [[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]
    await status.edit_text(
        f"✅ <b>Reklama tugadi!</b>\n\n✔️ Yuborildi: {sent}\n❌ Yuborilmadi: {failed}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data.clear()

# ==================== CANCEL ====================

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user_id = update.effective_user.id
    if is_super_admin(user_id):
        await show_super_admin_menu(update, context)
    elif is_admin(user_id):
        await show_admin_menu(update, context)
    else:
        await show_user_menu(update, context)
    return ConversationHandler.END

# ==================== MAIN ====================

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(
                callback_handler,
                pattern="^(add_movie|delete_movie|add_admin|remove_admin|add_sub_channel|set_post_channel|send_adv|adv_skip_media)$"
            ),
        ],
        states={
            USER_WAITING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, user_text_handler),
                CallbackQueryHandler(callback_handler, pattern="^check_sub:"),
            ],
            MOVIE_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, movie_code),
            ],
            MOVIE_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, movie_name),
            ],
            MOVIE_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, movie_caption),
            ],
            MOVIE_FILE: [
                MessageHandler(
                    filters.VIDEO | filters.PHOTO | filters.Document.ALL,
                    movie_file
                ),
            ],
            DELETE_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, delete_code),
            ],
            ADD_ADMIN_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_id),
            ],
            REMOVE_ADMIN_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, remove_admin_id),
            ],
            ADD_SUB_CHANNEL_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_sub_channel_link),
            ],
            ADD_SUB_CHANNEL_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_sub_channel_title),
            ],
            SET_POST_CHANNEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_post_channel),
            ],
            # ===== REKLAMA STATES =====
            ADV_MEDIA: [
                # Rasm yoki video qabul qiladi
                MessageHandler(filters.PHOTO | filters.VIDEO, adv_media),
                CallbackQueryHandler(callback_handler, pattern="^adv_skip_media$"),
            ],
            ADV_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, adv_caption),
            ],
            ADV_FILE: [
                # Fayl yoki hujjat qabul qiladi
                MessageHandler(filters.VIDEO | filters.Document.ALL, adv_file),
                CallbackQueryHandler(callback_handler, pattern="^adv_skip_file$"),
            ],
            ADV_BTN_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, adv_btn_name),
                CallbackQueryHandler(callback_handler, pattern="^adv_no_button$"),
            ],
            ADV_BTN_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, adv_btn_url),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
        ],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(callback_handler))

    print("🎬 Bot muvaffaqiyatli ishga tushdi...")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()