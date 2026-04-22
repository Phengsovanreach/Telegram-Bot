
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from telegram import Update

from bot import setup_application

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s – %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.environ["BOT_TOKEN"]           # set in Render dashboard
WEBHOOK_URL: str = os.environ["WEBHOOK_URL"].rstrip("/")  # e.g. https://your-app.onrender.com

WEBHOOK_SECRET_PATH = f"/webhook/{BOT_TOKEN}"
FULL_WEBHOOK_URL = f"{WEBHOOK_URL}{WEBHOOK_SECRET_PATH}"

# ── PTB Application (module-level singleton) ──────────────────────────────────

ptb_app = setup_application(BOT_TOKEN)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialise PTB + register webhook. Shutdown: cleanup."""
    # ── STARTUP ──
    logger.info("Initialising Telegram bot application …")
    await ptb_app.initialize()
    await ptb_app.start()

    webhook_info = await ptb_app.bot.get_webhook_info()
    if webhook_info.url != FULL_WEBHOOK_URL:
        await ptb_app.bot.set_webhook(
            url=FULL_WEBHOOK_URL,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            max_connections=100,
        )
        logger.info("Webhook registered: %s", FULL_WEBHOOK_URL)
    else:
        logger.info("Webhook already set: %s", FULL_WEBHOOK_URL)

    bot_info = await ptb_app.bot.get_me()
    logger.info("Bot online: @%s (id=%s)", bot_info.username, bot_info.id)

    yield  # ← FastAPI serves requests here

    # ── SHUTDOWN ──
    logger.info("Shutting down Telegram bot …")
    await ptb_app.bot.delete_webhook(drop_pending_updates=False)
    await ptb_app.stop()
    await ptb_app.shutdown()
    logger.info("Bot shutdown complete.")


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Telegram Media Bot",
    description="Production Telegram bot – FastAPI + webhook on Render.com",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,   # Disable Swagger in prod (optional)
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
async def root():
    """Public health / info endpoint."""
    bot_info = await ptb_app.bot.get_me()
    return {
        "status": "online",
        "bot_username": f"@{bot_info.username}",
        "mode": "webhook",
        "webhook_url": FULL_WEBHOOK_URL,
        "framework": "python-telegram-bot v21+",
        "server": "Render.com",
    }


@app.get("/health", tags=["Health"])
async def health():
    """Render health-check probe."""
    return {"status": "ok"}


@app.post(WEBHOOK_SECRET_PATH, tags=["Webhook"])
async def telegram_webhook(request: Request) -> Response:
    """
    Receive Telegram updates.
    The secret BOT_TOKEN in the path acts as a simple auth layer.
    """
    try:
        payload = await request.json()
        update = Update.de_json(data=payload, bot=ptb_app.bot)
        await ptb_app.process_update(update)
    except Exception:
        logger.exception("Failed to process incoming update")
        # Return 200 anyway so Telegram doesn't retry endlessly
    return Response(status_code=status.HTTP_200_OK)
