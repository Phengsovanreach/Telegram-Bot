import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from telegram import Update

from bot import setup_application

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"].rstrip("/")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
FULL_WEBHOOK = f"{WEBHOOK_URL}{WEBHOOK_PATH}"

ptb_app = setup_application(BOT_TOKEN)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting bot...")

    await ptb_app.initialize()
    await ptb_app.start()

    await ptb_app.bot.set_webhook(
        url=FULL_WEBHOOK,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        max_connections=100,
    )

    logger.info("Webhook set: %s", FULL_WEBHOOK)

    yield

    await ptb_app.bot.delete_webhook()
    await ptb_app.stop()
    await ptb_app.shutdown()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    me = await ptb_app.bot.get_me()
    return {"status": "online", "bot": me.username}


@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
    except Exception as e:
        logger.exception(e)

    return Response(status_code=status.HTTP_200_OK)