import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telegram_bot
from bot.admin import register_admin
from services.ton_service import get_transactions, parse_payment
from services.polymarket_service import (
    list_markets, list_events, normalize_market_data,
    normalize_event_for_channel, build_market_url,
)
from services.polymarket_resolver import resolve_prediction, fetch_market_by_slug, is_market_resolved
from db.database import (
    is_tx_processed, save_transaction, add_tokens, ensure_user,
    get_user, add_referral_earnings, get_setting, set_setting,
    get_all_pending, delete_pending, get_all_users,
    get_subscribed_users, set_subscription, is_subscribed,
    save_signal_cache, get_signal_cache,
    get_token_packages, find_package_by_amount,
    get_unresolved_predictions, update_resolution,
    get_active_watchlist_items, get_watchlist_subscribers,
    update_watchlist_probability, mark_watchlist_notified,
    reset_watchlist_change_notification, close_watchlist_market,
    cleanup_old_closed_watchlist,
)

register_admin(telegram_bot.dp)

CATEGORIES = ["Politics", "Crypto", "Sports", "Economy", "Tech"]
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "DeepAlphaAI_bot")


def calculate_tokens_for_amount(ton_amount: float) -> int:
    package = find_package_by_amount(ton_amount, tolerance=0.05)
    if package:
        print(f"TON PAYMENT: found package '{package['name']}' — {package['tokens']} tokens")
        return package["tokens"]
    try:
        token_price = float(get_setting("token_price_ton", "0.1"))
        if token_price <= 0:
            token_price = 0.1
        tokens = int(ton_amount / token_price)
        return tokens
    except Exception:
        return int(ton_amount / 0.1)


# ═══════════════════════════════════════════
# CHANNEL POSTER
# ═══════════════════════════════════════════

