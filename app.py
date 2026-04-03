import sys
import os
import asyncio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telegram_bot
from aiogram.utils import executor
from bot.admin import register_admin
from services.ton_service import get_transactions, parse_payment, calculate_tokens
from db.database import is_tx_processed, save_transaction, add_tokens, ensure_user

register_admin(telegram_bot.dp)


async def check_ton_payments():
    """Фоновый воркер — проверяет новые TON транзакции каждые 60 секунд."""
    while True:
        try:
            transactions = get_transactions(limit=20)
            for tx in transactions:
                payment = parse_payment(tx)
                if not payment:
                    continue

                tx_hash = payment["tx_hash"]
                user_id = payment["user_id"]
                ton_amount = payment["ton_amount"]

                if is_tx_processed(tx_hash):
                    continue

                tokens = calculate_tokens(ton_amount)
                if tokens <= 0:
                    continue

                ensure_user(user_id)
                new_balance = add_tokens(user_id, tokens)
                save_transaction(tx_hash, user_id, ton_amount, tokens)

                try:
                    await telegram_bot.bot.send_message(
                        user_id,
                        f"✅ Оплата получена!\n\n"
                        f"💎 TON: {ton_amount:.2f}\n"
                        f"🪙 Начислено токенов: {tokens}\n"
                        f"💰 Баланс: {new_balance} токенов"
                    )
                except Exception as e:
                    print(f"TON NOTIFY ERROR: {e}")

        except Exception as e:
            print(f"TON WORKER ERROR: {e}")

        await asyncio.sleep(60)


async def on_startup(dp):
    asyncio.create_task(check_ton_payments())


if __name__ == "__main__":
    executor.start_polling(
        telegram_bot.dp,
        skip_updates=True,
        on_startup=on_startup,
    )
