import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from aiogram import types

from telegram_bot import bot, dp

SPACE_HOST = os.getenv("SPACE_HOST")
if not SPACE_HOST:
    raise RuntimeError("SPACE_HOST is missing")

APP_BASE_URL = f"https://{SPACE_HOST}"
WEBHOOK_PATH = "/telegram/webhook"
WEBHOOK_URL = f"{APP_BASE_URL}{WEBHOOK_PATH}"

app = FastAPI()


@app.on_event("startup")
async def on_startup():
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook set to: {WEBHOOK_URL}")


@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await bot.session.close()
    print("🛑 Webhook deleted and bot session closed")


@app.get("/")
async def root():
    return {"status": "ok", "service": "DeepAlpha webhook bot"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = types.Update.to_object(data)
    await dp.process_update(update)
    return JSONResponse({"ok": True})