async def post_to_channel():
    """Постит интересный рынок в канал с ротацией категорий."""
    if not CHANNEL_ID:
        print("📢 CHANNEL_ID not set, skipping")
        return

    try:
        print("📢 Posting to channel...")
        import random
        from agents.market_agent import MarketAgent

        categories_cycle = ["Politics", "Crypto", "Sports", "Economy", "Tech"]
        last_category = get_setting("last_channel_category", "")
        try:
            last_idx = categories_cycle.index(last_category)
            next_idx = (last_idx + 1) % len(categories_cycle)
        except ValueError:
            next_idx = 0
        category_filter = categories_cycle[next_idx]
        set_setting("last_channel_category", category_filter)
        print(f"📢 Category: {category_filter}")

        shown_raw = get_setting("channel_shown_markets", "")
        shown = set(shown_raw.split(",")) if shown_raw else set()

        offset = random.randint(0, 50)
        events = list_events(limit=50, offset=offset)
        if not events:
            events = list_events(limit=50, offset=0)

        print(f"📢 Got {len(events)} events from API")

        agent = MarketAgent()
        candidates = []

        for event in events:
            normalized = normalize_event_for_channel(event)
            if not normalized:
                continue

            question = normalized.get("question", "")
            if not question or len(question) < 10:
                continue

            event_id = str(normalized.get("id", ""))
            if event_id and event_id in shown:
                continue

            detected = agent._detect_category(question)
            if detected != category_filter:
                continue

            try:
                markets = event.get("markets", [])
                skip = False
                for m in markets:
                    if not m.get("active") or m.get("closed"):
                        continue
                    outcome_prices = m.get("outcomePrices", "")
                    if isinstance(outcome_prices, str):
                        cleaned = outcome_prices.strip("[]")
                        prices = [float(p.strip().strip('"')) for p in cleaned.split(",") if p.strip()]
                    elif isinstance(outcome_prices, list):
                        prices = [float(p) for p in outcome_prices]
                    else:
                        prices = []
                    if prices and max(prices) >= 0.92:
                        skip = True
                    break
                if skip:
                    continue
            except Exception:
                pass

            candidates.append({
                "id": event_id,
                "question": question,
                "market_prob": normalized.get("market_probability", "Unknown"),
                "category": detected,
                "url": normalized.get("url", ""),
            })

        if not candidates:
            print(f"📢 No {category_filter} events, using any category")
            for event in events:
                normalized = normalize_event_for_channel(event)
                if not normalized:
                    continue
                question = normalized.get("question", "")
                if not question or len(question) < 10:
                    continue
                event_id = str(normalized.get("id", ""))
                if event_id and event_id in shown:
                    continue
                detected = agent._detect_category(question)
                if detected == "Other":
                    continue
                try:
                    markets = event.get("markets", [])
                    skip = False
                    for m in markets:
                        if not m.get("active") or m.get("closed"):
                            continue
                        outcome_prices = m.get("outcomePrices", "")
                        if isinstance(outcome_prices, str):
                            cleaned = outcome_prices.strip("[]")
                            prices = [float(p.strip().strip('"')) for p in cleaned.split(",") if p.strip()]
                        elif isinstance(outcome_prices, list):
                            prices = [float(p) for p in outcome_prices]
                        else:
                            prices = []
                        if prices and max(prices) >= 0.92:
                            skip = True
                        break
                    if skip:
                        continue
                except Exception:
                    pass
                candidates.append({
                    "id": event_id,
                    "question": question,
                    "market_prob": normalized.get("market_probability", "Unknown"),
                    "category": detected,
                    "url": normalized.get("url", ""),
                })

        if not candidates:
            print("📢 No candidates found")
            return

        market = random.choice(candidates[:10])
        question = market["question"]
        market_prob = market["market_prob"]
        category = market["category"]
        url = market["url"]

        print(f"📢 FINAL URL: {url}")

        category_emoji = {
            "Politics": "🌍", "Crypto": "💰", "Sports": "🏆",
            "Economy": "📈", "Tech": "💻", "Culture": "🎭",
            "Weather": "☁️", "Other": "📌",
        }.get(category, "📌")

        bot_link = f"https://t.me/{BOT_USERNAME}"
        text = (
            f"🔥 Горячий рынок Polymarket\n\n"
            f"📌 {question}\n\n"
            f"📊 {market_prob}\n"
            f"{category_emoji} Категория: {category}\n\n"
            f"🤖 Что думает AI?\n"
            f"Отправь ссылку боту и получи полный анализ!\n\n"
            f"👉 Анализировать → {bot_link}\n"
        )
        if url:
            text += f"🔗 Рынок → {url}"

        await telegram_bot.bot.send_message(
            CHANNEL_ID,
            text,
            disable_web_page_preview=True,
        )
        print(f"📢 Posted [{category}]: {question[:50]}")

        if market["id"]:
            shown.add(market["id"])
            if len(shown) > 200:
                shown = set(list(shown)[-200:])
            set_setting("channel_shown_markets", ",".join(filter(None, shown)))

        set_setting("last_channel_post", datetime.now(timezone.utc).isoformat())

    except Exception as e:
        print(f"📢 CHANNEL POST ERROR: {e}")
        import traceback
        traceback.print_exc()


async def channel_worker():
    """Постит в канал каждые N часов."""
    await asyncio.sleep(300)

    channel_enabled = get_setting("channel_posting_enabled", "on")
    if channel_enabled == "on" and CHANNEL_ID:
        await post_to_channel()

    while True:
        try:
            interval_hours = int(get_setting("channel_post_interval_hours", "3"))
            await asyncio.sleep(interval_hours * 3600)
            channel_enabled = get_setting("channel_posting_enabled", "on")
            if channel_enabled == "on" and CHANNEL_ID:
                await post_to_channel()
        except Exception as e:
            print(f"CHANNEL WORKER ERROR: {e}")
            await asyncio.sleep(60)


# ═══════════════════════════════════════════
# SIGNAL CACHE
# ═══════════════════════════════════════════

async def update_signal_cache():
    print("🔄 Starting signal cache update...")
    from agents.opportunity_agent import OpportunityAgent

    for category in CATEGORIES:
        try:
            print(f"🔄 Updating cache for {category}...")
            agent = OpportunityAgent()
            result = agent.run(lang="ru", limit=2, category_filter=category)

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
    await asyncio.sleep(21600)
    await update_signal_cache()

    while True:
        try:
            await asyncio.sleep(21600)
            await update_signal_cache()
        except Exception as e:
            print(f"CACHE WORKER ERROR: {e}")
            await asyncio.sleep(60)


# ═══════════════════════════════════════════
# PREDICTIONS TRACKING
# ═══════════════════════════════════════════

async def check_resolved_predictions():
    """
    Проходит по неразрешённым предсказаниям,
    проверяет через Polymarket API и обновляет метрики.
    """
    print("🎯 Checking resolved predictions...")
    try:
        predictions = get_unresolved_predictions(limit=100)
        if not predictions:
            print("🎯 No unresolved predictions to check")
            return

        print(f"🎯 Checking {len(predictions)} predictions...")
        resolved_count = 0
        skipped_count = 0
        errors_count = 0

        for pred in predictions:
            try:
                slug = pred.get("market_slug", "")
                if not slug:
                    skipped_count += 1
                    continue

                system_outcome = pred.get("system_outcome", "")
                system_probability = pred.get("system_probability", 0) or 0

                if not system_outcome or system_probability <= 0:
                    skipped_count += 1
                    continue

                result = resolve_prediction(
                    system_outcome=system_outcome,
                    system_probability=float(system_probability),
                    market_slug=slug,
                )

                if result is None:
                    skipped_count += 1
                    continue

                update_resolution(
                    prediction_id=pred["id"],
                    actual_outcome=result["actual_outcome"],
                    is_correct=result["is_correct"],
                    brier_score=result["brier_score"],
                    log_loss=result["log_loss"],
                )

                status = "✅" if result["is_correct"] else "❌"
                print(
                    f"🎯 {status} slug={slug[:30]} "
                    f"predicted={system_outcome} actual={result['actual_outcome']} "
                    f"brier={result['brier_score']:.3f}"
                )
                resolved_count += 1

                await asyncio.sleep(1)

            except Exception as e:
                errors_count += 1
                print(f"🎯 ERROR for prediction id={pred.get('id')}: {e}")
                await asyncio.sleep(0.5)

        print(
            f"🎯 Tracking done — resolved: {resolved_count}, "
            f"skipped: {skipped_count}, errors: {errors_count}"
        )
        set_setting("last_tracking_check", datetime.now(timezone.utc).isoformat())

    except Exception as e:
        print(f"🎯 TRACKING ERROR: {e}")
        import traceback
        traceback.print_exc()


async def tracking_worker():
    """Проверяет разрешённые рынки каждые 6 часов."""
    await asyncio.sleep(600)
    await check_resolved_predictions()

    while True:
        try:
            await asyncio.sleep(6 * 3600)
            tracking_enabled = get_setting("tracking_enabled", "on")
            if tracking_enabled == "on":
                await check_resolved_predictions()
            else:
                print("🎯 Tracking disabled in settings")
        except Exception as e:
            print(f"TRACKING WORKER ERROR: {e}")
            await asyncio.sleep(60)


# ═══════════════════════════════════════════
# WATCHLIST WORKER
# ═══════════════════════════════════════════

