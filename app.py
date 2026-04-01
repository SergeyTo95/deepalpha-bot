import os
import aiohttp
from aiogram.utils import executor
import telegram_bot  # <-- добавь эту строку

if __name__ == "__main__":
    executor.start_polling(telegram_bot.dp, skip_updates=True)
