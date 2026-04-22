"""
bot.py – All Telegram handlers, menus, yt-dlp downloader logic
Production-ready | python-telegram-bot v21+
"""

import asyncio
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Optional

import yt_dlp
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_FILE_BYTES = 45 * 1024 * 1024  # 45 MB – safe Telegram bot limit
BOT_USERNAME = os.environ.get("BOT_USERNAME", "YourBot")

URL_RE = re.compile(
    r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+(?:/[^\s]*)?"
)
YOUTUBE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:"
    r"youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/)|"
    r"youtu\.be/"
    r")([\w-]{11})"
)

# In-memory store for URLs (callback_data is limited to 64 bytes)
# Maps short_id → url; stored in bot_data for process-wide sharing
URL_STORE_KEY = "url_store"


# ── Keyboard Builders ─────────────────────────────────────────────────────────

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥  Download Video", callback_data="m:video"),
            InlineKeyboardButton("🎵  Download Audio", callback_data="m:audio"),
        ],
        [
            InlineKeyboardButton("📖  Help", callback_data="m:help"),
            InlineKeyboardButton("📊  Status",  callback_data="m:status"),
        ],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️  Main Menu", callback_data="m:home")]
    ])


def format_choice_kb(short_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥  Video MP4", callback_data=f"dl:v:{short_id}"),
            InlineKeyboardButton("🎵  Audio MP3", callback_data=f"dl:a:{short_id}"),
        ],
        [InlineKeyboardButton("❌  Cancel", callback_data="m:home")],
    ])


# ── URL Store Helpers ─────────────────────────────────────────────────────────

def store_url(bot_data: dict, url: str) -> str:
    """Persist url in bot_data, return a short id (<= 8 chars)."""
    store: dict = bot_data.setdefault(URL_STORE_KEY, {})
    short_id = uuid.uuid4().hex[:8]
    store[short_id] = url
    # Evict oldest entries if store grows large
    if len(store) > 500:
        oldest = list(store.keys())[0]
        del store[oldest]
    return short_id


def resolve_url(bot_data: dict, short_id: str) -> Optional[str]:
    store: dict = bot_data.get(URL_STORE_KEY, {})
    return store.get(short_id)


# ── Progress Helpers ──────────────────────────────────────────────────────────