async def check_watchlist():
    """
    Проверяет все рынки в watchlist, отправляет уведомления:
    - при изменении вероятности >= threshold
    - за N часов до закрытия рынка
    - когда рынок закрылся (с результатом)
    """
    print("⭐ Checking watchlist...")
    try:
        items = get_active_watchlist_items(limit=500)
        if not items:
            print("⭐ Watchlist is empty")
            return

        print(f"⭐ Checking {len(items)} unique markets...")

        threshold = float(get_setting("watchlist_probability_threshold", "10"))
        closing_hours = int(get_setting("watchlist_closing_hours", "24"))

        checked = 0
        resolved = 0
        notifications_sent = 0
        errors = 0

        for item in items:
            try:
                slug = item.get("market_slug", "")
                if not slug:
                    continue

                # Получаем текущие данные с Polymarket
                market_data = fetch_market_by_slug(slug)
                if not market_data:
                    errors += 1
                    continue

                # Проверяем закрылся ли рынок
                if is_market_resolved(market_data):
                    await _handle_resolved_market(slug, item, market_data)
                    resolved += 1
                    continue

                # Получаем текущую вероятность
                current_prob = _get_current_probability(market_data)
                if current_prob is None:
                    errors += 1
                    continue

                # Проверяем всех подписчиков этого рынка
                subscribers = get_watchlist_subscribers(slug)
                for sub in subscribers:
                    try:
                        await _check_subscriber_notifications(
                            sub, item, current_prob, threshold, closing_hours
                        )
                        notifications_sent += 1
                    except Exception as e:
                        print(f"⭐ Notification error for user {sub.get('user_id')}: {e}")

                checked += 1
                await asyncio.sleep(1)  # пауза между рынками

            except Exception as e:
                errors += 1
                print(f"⭐ Watchlist check error for {item.get('market_slug', '')[:30]}: {e}")
                await asyncio.sleep(0.5)

        # Очищаем старые закрытые записи (раз в день примерно)
        try:
            cleaned = cleanup_old_closed_watchlist(days=30)
            if cleaned > 0:
                print(f"⭐ Cleaned up {cleaned} old closed items")
        except Exception as e:
            print(f"⭐ Cleanup error: {e}")

        print(
            f"⭐ Watchlist done — checked: {checked}, resolved: {resolved}, "
            f"notified: {notifications_sent}, errors: {errors}"
        )
        set_setting("last_watchlist_check", datetime.now(timezone.utc).isoformat())

    except Exception as e:
        print(f"⭐ WATCHLIST ERROR: {e}")
        import traceback
        traceback.print_exc()


def _get_current_probability(market_data: dict) -> float:
    """Извлекает текущую вероятность лидера из market_data."""
    try:
        outcome_prices = market_data.get("outcomePrices", "")
        if isinstance(outcome_prices, str):
            cleaned = outcome_prices.strip("[]")
            prices = [float(p.strip().strip('"')) for p in cleaned.split(",") if p.strip()]
        elif isinstance(outcome_prices, list):
            prices = [float(p) for p in outcome_prices]
        else:
            return None

        if not prices:
            return None

        return max(prices) * 100  # возвращаем в процентах
    except Exception:
        return None


async def _check_subscriber_notifications(
    sub: dict, item: dict, current_prob: float,
    threshold: float, closing_hours: int
) -> None:
    """Проверяет нужно ли отправить уведомление подписчику."""
    user_id = sub["user_id"]
    watchlist_id = sub["id"]
    initial_prob = sub.get("initial_probability", 0)

    # Обновляем last_checked_probability всегда
    update_watchlist_probability(watchlist_id, current_prob)

    if not sub.get("notify_enabled"):
        return  # уведомления отключены

    question = item.get("question", "")
    url = item.get("market_url", "")

    # Проверка 1: изменение вероятности >= threshold
    change = current_prob - initial_prob
    abs_change = abs(change)

    if abs_change >= threshold and not sub.get("notified_change"):
        direction = "📈" if change > 0 else "📉"
        text = (
            f"{direction} Watchlist — изменение рынка!\n\n"
            f"📌 {question}\n\n"
            f"Было: {initial_prob:.1f}%\n"
            f"Стало: {current_prob:.1f}%\n"
            f"Изменение: {'+' if change > 0 else ''}{change:.1f}%\n\n"
            f"🔗 {url}"
        )
        try:
            await telegram_bot.bot.send_message(user_id, text, disable_web_page_preview=True)
            # Сбрасываем базу чтобы следить за новыми изменениями от текущей точки
            reset_watchlist_change_notification(watchlist_id, current_prob)
        except Exception as e:
            print(f"⭐ Failed to notify {user_id} about change: {e}")

    # Проверка 2: скорое закрытие
    end_date = sub.get("market_end_date") or item.get("market_end_date")
    if end_date and not sub.get("notified_closing_soon"):
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            hours_left = (end_dt - now).total_seconds() / 3600

            if 0 < hours_left <= closing_hours:
                text = (
                    f"⏰ Watchlist — рынок скоро закроется!\n\n"
                    f"📌 {question}\n\n"
                    f"Осталось: ~{int(hours_left)} часов\n"
                    f"Текущая вероятность: {current_prob:.1f}%\n\n"
                    f"🔗 {url}"
                )
                await telegram_bot.bot.send_message(user_id, text, disable_web_page_preview=True)
                mark_watchlist_notified(watchlist_id, "closing_soon")
        except Exception as e:
            print(f"⭐ Failed to check closing date for {user_id}: {e}")


