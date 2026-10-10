#!/usr/bin/env python3
"""Telegram File Saver Bot.

Send any file to the bot and it is downloaded and stored on the server that
runs the bot. Works best together with a *local* Telegram Bot API server
(files up to 2 GB). See README.md.
"""
import contextlib
import errno
import functools
import html
import logging
import os
import shutil
import sys
import threading
import time

import telebot
from telebot import apihelper, types

import config
import storage
import sysinfo

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("filesaver")

if not config.BOT_TOKEN:
    sys.exit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

# --- Bot API server ------------------------------------------------------------
if config.USES_LOCAL_API:
    apihelper.API_URL = config.API_URL_BASE + "/bot{0}/{1}"
    apihelper.FILE_URL = config.API_URL_BASE + "/file/bot{0}/{1}"
apihelper.CONNECT_TIMEOUT = 30
apihelper.READ_TIMEOUT = config.REQUEST_TIMEOUT

bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=4)
settings = storage.SettingsStore(config.SETTINGS_FILE)

# Users who pressed "change path" and whose next text message is the new path.
awaiting_path = set()

FILE_TYPES = ["document", "video", "audio", "voice", "photo", "animation", "video_note"]

COMMANDS = [
    ("start", "منوی اصلی"),
    ("path", "مسیر ذخیره‌سازی"),
    ("files", "آخرین فایل‌های ذخیره‌شده"),
    ("disk", "وضعیت دیسک"),
    ("server", "اطلاعات سرور"),
    ("help", "راهنمای کامل"),
    ("id", "شناسه‌ی عددی من"),
    ("cancel", "لغو عملیات جاری"),
]


# --- Access control --------------------------------------------------------------
def deny(obj):
    try:
        if isinstance(obj, types.CallbackQuery):
            bot.answer_callback_query(
                obj.id, "⛔ شما اجازه‌ی استفاده از این ربات را ندارید.", show_alert=True
            )
            return
        user_id = obj.from_user.id if obj.from_user else 0
        bot.reply_to(
            obj,
            "⛔ <b>دسترسی ندارید</b>\n\n"
            "این ربات خصوصی است و روی سرور فایل ذخیره می‌کند.\n"
            f"شناسه‌ی عددی شما: <code>{user_id}</code>\n\n"
            "اگر صاحب ربات هستید، این شناسه را به <code>ALLOWED_USER_IDS</code> "
            "در فایل <code>.env</code> اضافه کنید و ربات را دوباره اجرا کنید.",
        )
    except Exception:  # never let a denial crash a handler
        log.exception("deny() failed")


def authorized(func):
    @functools.wraps(func)
    def wrapper(obj):
        if obj.from_user is None or obj.from_user.id not in config.ALLOWED_USER_IDS:
            deny(obj)
            return None
        return func(obj)

    return wrapper


# --- Small helpers ---------------------------------------------------------------
def clip(text, limit=3900):
    return text if len(text) <= limit else text[: limit - 20] + "\n…(کوتاه شد)"


def safe_edit(chat_id, message_id, text, markup=None):
    try:
        bot.edit_message_text(
            clip(text), chat_id, message_id, parse_mode="HTML", reply_markup=markup
        )
    except Exception as exc:
        if "message is not modified" not in str(exc):
            log.warning("edit_message_text failed: %s", exc)


@contextlib.contextmanager
def ticker(chat_id, message_id, text, interval=7):
    """Show ``text`` and keep updating an elapsed-time counter while a long
    blocking call (getFile on a big file) is running."""
    stop = threading.Event()
    started = time.time()
    safe_edit(chat_id, message_id, text)

    def run():
        while not stop.wait(interval):
            elapsed = int(time.time() - started)
            safe_edit(chat_id, message_id, f"{text}\n\n⏱ {elapsed} ثانیه گذشته…")

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=5)


def human_error(exc):
    text = str(exc)
    if "file is too big" in text.lower():
        return (
            "حجم فایل از حد مجاز سرور Bot API بیشتر است. "
            "برای فایل‌های بالای ۲۰ مگابایت باید از Bot API سرور لوکال (حالت --local) استفاده کنید."
        )
    if isinstance(exc, PermissionError):
        return (
            "دسترسی رد شد. ربات باید با کاربر root اجرا شود، چون پوشه‌ی داده‌ی "
            "Bot API داخل /var/lib/docker است و فقط root به آن دسترسی دارد."
        )
    if isinstance(exc, FileNotFoundError):
        return (
            "فایل دریافت‌شده روی دیسک پیدا نشد. مقدار API_CONTAINER_DIR و "
            "API_HOST_DIR را در فایل .env بررسی کنید (مسیر واقعی volume را با "
            "docker volume inspect telegram-bot-api-data ببینید).\n" + text
        )
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "فضای دیسک تمام شد."
    return text


def path_html(path):
    return f"<code>{html.escape(path)}</code>"


# --- Keyboards -------------------------------------------------------------------
def btn(label, data):
    return types.InlineKeyboardButton(label, callback_data=data)


def main_menu():
    kb = types.InlineKeyboardMarkup()
    kb.row(btn("📂 مسیر ذخیره", "menu:path"), btn("📋 فایل‌ها", "menu:files"))
    kb.row(btn("💾 دیسک", "menu:disk"), btn("🖥 سرور", "menu:server"))
    kb.row(btn("❓ راهنما", "menu:help"))
    return kb


def nav_kb(refresh=None):
    kb = types.InlineKeyboardMarkup()
    row = []
    if refresh:
        row.append(btn("🔄 بروزرسانی", refresh))
    row.append(btn("🏠 منوی اصلی", "menu:home"))
    kb.row(*row)
    return kb


# --- Screens (text + keyboard) ---------------------------------------------------
def screen_home(user):
    path = settings.get_path(user.id)
    text = (
        f"👋 سلام <b>{html.escape(user.first_name or 'دوست من')}</b>!\n\n"
        "من یک ربات <b>ذخیره‌ساز فایل</b> هستم 📥\n"
        "هر فایلی (سند، ویدیو، موزیک، ویس یا عکس) برایم بفرستی، آن را روی "
        "سرور خودم ذخیره می‌کنم و مسیر نهایی را نشانت می‌دهم.\n\n"
        f"📂 مسیر ذخیره‌ی فعلی:\n{path_html(path)}\n"
        f"📦 حداکثر حجم هر فایل: <b>{config.MAX_FILE_MB} MB</b>\n\n"
        "قبل از دریافت هر فایل، فضای خالی دیسک را بررسی می‌کنم تا مطمئن شوم جا دارد.\n"
        "برای شروع یک فایل بفرست یا از منوی زیر استفاده کن 👇"
    )
    return text, main_menu()


def screen_help():
    text = (
        "❓ <b>راهنمای کامل</b>\n\n"
        "<b>این ربات چه کاری می‌کند؟</b>\n"
        "هر فایلی که بفرستید روی سرور ربات ذخیره می‌شود؛ می‌توانید پوشه‌ی مقصد را "
        "خودتان تعیین کنید و وضعیت دیسک و سرور را ببینید.\n\n"
        "<b>مراحل کار</b>\n"
        f"1️⃣ (اختیاری) مسیر ذخیره را تنظیم کنید. پیش‌فرض: <code>{html.escape(config.DEFAULT_SAVE_PATH)}</code>\n"
        "2️⃣ فایل را بفرستید.\n"
        "3️⃣ ربات فضای خالی دیسک را بررسی می‌کند؛ اگر جا نباشد، فایل را دریافت نمی‌کند.\n"
        "4️⃣ فایل دریافت و ذخیره می‌شود و مسیر دقیق آن نمایش داده می‌شود.\n\n"
        "<b>دستورات</b>\n"
        "/start — منوی اصلی\n"
        "/path — نمایش و تغییر مسیر ذخیره (مثلاً <code>/path /root/movies</code>)\n"
        "/files — آخرین فایل‌های ذخیره‌شده در مسیر فعلی\n"
        "/disk — وضعیت همه‌ی دیسک‌ها\n"
        "/server — اطلاعات سرور (سیستم‌عامل، رم، پردازنده، دیسک)\n"
        "/id — شناسه‌ی عددی شما در تلگرام\n"
        "/cancel — لغو عملیات جاری\n\n"
        "<b>نکته‌ها</b>\n"
        "• فایل هم‌نام بازنویسی نمی‌شود؛ یک شماره به نامش اضافه می‌شود.\n"
        "• عکس را به‌صورت «فایل (Document)» بفرستید تا کیفیت اصلی حفظ شود.\n"
        f"• حداکثر حجم هر فایل: {config.MAX_FILE_MB} MB."
    )
    return text, nav_kb()


