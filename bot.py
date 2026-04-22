import asyncio
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path

import yt_dlp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s]+")
STORE = "store"


# ── UI ───────────────────────────────

def kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 Video", callback_data="v"),
            InlineKeyboardButton("🎵 Audio", callback_data="a"),
        ]
    ])


def store_url(data, url):
    uid = uuid.uuid4().hex[:8]
    data.setdefault(STORE, {})[uid] = url
    return uid


def get_url(data, uid):
    return data.get(STORE, {}).get(uid)


# ── PROGRESS ─────────────────────────

class Tracker:
    def __init__(self):
        self.p = 0
        self.dl = 0
        self.total = 0
        self.speed = 0
        self.eta = None
        self.unknown = False

    def hook(self, d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            self.dl = d.get("downloaded_bytes", 0)
            self.speed = d.get("speed") or 0
            self.eta = d.get("eta")

            if total:
                self.total = total
                self.p = int(self.dl * 100 / total)
            else:
                self.unknown = True


# ── DOWNLOAD ─────────────────────────

def download_sync(url, path, audio, t):
    opts = {
        "outtmpl": f"{path}/%(title).50s.%(ext)s",
        "progress_hooks": [t.hook],

        # 🔥 FIX FOR PROGRESS
        "quiet": False,
        "no_warnings": False,
        "noprogress": False,

        "retries": 3,
    }

    if audio:
        opts["format"] = "bestaudio/best"
    else:
        opts["format"] = "best[height<=720]"

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


async def download(url, audio, cb):
    loop = asyncio.get_running_loop()
    tmp = tempfile.mkdtemp()
    t = Tracker()

    task = loop.run_in_executor(None, download_sync, url, tmp, audio, t)

    last = -1

    while not task.done():
        await asyncio.sleep(1)

        if t.p != last or t.unknown:
            last = t.p
            await cb(t)

    await task

    files = list(Path(tmp).iterdir())
    return files[0] if files else None


# ── HANDLERS ─────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send link 👇", reply_markup=kb())


async def msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = URL_RE.findall(update.message.text or "")
    if not url:
        return

    uid = store_url(context.bot_data, url[0])
    await update.message.reply_text("Choose:", reply_markup=kb())


async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    url = list(context.bot_data.get(STORE, {}).values())[0]
    audio = q.data == "a"

    msg = await q.edit_message_text("Starting...")

    async def progress(t):
        try:
            mb = t.dl / 1_048_576
            if t.p > 0:
                bar = "█" * (t.p // 10) + "░" * (10 - t.p // 10)
                text = f"[{bar}] {t.p}%\n{mb:.1f} MB"
            else:
                text = f"{mb:.1f} MB"
            await msg.edit_text(text)
        except:
            pass

    file = await download(url, audio, progress)

    if not file:
        return await msg.edit_text("Failed")

    with open(file, "rb") as f:
        if audio:
            await context.bot.send_audio(q.message.chat_id, f)
        else:
            await context.bot.send_video(q.message.chat_id, f)

    await msg.edit_text("Done")


def setup_application(token):
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT, msg))
    app.add_handler(CallbackQueryHandler(cb))

    return app