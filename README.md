# 🤖 Telegram Media Bot

Production-ready Telegram bot powered by **FastAPI** + **python-telegram-bot v21+**,
deployed on **Render.com** using **webhook mode**.

---

## 📁 Project Structure

```
telegram-bot/
├── main.py          # FastAPI server + webhook lifecycle
├── bot.py           # All PTB handlers, yt-dlp downloader
├── requirements.txt # Pinned Python dependencies
├── render.yaml      # Render.com IaC manifest
├── Procfile         # Fallback start command
└── README.md        # This file
```

---

## ✨ Features

| Feature | Details |
|---|---|
| `/start` | Welcome message + inline menu |
| `/help` | Usage instructions |
| **YouTube download** | Auto-detected URLs → MP4 or MP3 |
| **Format choice** | Video (≤720p) or Audio (192 kbps MP3) |
| **Live progress** | Animated progress bar during download |
| **Callback menus** | Full inline button navigation system |
| **Error handling** | Per-handler + global error handler |
| **Logging** | Structured timestamps + levels |
| **Webhook mode** | No polling – production-grade |
| **Concurrent updates** | Multiple users handled in parallel |

---

## 🚀 Render.com Deployment (Step-by-Step)

### Step 1 – Create a Telegram Bot

1. Open Telegram, search **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **BOT_TOKEN** (looks like `123456:ABCdef…`)

### Step 2 – Push Code to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
gh repo create my-telegram-bot --public --push
# or: git remote add origin https://github.com/YOU/REPO.git && git push -u origin main
```

### Step 3 – Create a Render Web Service

1. Go to [render.com](https://render.com) → **New → Web Service**
2. Connect your GitHub repo
3. Render auto-detects `render.yaml`. Confirm:
   - **Runtime:** Python
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port 10000`
   - **Plan:** Free (or Starter for always-on)

### Step 4 – Set Environment Variables

In the Render dashboard → **Environment** tab, add:

| Key | Value |
|---|---|
| `BOT_TOKEN` | `123456:ABCdef…` (from BotFather) |
| `WEBHOOK_URL` | `https://YOUR-SERVICE-NAME.onrender.com` |
| `BOT_USERNAME` | `YourBotUsername` (without @, optional) |

> ⚠️ **Never** commit secrets to Git. Use the Render dashboard only.

### Step 5 – Deploy

Click **Deploy** (or push to main branch – Render auto-deploys).

The bot will:
1. Start the FastAPI server on port 10000
2. Initialize the PTB application
3. Register the webhook with Telegram automatically

### Step 6 – Verify

```bash
# Check bot is live
curl https://YOUR-SERVICE-NAME.onrender.com/

# Should return:
# {"status":"online","bot_username":"@YourBot","mode":"webhook", ...}

# Check Telegram webhook
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

---

## 🛠 Local Development

```bash
# Install deps
pip install -r requirements.txt

# Install ffmpeg (required for yt-dlp audio extraction)
# macOS:   brew install ffmpeg
# Ubuntu:  sudo apt install ffmpeg
# Windows: winget install ffmpeg

# Run with polling instead of webhook for local testing
# (temporarily replace main.py startup with polling – see below)

# Set env vars
export BOT_TOKEN="123456:ABCdef..."
export WEBHOOK_URL="https://your-ngrok-url.ngrok.io"  # use ngrok for local webhook

# Start server
uvicorn main:app --host 0.0.0.0 --port 10000 --reload
```

### Using ngrok for local webhook testing

```bash
# Install ngrok: https://ngrok.com/download
ngrok http 10000
# Copy the https URL → set as WEBHOOK_URL
```

---

## ⚙️ Architecture

```
Telegram servers
      │  HTTPS POST /webhook/<BOT_TOKEN>
      ▼
 Render.com (uvicorn)
      │
  FastAPI app
      │  Update.de_json()
      ▼
 PTB Application.process_update()
      │
  ┌───┴─────────────────────┐
  │  CommandHandler /start  │
  │  CommandHandler /help   │
  │  CallbackQueryHandler   │  ← inline menu navigation
  │  MessageHandler (text)  │  ← URL detection
  └─────────────────────────┘
      │
  yt-dlp (thread pool)
      │
  Telegram sendVideo / sendAudio
```

---

## 🔒 Security Notes

- The webhook path includes the `BOT_TOKEN` as a secret segment
  (`/webhook/<token>`), so only Telegram can trigger it
- All secrets are environment variables – nothing hardcoded
- File size checked before upload (45 MB limit)
- Temp files cleaned up in `finally` blocks

---

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | Web framework |
| `uvicorn` | ASGI server |
| `python-telegram-bot` | Telegram Bot API (v21+) |
| `yt-dlp` | YouTube / media downloader |
| `httpx` | Async HTTP (used by PTB) |

---

## 🐛 Troubleshooting

| Problem | Fix |
|---|---|
| Bot doesn't respond | Check webhook via `getWebhookInfo`, verify `WEBHOOK_URL` |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| FFmpeg not found | Install ffmpeg (required for MP3 extraction) |
| File too large | Telegram bot limit is 50 MB; bot caps at 45 MB |
| Free tier sleeps | Use UptimeRobot to ping `/health` every 5 min |

---

## 📄 License

MIT – free to use, modify, and deploy.
