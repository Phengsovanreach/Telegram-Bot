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
)

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
WEBHOOK_URL: str = os.environ["WEBHOOK_URL"].rstrip("/")

WEBHOOK_SECRET_PATH = f"/webhook/{BOT_TOKEN}"
FULL_WEBHOOK_URL = f"{WEBHOOK_URL}{WEBHOOK_SECRET_PATH}"

# ── PTB App ───────────────────────────────────────────────────────────────────

ptb_app = setup_application(BOT_TOKEN)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting bot...")

    await ptb_app.initialize()
    await ptb_app.start()

    await ptb_app.bot.set_webhook(
        url=FULL_WEBHOOK_URL,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        max_connections=100,
    )

    logger.info("Webhook set: %s", FULL_WEBHOOK_URL)

    yield

    logger.info("Shutting down bot...")
    await ptb_app.bot.delete_webhook()
    await ptb_app.stop()
    await ptb_app.shutdown()


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Media Downloader Bot",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    me = await ptb_app.bot.get_me()
    return {
        "status": "online",
        "bot": me.username,
        "mode": "webhook",
    }


@app.post(WEBHOOK_SECRET_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
    except Exception:
        logger.exception("Webhook error")

    return Response(status_code=status.HTTP_200_OK)