def screen_path(user_id):
    path = settings.get_path(user_id)
    note = " (پیش‌فرض)" if settings.is_default(user_id) else ""
    if config.SAVE_ROOT:
        shown = config.SAVE_ROOT
        example = "/path movies"
        rule = (
            f"🔒 فقط داخل <code>{html.escape(shown)}</code> می‌توانید پوشه انتخاب کنید. "
            "نوشتن فقط نام پوشه (مثل <code>movies</code>) کافی است؛ اگر وجود نداشته باشد ساخته می‌شود."
        )
    else:
        example = "/path /root/my-folder"
        rule = "مسیر باید مطلق باشد (با / شروع شود). اگر پوشه وجود نداشته باشد ساخته می‌شود."
    text = (
        "📂 <b>مسیر ذخیره‌سازی</b>\n\n"
        f"مسیر فعلی{note}:\n{path_html(path)}\n\n"
        f"مسیر پیش‌فرض:\n{path_html(config.DEFAULT_SAVE_PATH)}\n\n"
        "برای تغییر، دکمه‌ی ✏️ را بزنید یا این دستور را بفرستید:\n"
        f"<code>{example}</code>\n\n{rule}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(btn("✏️ تغییر مسیر", "path:custom"))
    kb.row(btn("♻️ بازگشت به پیش‌فرض", "path:default"))
    kb.row(btn("🏠 منوی اصلی", "menu:home"))
    return text, kb


def screen_files(user_id):
    path = settings.get_path(user_id)
    try:
        files, count, total = storage.recent_files(path)
    except OSError as exc:
        return (
            f"❌ خواندن مسیر ممکن نشد:\n<code>{html.escape(str(exc))}</code>",
            nav_kb(),
        )
    lines = [f"📋 <b>فایل‌های ذخیره‌شده</b>\n{path_html(path)}\n"]
    if not files:
        lines.append("هنوز فایلی در این مسیر ذخیره نشده است.")
    else:
        lines.append(f"📁 {count} فایل • مجموع {storage.human_size(total)}\n")
        for mtime, name, size in files:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
            shown = name if len(name) <= 40 else name[:37] + "…"
            lines.append(
                f"• <code>{html.escape(shown)}</code>\n   {storage.human_size(size)} • {when}"
            )
    return "\n".join(lines), nav_kb("menu:files")


def screen_disk(user_id):
    return sysinfo.disk_report(settings.get_path(user_id)), nav_kb("menu:disk")


def screen_server(user_id):
    return sysinfo.server_report(settings.get_path(user_id)), nav_kb("menu:server")


def build_screen(action, user):
    if action == "menu:home":
        return screen_home(user)
    if action == "menu:help":
        return screen_help()
    if action == "menu:path":
        return screen_path(user.id)
    if action == "menu:files":
        return screen_files(user.id)
    if action == "menu:disk":
        return screen_disk(user.id)
    if action == "menu:server":
        return screen_server(user.id)
    return None


def send_screen(chat_id, screen):
    text, markup = screen
    bot.send_message(chat_id, clip(text), reply_markup=markup)


# --- Commands --------------------------------------------------------------------
@bot.message_handler(commands=["id"])
def cmd_id(message):
    allowed = message.from_user.id in config.ALLOWED_USER_IDS
    bot.reply_to(
        message,
        f"🆔 شناسه‌ی عددی شما: <code>{message.from_user.id}</code>\n"
        f"دسترسی به ربات: {'✅ مجاز' if allowed else '⛔ غیرمجاز'}",
    )


@bot.message_handler(commands=["start"])
@authorized
def cmd_start(message):
    awaiting_path.discard(message.from_user.id)
    send_screen(message.chat.id, screen_home(message.from_user))


@bot.message_handler(commands=["help"])
@authorized
def cmd_help(message):
    awaiting_path.discard(message.from_user.id)
    send_screen(message.chat.id, screen_help())


@bot.message_handler(commands=["cancel"])
@authorized
def cmd_cancel(message):
    awaiting_path.discard(message.from_user.id)
    bot.reply_to(message, "✅ عملیات لغو شد.", reply_markup=main_menu())


@bot.message_handler(commands=["files"])
@authorized
def cmd_files(message):
    awaiting_path.discard(message.from_user.id)
    send_screen(message.chat.id, screen_files(message.from_user.id))


@bot.message_handler(commands=["disk"])
@authorized
def cmd_disk(message):
    awaiting_path.discard(message.from_user.id)
    send_screen(message.chat.id, screen_disk(message.from_user.id))


@bot.message_handler(commands=["server"])
@authorized
def cmd_server(message):
    awaiting_path.discard(message.from_user.id)
    send_screen(message.chat.id, screen_server(message.from_user.id))


@bot.message_handler(commands=["path"])
@authorized
def cmd_path(message):
    user_id = message.from_user.id
    awaiting_path.discard(user_id)
    arg = message.text.partition(" ")[2].strip()
    if arg:
        apply_new_path(message, arg)
    else:
        send_screen(message.chat.id, screen_path(user_id))


def apply_new_path(message, raw):
    """Validate + store a new save path. Returns True on success."""
    path, error = storage.validate_save_path(raw)
    if error:
        bot.reply_to(
            message, f"❌ {html.escape(error)}\n\nدوباره امتحان کنید یا /cancel را بفرستید."
        )
        return False
    settings.set_path(message.from_user.id, path)
    free = shutil.disk_usage(path).free
    bot.reply_to(
        message,
        "✅ مسیر ذخیره تغییر کرد:\n"
        f"{path_html(path)}\n\n"
        f"💾 فضای آزاد این دیسک: <b>{storage.human_size(free)}</b>",
        reply_markup=nav_kb(),
    )
    return True


# --- Buttons ---------------------------------------------------------------------
@bot.callback_query_handler(func=lambda call: True)
@authorized
def on_callback(call):
    user = call.from_user
    chat_id, message_id = call.message.chat.id, call.message.message_id
    data = call.data or ""
    awaiting_path.discard(user.id)

    if data == "path:custom":
        awaiting_path.add(user.id)
        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            "✏️ مسیر جدید را بفرستید (مثال: "
            f"<code>{'movies' if config.SAVE_ROOT else '/root/movies'}</code>)\n"
            "برای لغو: /cancel",
        )
        return

    notice = None
    if data == "path:default":
        _, error = storage.validate_save_path(config.DEFAULT_SAVE_PATH)
        if error:
            bot.answer_callback_query(call.id, error[:190], show_alert=True)
            return
        settings.reset_path(user.id)
        notice = "✅ به مسیر پیش‌فرض برگشت"
        data = "menu:path"

    bot.answer_callback_query(call.id, notice)
    screen = build_screen(data, user)
    if screen:
        safe_edit(chat_id, message_id, screen[0], screen[1])


