import os
import aiohttp
from aiogram.utils import executor
import telegram_bot  # <-- добавь эту строку
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    executor.start_polling(telegram_bot.dp, skip_updates=True)
  
