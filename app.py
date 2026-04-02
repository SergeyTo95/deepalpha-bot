import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telegram_bot
from aiogram.utils import executor

from bot.admin import register_admin

register_admin(dp)

if __name__ == "__main__":
    executor.start_polling(telegram_bot.dp, skip_updates=True)