# Plain text that arrives while we wait for a new path (registered after the
# command handlers, so /start, /cancel ... still work).
@bot.message_handler(
    content_types=["text"], func=lambda m: m.from_user.id in awaiting_path
)
@authorized
def on_path_text(message):
    awaiting_path.discard(message.from_user.id)
    if not apply_new_path(message, message.text):
        awaiting_path.add(message.from_user.id)  # let the user retry


# --- Receiving files -------------------------------------------------------------
def extract_file(message):
    """Return ``(file_id, safe_name, size)`` for any supported media message."""
    kind = message.content_type
    if kind == "photo":
        media = message.photo[-1]  # biggest size
        raw_name, default = None, f"photo_{media.file_unique_id}.jpg"
    else:
        media = getattr(message, kind)
        ext = {
            "video": ".mp4", "audio": ".mp3", "voice": ".ogg",
            "animation": ".mp4", "video_note": ".mp4",
        }.get(kind, "")
        raw_name = getattr(media, "file_name", None)
        default = f"{kind}_{media.file_unique_id}{ext}"
    name = storage.safe_filename(raw_name, default)
    return media.file_id, name, getattr(media, "file_size", None)


def place_file(file_path, dest):
    """Put the file Telegram gave us at ``dest`` (via a .part file so a failed
    transfer never leaves a half-written file under the final name)."""
    tmp = dest + ".part"
    try:
        if os.path.isabs(file_path):
            # Local Bot API server (--local): the file already sits on disk.
            src = file_path
            cdir, hdir = config.API_CONTAINER_DIR, config.API_HOST_DIR
            if cdir and hdir and (src == cdir or src.startswith(cdir + "/")):
                src = hdir + src[len(cdir):]
            if not os.path.isfile(src):
                raise FileNotFoundError(src)
            shutil.move(src, tmp)
        else:
            # Cloud / non-local server: download over HTTP (max 20 MB).
            data = bot.download_file(file_path)
            with open(tmp, "wb") as fh:
                fh.write(data)
        os.replace(tmp, dest)
        with contextlib.suppress(OSError, AttributeError):
            os.chmod(dest, 0o644)  # the API server may create files as 0600
            owner = os.stat(os.path.dirname(dest))  # same owner as the save folder
            os.chown(dest, owner.st_uid, owner.st_gid)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def format_space_error(report, name, size):
    def mark(free, needed):
        return " ✅" if free >= needed else " ❌"

    lines = [
        "❌ <b>فضای دیسک کافی نیست — فایل دریافت نشد</b>",
        "",
        f"📄 فایل: <code>{html.escape(name)}</code> ({storage.human_size(size)})",
        f"📏 فضای لازم (با حاشیه‌ی امن {config.MIN_FREE_MB} MB): "
        f"<b>{storage.human_size(report.dest_needed)}</b>",
        f"📂 فضای آزاد مسیر ذخیره: {storage.human_size(report.dest_free)}"
        f"{mark(report.dest_free, report.dest_needed)}",
    ]
    if report.staging_free is not None:
        lines.append(
            "📥 فضای آزاد پوشه‌ی داده‌ی Bot API: "
            f"{storage.human_size(report.staging_free)}"
            f"{mark(report.staging_free, report.staging_needed)}"
        )
    lines += [
        "",
        "فضای دیسک را آزاد کنید (یا دیسک بزرگ‌تری به سرور اضافه کنید). "
        "وضعیت کامل: /disk",
    ]
    return "\n".join(lines)