async def _handle_resolved_market(slug: str, item: dict, market_data: dict) -> None:
    """Обрабатывает закрытие рынка — уведомляет всех подписчиков."""
    try:
        from services.polymarket_resolver import extract_actual_outcome

        subscribers = get_watchlist_subscribers(slug)
        if not subscribers:
            close_watchlist_market(slug)
            return

        actual_outcome = extract_actual_outcome(market_data)
        question = item.get("question", "")
        url = item.get("market_url", "")

        for sub in subscribers:
            if not sub.get("notify_enabled"):
                continue
            if sub.get("notified_resolved"):
                continue

            try:
                text = (
                    f"🎯 Watchlist — рынок закрылся!\n\n"
                    f"📌 {question}\n\n"
                    f"Результат: {actual_outcome or 'неизвестен'}\n\n"
                    f"🔗 {url}\n\n"
                    f"Рынок удалён из watchlist."
                )
                await telegram_bot.bot.send_message(
                    sub["user_id"], text, disable_web_page_preview=True
                )
                mark_watchlist_notified(sub["id"], "resolved")
            except Exception as e:
                print(f"⭐ Failed to notify {sub.get('user_id')} about resolution: {e}")

        # Отмечаем рынок как закрытый (больше не проверяем)
        close_watchlist_market(slug)
        print(f"⭐ Market resolved: {slug[:40]} -> {actual_outcome}")

    except Exception as e:
        print(f"⭐ _handle_resolved_market error: {e}")


async def watchlist_worker():
    """Проверяет watchlist каждые N часов."""
    await asyncio.sleep(900)  # первый запуск через 15 минут
    watchlist_enabled = get_setting("watchlist_enabled", "on")
    if watchlist_enabled == "on":
        await check_watchlist()

    while True:
        try:
            interval_hours = int(get_setting("watchlist_check_interval_hours", "3"))
            await asyncio.sleep(interval_hours * 3600)

            watchlist_enabled = get_setting("watchlist_enabled", "on")
            if watchlist_enabled == "on":
                await check_watchlist()
            else:
                print("⭐ Watchlist disabled in settings")
        except Exception as e:
            print(f"WATCHLIST WORKER ERROR: {e}")
            await asyncio.sleep(60)


# ═══════════════════════════════════════════
# TON PAYMENTS
# ═══════════════════════════════════════════

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
                    tokens = calculate_tokens_for_amount(ton_amount)
                    if tokens <= 0:
                        continue

                    new_balance = add_tokens(user_id, tokens)

                    save_transaction(
                        tx_hash, user_id, ton_amount, tokens,
                        referral_bonus_ton=referral_bonus_ton,
                        referrer_id=referrer_id,
                    )

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


# ═══════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════

async def send_daily_notifications():
    try:
        print("📢 Starting daily notifications...")

        all_users = get_all_users(limit=10000)
        subscribed_ids = {u["user_id"] for u in get_subscribed_users()}

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


# ═══════════════════════════════════════════
# POLLING + MAIN
# ═══════════════════════════════════════════

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
    asyncio.create_task(channel_worker())
    asyncio.create_task(tracking_worker())
    asyncio.create_task(watchlist_worker())
    asyncio.create_task(cache_worker())
    await run_polling()


if __name__ == "__main__":
    asyncio.run(main())

