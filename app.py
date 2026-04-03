
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telegram_bot
from bot.admin import register_admin
from services.ton_service import get_transactions, parse_payment, calculate_tokens
from db.database import (
    is_tx_processed, save_transaction, add_tokens, ensure_user,
    get_user, add_referral_earnings, get_setting,
    get_all_pending, delete_pending,
)

register_admin(telegram_bot.dp)


async def check_ton_payments():
    await asyncio.sleep(15)
    while True:
        try:
            transactions = get_transactions(limit=20)
            pending = get_all_pending()

            for tx in transactions:
                tx_hash = tx.get("transaction_id", {}).get("hash", "")
                if not tx_hash:
                    continue

                if is_tx_processed(tx_hash):
                    continue

                in_msg = tx.get("in_msg", {})
                value = int(in_msg.get("value", 0))
                ton_amount = value / 1_000_000_000

                if ton_amount <= 0:
                    continue

                # Сначала пробуем по комментарию
                payment = parse_payment(tx)
                user_id = None

                if payment and payment.get("user_id"):
                    user_id = payment["user_id"]
                    print(f"TON: found by comment, user_id={user_id}, amount={ton_amount}")
                else:
                    # Ищем по pending в БД
                    tx_time = tx.get("utime", 0)
                    for uid, p in list(pending.items()):
                        time_diff = abs(tx_time - p["timestamp"])
                        amount_diff = abs(ton_amount - p["amount"])
                        print(f"TON: checking pending uid={uid}, time_diff={time_diff}, amount_diff={amount_diff}")
                        if time_diff < 300 and amount_diff < 0.01:
                            user_id = uid
                            print(f"TON: found by pending, user_id={user_id}")
                            break

                if not user_id:
                    print(f"TON: no user found for tx {tx_hash[:8]}, amount={ton_amount}")
                    continue

                tokens = calculate_tokens(ton_amount)
                if tokens <= 0:
                    continue

                ensure_user(user_id)
                new_balance = add_tokens(user_id, tokens)

                # Реферальный бонус
                referral_bonus_ton = 0
                referrer_id = None
                user = get_user(user_id)
                if user and user.get("referred_by"):
                    referrer_id = user["referred_by"]
                    try:
                        ref_percent = float(get_setting("referral_percent", "10"))
                    except Exception:
                        ref_percent = 10
                    referral_bonus_ton = round(ton_amount * ref_percent / 100, 6)
                    referral_tokens = calculate_tokens(referral_bonus_ton)
                    if referral_tokens > 0:
                        add_tokens(referrer_id, referral_tokens)
                    add_referral_earnings(referrer_id, referral_bonus_ton)

                    try:
                        await telegram_bot.bot.send_message(
                            referrer_id,
                            f"🎉 Ваш реферал пополнил баланс!\n\n"
                            f"💎 Его покупка: {ton_amount:.2f} TON\n"
                            f"🎁 Ваш бонус: {referral_bonus_ton:.4f} TON "
                            f"({referral_tokens} токенов)"
                        )
                    except Exception as e:
                        print(f"REFERRAL NOTIFY ERROR: {e}")

                save_transaction(
                    tx_hash, user_id, ton_amount, tokens,
                    referral_bonus_ton=referral_bonus_ton,
                    referrer_id=referrer_id,
                )

                delete_pending(user_id)

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


async def run_polling():
    while True:
        try:
            print("✅ Starting polling...")
            await telegram_bot.dp.start_polling(
                reset_webhook=True,
                timeout=20,
                relax=0.5,
                fast=True,
            )
        except Exception as e:
            err = str(e)
            if "TerminatedByOtherGetUpdates" in err or "Conflict" in err:
                print("⚠️ Conflict detected, waiting 15 seconds...")
                await asyncio.sleep(15)
            else:
                print(f"Polling error: {e}")
                await asyncio.sleep(5)


async def main():
    try:
        await telegram_bot.bot.delete_webhook(drop_pending_updates=True)
        print("✅ Webhook deleted")
    except Exception as e:
        print(f"Webhook delete error: {e}")

    asyncio.create_task(check_ton_payments())
    await run_polling()


if __name__ == "__main__":
    asyncio.run(main())