@bot.message_handler(content_types=FILE_TYPES)
@authorized
def on_file(message):
    chat_id, user_id = message.chat.id, message.from_user.id
    file_id, name, size = extract_file(message)

    status = bot.reply_to(message, "🔎 در حال بررسی فایل و فضای دیسک…")
    sid = status.message_id
    started = time.time()
    label = f"<code>{html.escape(name)}</code> • {storage.human_size(size)}"

    try:
        if size and size > config.MAX_FILE_MB * 1024 * 1024:
            safe_edit(
                chat_id, sid,
                f"❌ حجم فایل ({storage.human_size(size)}) بیشتر از حداکثر مجاز "
                f"({config.MAX_FILE_MB} MB) است.",
                nav_kb(),
            )
            return

        # Re-validate the folder every time (it may have been deleted, or the
        # sandbox rules may have changed since the user picked it).
        save_dir, error = storage.validate_save_path(settings.get_path(user_id))
        if error:
            safe_edit(
                chat_id, sid,
                f"❌ مسیر ذخیره‌ی فعلی معتبر نیست: {html.escape(error)}\n"
                "با /path یک مسیر جدید انتخاب کنید.",
                nav_kb(),
            )
            return

        # 1) Is there room? Check BEFORE asking Telegram for the file.
        report = storage.check_space(save_dir, size)
        if not report.ok:
            safe_edit(chat_id, sid, format_space_error(report, name, size), nav_kb())
            return

        # 2) Download (blocks until the Bot API server has the whole file).
        with ticker(chat_id, sid, f"⬇️ در حال دریافت از تلگرام…\n{label}"):
            info = bot.get_file(file_id)

        # 3) Store (an instant rename when the API data folder and the save
        # folder are on the same mount; a slower copy otherwise).
        dest = storage.unique_path(save_dir, name)
        with ticker(chat_id, sid, f"📦 در حال ذخیره روی سرور…\n{label}"):
            place_file(info.file_path, dest)
    except Exception as exc:
        log.exception("Saving file failed")
        safe_edit(
            chat_id, sid,
            "❌ <b>ذخیره‌سازی ناموفق بود</b>\n\n" + html.escape(human_error(exc)),
            nav_kb(),
        )
        return

    final_size = os.path.getsize(dest)
    lines = [
        "✅ <b>فایل ذخیره شد</b>",
        "",
        f"📄 نام: <code>{html.escape(os.path.basename(dest))}</code>",
        f"📦 حجم: {storage.human_size(final_size)}",
        f"📂 مسیر: {path_html(dest)}",
        f"⏱ مدت: {time.time() - started:.1f} ثانیه",
    ]
    if os.path.basename(dest) != name:
        lines.append("ℹ️ فایلی با همین نام وجود داشت؛ یک شماره به نام اضافه شد.")
    try:
        usage = shutil.disk_usage(save_dir)
        lines.append(
            f"💾 فضای آزاد باقی‌مانده: <b>{storage.human_size(usage.free)}</b> "
            f"({sysinfo.percent_used(usage):.0f}% پر)"
        )
    except OSError:
        pass

    kb = types.InlineKeyboardMarkup()
    kb.row(btn("📋 فایل‌ها", "menu:files"), btn("💾 دیسک", "menu:disk"))
    kb.row(btn("🏠 منوی اصلی", "menu:home"))
    safe_edit(chat_id, sid, "\n".join(lines), kb)


