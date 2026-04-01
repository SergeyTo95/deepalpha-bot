import os
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

# --- ENV ---
TG_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TG_TOKEN)
dp = Dispatcher(bot)


# --- ПРОСТОЙ ТЕСТ ---
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.answer("🚀 DeepAlpha bot is running!")


@dp.message_handler()
async def echo(message: types.Message):
    await message.answer(f"You said: {message.text}")


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates
