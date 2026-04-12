import sys
import os
import asyncio
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telegram_bot
from bot.admin import register_admin
from services.ton_service import get_transactions, parse_payment
from db.database import (
    is_tx_processed, save_transaction, add_tokens, ensure_user,
    get_user, add_referral_earnings, get_setting, set_setting,
    get_all_pending, delete_pending, get_all_users,
    get_subscribed_users, set_subscription, is_subscribed,
    save_signal_cache, get_signal_cache, get_all_cache_status,
    get_token_packages, find_package_by_amount,
)

register_admin(telegram_bot.dp)

CATEGORIES = ["Politics", "Crypto", "Sports", "Economy", "Tech"]


def calculate_tokens_for_amount(ton_amount: float) -> int:
    """Рассчитывает количество токенов по сумме — сначала ищет пакет, потом базовую цену."""
    # Ищем подходящий пакет с допуском 0.05 TON
    package = find_package_by_amount(ton_amount, tolerance=0.05)
    if package:
        print(f"TON PAYMENT: found package '{package['name']}' — {package['tokens']} tokens")
        return package["tokens"]

    # Fallback — базовая цена из настроек
    try:
        token_price = float(get_setting("token_price_ton", "0.1"))
        if token_price <= 0:
            token_price = 0.1
        tokens = int(ton_amount / token_price)
        print(f"TON PAYMENT: no package found, calculated {tokens} tokens at {token_price} TON each")
        return tokens
    except Exception:
        return int(ton_amount / 0.1)


async def update_signal_cache():
    """Обновляет кэш сигналов по всем категориям."""
    print("🔄 Starting signal cache update...")
    from agents.opportunity_agent import OpportunityAgent

    for category in CATEGORIES:
        try:
            print(f"🔄 Updating cache for {category}...")
            agent = OpportunityAgent()
            result = agent.run(
                lang="ru",
                limit=2,
                category_filter=category,
            )

            if result and result.get("question") != "No strong opportunity found":
                import time
                result["cached_at"] = int(time.time())
                result["cache_category"] = category
                save_signal_cache(category, result)
                print(f"✅ Cache updated for {category}: {result.get('question', '')[:50]}")
            else:
                print(f"⚠️ No signal found for {category}")

            await asyncio.sleep(5)

        except Exception as e:
            print(f"CACHE UPDATE ERROR [{category}]: {e}")
            await asyncio.sleep(2)

    print("✅ Signal cache update complete")


async def cache_worker():
    """Воркер обновляет кэш каждые 6 часов."""
    await asyncio.sleep(21600)  # Первое обновление через 6 часов
    await update_signal_cache()

    while True:
        try:
            await asyncio.sleep(21600)
            await update_signal_cache()
        except Exception as e:
            print(f"CACHE WORKER ERROR: {e}")
            await asyncio.sleep(60)


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

                payment = parse_payment(tx)
                user_id = None
                payment_type = "tokens"

                if payment and payment.get("user_id"):
                    user_id = payment["user_id"]
                else:
                    tx_time = tx.get("utime", 0)
                    for uid, p in list(pending.items()):
                        time_diff = abs(tx_time - p["timestamp"])
                        amount_diff = abs(ton_amount - p["amount"])
                        if time_diff < 300 and amount_diff < 0.1:
                            user_id = uid
                            payment_type = p.get("payment_type", "tokens")
                            break

                if not user_id:
                    continue

                ensure_user(user_id)

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

                    # Начисляем реферальные токены рефереру
                    referral_tokens = calculate_tokens_for_amount(referral_bonus_ton)
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

                if payment_type == "subscription":
                    sub_days = int(get_setting("subscription_days", "30"))
                    until = set_subscription(user_id, days=sub_days)

                    save_transaction(
                        tx_hash, user_id, ton_amount, 0,
                        referral_bonus_ton=referral_bonus_ton,
                        referrer_id=referrer_id,
                    )

                    try:
                        await telegram_bot.bot.send_message(
                            user_id,
                            f"✅ Подписка активирована!\n\n"
                            f"💎 Оплачено: {ton_amount:.2f} TON\n"
                            f"📅 Действует до: {until[:10]}\n\n"
                            f"Теперь у тебя:\n"
                            f"• 🔔 Ежедневные сигналы\n"
                            f"• 📊 До 15 анализов в день\n"
                            f"• 💡 До 3 сигналов в день"
                        )
                    except Exception as e:
                        print(f"SUB NOTIFY ERROR: {e}")

                else:
                    # Рассчитываем токены с учётом пакетов
                    tokens = calculate_tokens_for_amount(ton_amount)
                    if tokens <= 0:
                        continue

                    new_balance = add_tokens(user_id, tokens)

                    save_transaction(
                        tx_hash, user_id, ton_amount, tokens,
                        referral_bonus_ton=referral_bonus_ton,
                        referrer_id=referrer_id,
                    )

                    # Определяем название пакета для уведомления
                    package = find_package_by_amount(ton_amount, tolerance=0.05)
                    package_name = f"«{package['name']}»" if package else ""
                    discount_text = ""
                    if package and package.get("discount_percent", 0) > 0:
                        discount_text = f"\n🏷 Скидка: {package['discount_percent']}%"

                    try:
                        await telegram_bot.bot.send_message(
                            user_id,
                            f"✅ Оплата получена!\n\n"
                            f"💎 TON: {ton_amount:.4f}\n"
                            f"📦 Пакет: {package_name}\n"
                            f"🪙 Начислено токенов: {tokens}"
                            f"{discount_text}\n"
                            f"💰 Баланс: {new_balance} токенов"
                        )
                    except Exception as e:
                        print(f"TON NOTIFY ERROR: {e}")

                delete_pending(user_id)

        except Exception as e:
            print(f"TON WORKER ERROR: {e}")

        await asyncio.sleep(60)


async def send_daily_notifications():
    try:
        print("📢 Starting daily notifications...")

        all_users = get_all_users(limit=10000)
        subscribed_ids = {u["user_id"] for u in get_subscribed_users()}

        # Берём лучший кэшированный сигнал
        best_cached = None
        for category in CATEGORIES:
            cached = get_signal_cache(category, max_age_seconds=7200)
            if cached and cached.get("question") != "No strong opportunity found":
                if not best_cached or cached.get("opportunity_score", 0) > best_cached.get("opportunity_score", 0):
                    best_cached = cached

        if not best_cached:
            print("📢 No cached signals for notification")
            return

        score = best_cached.get("opportunity_score", 0)
        question = best_cached.get("question", "")[:60]
        market_prob = best_cached.get("market_probability", "")
        category = best_cached.get("category", "")
        probability = best_cached.get("probability", "")
        confidence = best_cached.get("confidence", "")
        conclusion = best_cached.get("conclusion", "")
        score_bar = "🟩" * min(int(score / 20), 5) + "⬜" * (5 - min(int(score / 20), 5))

        teaser = (
            f"🔔 DeepAlpha — Сигнал дня\n\n"
            f"📌 {question}\n\n"
            f"🏷 Категория: {category}\n"
            f"📊 Рынок: {market_prob}\n"
            f"⚡ Скор: {score} {score_bar}\n\n"
            f"🔒 Полный анализ доступен в боте\n"
            f"👉 Нажми 💡 Сигнал часа"
        )

        conf_emoji = "🟢" if "high" in confidence.lower() or "высок" in confidence.lower() else (
            "🟡" if "medium" in confidence.lower() or "средн" in confidence.lower() else "🔴"
        )
        full_text = (
            f"🔔 DeepAlpha — Сигнал дня\n"
            f"{'─' * 30}\n\n"
            f"📌 {question}\n\n"
            f"🏷 Категория: {category}\n"
            f"📊 Рынок: {market_prob}\n"
            f"🎯 Прогноз: {probability}\n"
            f"{conf_emoji} Уверенность: {confidence}\n"
            f"⚡ Скор: {score} {score_bar}\n\n"
            f"{'─' * 30}\n"
            f"📝 Вывод: {conclusion}\n\n"
            f"✅ Подписка активна"
        )

        sent_teaser = 0
        sent_full = 0
        failed = 0

        for user in all_users:
            if user.get("is_banned"):
                continue
            uid = user["user_id"]
            try:
                if uid in subscribed_ids:
                    await telegram_bot.bot.send_message(uid, full_text)
                    sent_full += 1
                else:
                    await telegram_bot.bot.send_message(uid, teaser)
                    sent_teaser += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1

        print(f"📢 Full: {sent_full}, Teaser: {sent_teaser}, Failed: {failed}")
        set_setting("last_notification_sent", datetime.now(timezone.utc).isoformat())

    except Exception as e:
        print(f"NOTIFICATION ERROR: {e}")


async def notification_worker():
    await asyncio.sleep(30)
    while True:
        try:
            notifications_enabled = get_setting("notifications_enabled", "off")
            if notifications_enabled == "on":
                notify_hour = int(get_setting("notification_hour", "9"))
                notify_interval = get_setting("notification_interval", "daily")

                now = datetime.now(timezone.utc)
                current_hour = now.hour
                current_minute = now.minute

                if current_hour == notify_hour and current_minute < 2:
                    last_sent = get_setting("last_notification_sent", "")
                    should_send = False

                    if not last_sent:
                        should_send = True
                    else:
                        try:
                            last_dt = datetime.fromisoformat(last_sent)
                            diff_hours = (now - last_dt).total_seconds() / 3600
                            if notify_interval == "daily" and diff_hours >= 23:
                                should_send = True
                            elif notify_interval == "weekly" and diff_hours >= 167:
                                should_send = True
                        except Exception:
                            should_send = True

                    if should_send:
                        await send_daily_notifications()

        except Exception as e:
            print(f"NOTIFICATION WORKER ERROR: {e}")

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
    asyncio.create_task(notification_worker())
    # asyncio.create_task(cache_worker())  # Раскомментируй когда нужен кэш
    await run_polling()


if __name__ == "__main__":
    asyncio.run(main())