# --- Fallback (must stay last) ---------------------------------------------------
@bot.message_handler(content_types=["text"])
@authorized
def on_other_text(message):
    bot.reply_to(
        message,
        "🤔 متوجه نشدم. یک <b>فایل</b> بفرستید یا از منوی زیر استفاده کنید:",
        reply_markup=main_menu(),
    )


# --- Entry point -----------------------------------------------------------------
def wait_for_api(retries=30, delay=3):
    """The Bot API container may still be starting: retry for a while."""
    for attempt in range(1, retries + 1):
        try:
            return bot.get_me()
        except Exception as exc:
            if getattr(exc, "error_code", None) in (401, 404):
                log.error("Telegram rejected the token (BOT_TOKEN wrong or revoked): %s", exc)
                return None
            log.warning(
                "Bot API not reachable yet (%s/%s) at %s: %s",
                attempt, retries, config.API_URL_BASE or "api.telegram.org", exc,
            )
            time.sleep(delay)
    return None


def main():
    if not config.ALLOWED_USER_IDS:
        log.warning(
            "ALLOWED_USER_IDS is empty - nobody can use the bot yet. "
            "Send /id to the bot to learn your ID and put it in .env"
        )

    me = wait_for_api()
    if me is None:
        log.error("Giving up: could not log in to the Bot API server.")
        sys.exit(1)
    log.info("Started as @%s (API: %s)", me.username, config.API_URL_BASE or "cloud")

    _, error = storage.validate_save_path(config.DEFAULT_SAVE_PATH)
    if error:
        log.error("DEFAULT_SAVE_PATH is not usable: %s", error)

    try:
        bot.set_my_commands([types.BotCommand(c, d) for c, d in COMMANDS])
    except Exception as exc:
        log.warning("Could not set the command menu: %s", exc)

    bot.infinity_polling(timeout=60, long_polling_timeout=60)


if __name__ == "__main__":
    main()