def build_progress_bar(percent: int, width: int = 10) -> str:
    filled = int(percent / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {percent}%"


async def update_status(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    text: str,
) -> None:
    """Edit a status message, swallowing harmless edit errors."""
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        if "message is not modified" not in str(e).lower():
            logger.debug("edit_message_text: %s", e)


# ── yt-dlp Helpers ────────────────────────────────────────────────────────────

class _ProgressTracker:
    """Thread-safe percent tracker for yt-dlp hooks."""

    def __init__(self):
        self.percent: int = 0

    def hook(self, d: dict) -> None:
        if d["status"] != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
        downloaded = d.get("downloaded_bytes", 0)
        if total:
            self.percent = min(int(downloaded / total * 100), 99)


def _blocking_get_info(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _blocking_download(url: str, out_dir: str, audio_only: bool, tracker: _ProgressTracker) -> None:
    opts: dict = {
        "outtmpl": f"{out_dir}/%(title).60s.%(ext)s",
        "progress_hooks": [tracker.hook],
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_FILE_BYTES,
        "retries": 3,
        "fragment_retries": 3,
    }
    if audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        opts["format"] = (
            "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
            "/bestvideo[height<=720]+bestaudio"
            "/best[height<=720]/best"
        )
        opts["merge_output_format"] = "mp4"

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


async def download_media(
    url: str,
    audio_only: bool,
    on_progress,
) -> Optional[Path]:
    """
    Download media asynchronously (blocking yt-dlp runs in thread pool).
    Calls `on_progress(percent)` every ~2 s.
    Returns the local file path or None on failure.
    """
    loop = asyncio.get_running_loop()
    tmp_dir = tempfile.mkdtemp(prefix="tgbot_")
    tracker = _ProgressTracker()

    download_task = loop.run_in_executor(
        None, _blocking_download, url, tmp_dir, audio_only, tracker
    )

    while not download_task.done():
        await asyncio.sleep(2)
        await on_progress(tracker.percent)

    await download_task  # re-raise any exception

    files = list(Path(tmp_dir).iterdir())
    return files[0] if files else None


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (
        f"👋 Hey <b>{user.first_name}</b>, welcome!\n\n"
        "🤖 I'm your <b>Media Downloader Bot</b>.\n\n"
        "📥 Send me a <b>YouTube link</b> and I'll fetch it as\n"
        "   • an <b>MP4 video</b> (up to 720 p)\n"
        "   • an <b>MP3 audio</b> file (192 kbps)\n\n"
        "Pick an option or just drop a link 👇"
    )
    await update.message.reply_html(text, reply_markup=main_menu_kb())


# ── /help ─────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(
        "📖 <b>Instructions</b>\n\n"
        "1. Paste any YouTube / Shorts URL in chat\n"
        "2. Choose <b>Video</b> or <b>Audio</b>\n"
        "3. Wait for the download ✅\n\n"
        "<b>Limits</b>\n"
        "• Max file size: <code>45 MB</code>\n"
        "• Max resolution: <code>720p</code>\n\n"
        "💡 <i>Tip: Use Audio mode for music or podcasts!</i>",
        reply_markup=back_kb(),
    )


# ── Callback Handler ──────────────────────────────────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data: str = query.data

    # ── Navigation ──
    if data == "m:home":
        await query.edit_message_text(
            "🏠 <b>Main Menu</b>\n\nChoose an option below:",
            reply_markup=main_menu_kb(),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "m:video":
        context.user_data["next_mode"] = "video"
        await query.edit_message_text(
            "📥 <b>Video Download Mode</b>\n\n"
            "Paste a YouTube link and I'll send it as an <b>MP4</b> (≤ 720p).",
            reply_markup=back_kb(),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "m:audio":
        context.user_data["next_mode"] = "audio"
        await query.edit_message_text(
            "🎵 <b>Audio Download Mode</b>\n\n"
            "Paste a YouTube link and I'll extract a <b>192 kbps MP3</b>.",
            reply_markup=back_kb(),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "m:help":
        await query.edit_message_text(
            "📖 <b>Instructions</b>\n\n"
            "1. Paste any YouTube / Shorts URL in chat\n"
            "2. Choose <b>Video</b> or <b>Audio</b>\n"
            "3. Wait for the download ✅\n\n"
            "<b>Limits</b>\n"
            "• Max file size: <code>45 MB</code>\n"
            "• Max resolution: <code>720p</code>",
            reply_markup=back_kb(),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "m:status":
        me = await context.bot.get_me()
        webhook = await context.bot.get_webhook_info()
        await query.edit_message_text(
            "📊 <b>Bot Status</b>\n\n"
            f"🟢 Status:    <b>Online</b>\n"
            f"🤖 Username: <b>@{me.username}</b>\n"
            f"⚡ Mode:      <b>Webhook</b>\n"
            f"📡 Pending:  <b>{webhook.pending_update_count}</b>\n"
            f"🐍 PTB:       <b>v21+</b>\n"
            f"🌐 Host:      <b>Render.com</b>",
            reply_markup=back_kb(),
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Download triggers ──
    if data.startswith("dl:v:") or data.startswith("dl:a:"):
        parts = data.split(":", 2)       # ["dl", "v"/"a", short_id]
        audio_only = parts[1] == "a"
        short_id = parts[2]
        url = resolve_url(context.bot_data, short_id)
        if not url:
            await query.edit_message_text("⚠️ Session expired. Please send the link again.")
            return
        await query.edit_message_text(
            "⏳ <b>Starting download…</b>",
            parse_mode=ParseMode.HTML,
        )
        await _run_download(
            context=context,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            url=url,
            audio_only=audio_only,
        )


# ── Message / URL Handler ─────────────────────────────────────────────────────

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    urls = URL_RE.findall(text)

    if not urls:
        await update.message.reply_html(
            "🤔 No URL found in your message.\n\n"
            "Send me a <b>YouTube link</b> to get started!",
            reply_markup=main_menu_kb(),
        )
        return

    url = urls[0]

    if not YOUTUBE_RE.search(url):
        await update.message.reply_html(
            f"🔗 <b>URL detected:</b> <code>{url}</code>\n\n"
            "⚠️ Only <b>YouTube</b> links are supported right now.\n"
            "Please send a YouTube URL.",
            reply_markup=main_menu_kb(),
        )
        return

    # If user already selected a mode, skip the choice screen
    mode = context.user_data.pop("next_mode", None)
    if mode:
        status_msg = await update.message.reply_html("⏳ <b>Preparing…</b>")
        await _run_download(
            context=context,
            chat_id=update.effective_chat.id,
            message_id=status_msg.message_id,
            url=url,
            audio_only=(mode == "audio"),
        )
        return

    # Fetch video info to show a nice preview
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, _blocking_get_info, url)
        title = (info.get("title") or "YouTube Video")[:60]
        duration = int(info.get("duration") or 0)
        mins, secs = divmod(duration, 60)
        uploader = (info.get("uploader") or "Unknown")[:30]
        preview = (
            f"🎬 <b>{title}</b>\n"
            f"👤 {uploader}\n"
            f"⏱  {mins:02d}:{secs:02d}\n\n"
            "Choose format to download:"
        )
    except Exception:
        preview = "🎬 <b>YouTube Video</b>\n\nChoose format to download:"

    short_id = store_url(context.bot_data, url)
    await update.message.reply_html(preview, reply_markup=format_choice_kb(short_id))


# ── Core Download Worker ──────────────────────────────────────────────────────

async def _run_download(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    url: str,
    audio_only: bool,
) -> None:
    """Download media and send the file back, with live progress updates."""

    label = "🎵 Audio" if audio_only else "📥 Video"
    last_pct: dict = {"val": -15}

    async def on_progress(pct: int) -> None:
        # Throttle edits – only update every 15%
        if pct - last_pct["val"] >= 15:
            last_pct["val"] = pct
            bar = build_progress_bar(pct)
            await update_status(
                context, chat_id, message_id,
                f"{label} <b>Downloading…</b>\n\n{bar}",
            )

    file_path: Optional[Path] = None
    try:
        await update_status(context, chat_id, message_id, f"⏳ <b>{label} – fetching…</b>")

        file_path = await download_media(url, audio_only, on_progress)

        if not file_path or not file_path.exists():
            await update_status(
                context, chat_id, message_id,
                "❌ <b>Download failed.</b>\n\n"
                "The file may be unavailable, geo-restricted, or too large.",
            )
            return

        size_mb = file_path.stat().st_size / 1_048_576
        if file_path.stat().st_size > MAX_FILE_BYTES:
            await update_status(
                context, chat_id, message_id,
                f"⚠️ <b>File too large ({size_mb:.1f} MB).</b>\n\n"
                "Telegram bots support up to 45 MB.\n"
                "Try a shorter video or lower quality.",
            )
            return

        await update_status(context, chat_id, message_id, f"📤 <b>Uploading ({size_mb:.1f} MB)…</b>")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)

        caption = f"✅ <b>{file_path.stem[:60]}</b>"
        with open(file_path, "rb") as fh:
            if audio_only:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=fh,
                    filename=file_path.name,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
            else:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=fh,
                    filename=file_path.name,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    supports_streaming=True,
                )

        await update_status(context, chat_id, message_id, "✅ <b>Done! Enjoy.</b>")

    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc)[:200]
        logger.error("yt-dlp DownloadError: %s", exc)
        await update_status(
            context, chat_id, message_id,
            f"❌ <b>yt-dlp error:</b>\n<code>{msg}</code>",
        )

    except TelegramError as exc:
        logger.error("TelegramError during upload: %s", exc)
        await update_status(
            context, chat_id, message_id,
            f"❌ <b>Telegram upload error:</b>\n<code>{exc}</code>",
        )

    except Exception as exc:
        logger.exception("Unexpected error in _run_download: %s", exc)
        await update_status(
            context, chat_id, message_id,
            "❌ <b>Something went wrong.</b>\n\nPlease try again later.",
        )

    finally:
        if file_path and file_path.exists():
            try:
                file_path.unlink()
                file_path.parent.rmdir()
            except OSError:
                pass


# ── Global Error Handler ──────────────────────────────────────────────────────

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_html(
                "⚠️ <b>An internal error occurred.</b>\n\n"
                "Please try again or use /start to reset.",
                reply_markup=main_menu_kb(),
            )
        except TelegramError:
            pass


# ── Application Factory ───────────────────────────────────────────────────────

def setup_application(token: str) -> Application:
    """Build and wire up the PTB Application."""
    application: Application = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(True)  # handle multiple users in parallel
        .build()
    )

    # Commands
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help",  cmd_help))

    # Inline button callbacks
    application.add_handler(CallbackQueryHandler(on_callback))

    # Any plain text message (auto-detect URLs)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_message)
    )

    # Global error handler
    application.add_error_handler(on_error)

    logger.info("PTB application configured with %d handlers.", len(application.handlers[0]))
    return application
