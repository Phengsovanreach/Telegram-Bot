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

# ── CONFIG ───────────────────────────────────────────────────────────────────

MAX_FILE_BYTES = 45 * 1024 * 1024

# 🌍 GLOBAL URL SUPPORT (YouTube + TikTok + Facebook + etc.)
URL_RE = re.compile(r"https?://[^\s]+")

URL_STORE_KEY = "url_store"


# ── KEYBOARD ─────────────────────────────────────────────────────────────────

def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 Video", callback_data="m:video"),
            InlineKeyboardButton("🎵 Audio", callback_data="m:audio"),
        ],
        [
            InlineKeyboardButton("📖 Help", callback_data="m:help"),
        ]
    ])


def format_kb(uid: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 MP4", callback_data=f"dl:v:{uid}"),
            InlineKeyboardButton("🎵 MP3", callback_data=f"dl:a:{uid}"),
        ]
    ])


# ── URL STORE ────────────────────────────────────────────────────────────────

def store_url(bot_data, url):
    uid = uuid.uuid4().hex[:8]
    bot_data.setdefault(URL_STORE_KEY, {})[uid] = url
    return uid


def get_url(bot_data, uid):
    return bot_data.get(URL_STORE_KEY, {}).get(uid)


# ── YT-DLP CORE ──────────────────────────────────────────────────────────────

class Progress:
    def __init__(self):
        self.p = 0

    def hook(self, d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes", 0)
            if total:
                self.p = int(done / total * 100)


def download_sync(url, path, audio, tracker):
    opts = {
        "outtmpl": f"{path}/%(title).50s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [tracker.hook],
        "max_filesize": MAX_FILE_BYTES,
    }

    if audio:
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        })
    else:
        opts["format"] = "best[height<=720]"

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


async def download(url, audio, progress_cb):
    loop = asyncio.get_running_loop()
    tmp = tempfile.mkdtemp()
    tracker = Progress()

    task = loop.run_in_executor(None, download_sync, url, tmp, audio, tracker)

    while not task.done():
        await asyncio.sleep(2)
        await progress_cb(tracker.p)

    await task

    files = list(Path(tmp).iterdir())
    return files[0] if files else None


# ── START ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Send any link:\nYouTube 🎥 TikTok 🎵 Facebook 📘",
        reply_markup=main_menu()
    )


# ── MESSAGE HANDLER ──────────────────────────────────────────────────────────

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    urls = URL_RE.findall(text)

    if not urls:
        return await update.message.reply_text("Send a valid video link.")

    url = urls[0]

    await update.message.reply_text("🔗 Link received... processing.")

    uid = store_url(context.bot_data, url)

    await update.message.reply_text(
        "Choose format:",
        reply_markup=format_kb(uid)
    )


# ── CALLBACK ─────────────────────────────────────────────────────────────────

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data

    if data.startswith("dl:"):
        _, mode, uid = data.split(":")
        url = get_url(context.bot_data, uid)

        if not url:
            return await q.edit_message_text("Session expired.")

        audio = mode == "a"

        msg = await q.edit_message_text("⏳ Downloading...")

        async def progress(p):
            if p % 20 == 0:
                await msg.edit_text(f"Downloading... {p}%")

        file = await download(url, audio, progress)

        if not file:
            return await msg.edit_text("Failed download.")

        with open(file, "rb") as f:
            if audio:
                await context.bot.send_audio(q.message.chat_id, f)
            else:
                await context.bot.send_video(q.message.chat_id, f)

        await msg.edit_text("✅ Done!")

# ── APP ──────────────────────────────────────────────────────────────────────

def setup_application(token):
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT, on_message))
    app.add_handler(CallbackQueryHandler(callback))

    return app