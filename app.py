import sys
import os
import asyncio
import random
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telegram_bot
from bot.admin import register_admin
from services.ton_service import get_transactions, parse_payment
from services.watchlist_ai_summary_service import build_watchlist_ai_summary, format_watchlist_ai_summary
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
    charge_watchlist_event,
    cleanup_old_closed_watchlist,
    # ═══ NEW: Authors / Donations / Watchlist slots ═══
    set_author_status, add_watchlist_extra_slots,
    complete_donation, get_donation, get_author_profile,
)

register_admin(telegram_bot.dp)

CATEGORIES = ["Politics", "Crypto", "Sports", "Economy", "Tech"]
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "DeepAlphaAI_bot")


def _watchlist_ai_summary_enabled() -> bool:
    return str(get_setting("watchlist_ai_summary_enabled", "on") or "on").strip().lower() == "on"



def format_watchlist_charge_footer(charged_result: dict, lang: str = "ru") -> str:
    if not charged_result:
        return ""
    reason = charged_result.get("reason")
    if charged_result.get("charged"):
        if lang == "ru":
            return f"\n\n💳 Списано: {charged_result.get('cost', 0)} токенов\n💰 Баланс: {charged_result.get('balance', 0)} токена"
        return f"\n\n💳 Charged: {charged_result.get('cost', 0)} tokens\n💰 Balance: {charged_result.get('balance', 0)} tokens"
    if reason == "vip":
        return "\n\n💎 VIP: токены не списаны" if lang == "ru" else "\n\n💎 VIP: no tokens charged"
    return ""


def get_watchlist_pause_keyboard(watchlist_id: int) -> "telegram_bot.InlineKeyboardMarkup":
    kb = telegram_bot.InlineKeyboardMarkup(row_width=1)
    kb.add(telegram_bot.InlineKeyboardButton("💎 Buy tokens / cashier", callback_data="buy_tokens"))
    kb.add(telegram_bot.InlineKeyboardButton("▶️ Resume watcher", callback_data=f"watchlist_resume:{watchlist_id}"))
    kb.add(telegram_bot.InlineKeyboardButton("🗑 Remove from watchlist", callback_data=f"wl_remove_{watchlist_id}"))
    return kb


async def send_watchlist_pause_message(user_id: int, watchlist_id: int, question: str) -> None:
    text = (
        "⏸ Watchlist Autopilot paused\n\n"
        "Рынок:\n"
        f"{question}\n\n"
        "Причина:\n"
        "Недостаточно токенов для AI Watchlist alerts.\n\n"
        "Пополните баланс и нажмите Resume, чтобы DeepAlpha продолжил следить за рынком."
    )
    await telegram_bot.bot.send_message(user_id, text, reply_markup=get_watchlist_pause_keyboard(watchlist_id))


def get_watchlist_buy_tokens_keyboard() -> "telegram_bot.InlineKeyboardMarkup":
    kb = telegram_bot.InlineKeyboardMarkup(row_width=1)
    kb.add(telegram_bot.InlineKeyboardButton("💎 Buy tokens / cashier", callback_data="buy_tokens"))
    return kb


async def send_watchlist_resolved_insufficient_tokens_message(
    user_id: int,
    question: str,
    actual_outcome: str | None,
    url: str,
    lang: str = "ru",
) -> None:
    if lang == "en":
        text = (
            "🎯 Watchlist — market resolved\n\n"
            f"📌 {question}\n\n"
            f"Result: {actual_outcome or 'unknown'}\n\n"
            "Full paid recap was not sent because there were not enough tokens.\n"
            "The market was closed and removed from your watchlist."
        )
    else:
        text = (
            "🎯 Watchlist — рынок закрылся\n\n"
            f"📌 {question}\n\n"
            f"Результат: {actual_outcome or 'неизвестен'}\n\n"
            "Полный paid recap не отправлен: недостаточно токенов.\n"
            "Рынок закрыт и удалён из watchlist."
        )
    if url:
        text = f"{text}\n\n🔗 {url}"
    await telegram_bot.bot.send_message(
        user_id,
        text,
        disable_web_page_preview=True,
        reply_markup=get_watchlist_buy_tokens_keyboard(),
    )


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

def channel_posting_disabled_reason() -> str | None:
    disabled_values = {"off", "false", "0", "no", "disabled"}
    enabled_values = {"on", "true", "1", "yes", "enabled"}

    env_value = str(os.getenv("CHANNEL_POSTING_DISABLED", "")).strip().lower()
    if env_value in {"true", "1", "on", "yes", "disabled", "off"}:
        return "env_disabled"

    if not CHANNEL_ID:
        return "no_channel_id"

    value = str(get_setting("channel_posting_enabled", "on")).strip().lower()
    if value in disabled_values:
        return "db_disabled"
    if value in enabled_values:
        return None
    return "db_unknown_disabled"


def is_channel_posting_enabled() -> bool:
    return channel_posting_disabled_reason() is None


async def safe_send_channel_message(text: str, **kwargs):
    reason = channel_posting_disabled_reason()
    if reason:
        print(f"📢 CHANNEL SEND BLOCKED reason={reason}")
        print(f"channel_posting_blocked reason={reason}")
        return {"ok": False, "reason": reason}
    await telegram_bot.bot.send_message(CHANNEL_ID, text, **kwargs)
    return {"ok": True, "reason": "sent"}


def log_channel_diagnostics_once():
    db_value = get_setting("channel_posting_enabled", "on")
    enabled = channel_posting_disabled_reason() is None
    print(
        "📢 CHANNEL DIAGNOSTICS "
        f"channel_id_set={'yes' if CHANNEL_ID else 'no'} "
        f"env_disabled={os.getenv('CHANNEL_POSTING_DISABLED', '')} "
        f"db={db_value} "
        f"enabled={str(enabled).lower()} "
        f"commit={os.getenv('RAILWAY_GIT_COMMIT_SHA', '')} "
        f"service={os.getenv('RAILWAY_SERVICE_NAME', '')} "
        f"environment={os.getenv('RAILWAY_ENVIRONMENT_NAME', '')}"
    )


async def post_to_channel(force: bool = False):
    """Постит рандомный рынок Polymarket в канал."""
    disabled_reason = channel_posting_disabled_reason()
    if disabled_reason:
        print(f"📢 Channel posting disabled, skip force={force} reason={disabled_reason}")
        print(f"channel_posting_blocked reason={disabled_reason} force={force}")
        return {"ok": False, "reason": disabled_reason}

    try:
        shown_str = get_setting("channel_shown_markets", "")
        shown = set(shown_str.split(",")) if shown_str else set()

        last_category = get_setting("channel_last_category", "")
        available_categories = [c for c in CATEGORIES if c != last_category]
        if not available_categories:
            available_categories = CATEGORIES
        category_filter = random.choice(available_categories)
        set_setting("channel_last_category", category_filter)
        print(f"📢 Chose category: {category_filter}")

        events = list_events(limit=50)
        if not events:
            print("📢 No events from Polymarket")
            return {"ok": False, "reason": "no_events"}

        from agents.news_agent import NewsAgent
        agent = NewsAgent()
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
            return {"ok": False, "reason": "no_candidates"}

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

        sent = await safe_send_channel_message(text, disable_web_page_preview=True)
        if not sent.get("ok"):
            return sent
        print(f"📢 Posted [{category}]: {question[:50]}")

        if market["id"]:
            shown.add(market["id"])
            if len(shown) > 200:
                shown = set(list(shown)[-200:])
            set_setting("channel_shown_markets", ",".join(filter(None, shown)))

        set_setting("last_channel_post", datetime.now(timezone.utc).isoformat())
        return {"ok": True, "reason": "sent"}

    except Exception as e:
        print(f"📢 CHANNEL POST ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"ok": False, "reason": "error"}


async def channel_worker():
    """Постит в канал каждые N часов."""
    await asyncio.sleep(300)

    disabled_reason = channel_posting_disabled_reason()
    if disabled_reason:
        print(f"📢 Channel worker blocked reason={disabled_reason}")
    else:
        await post_to_channel()

    while True:
        try:
            interval_hours = int(get_setting("channel_post_interval_hours", "3"))
            await asyncio.sleep(interval_hours * 3600)
            disabled_reason = channel_posting_disabled_reason()
            if disabled_reason:
                print(f"📢 Channel worker blocked reason={disabled_reason}")
            else:
                await post_to_channel()
        except Exception as e:
            print(f"CHANNEL WORKER ERROR: {e}")
            await asyncio.sleep(60)


# ═══════════════════════════════════════════
# SIGNAL CACHE
# ═══════════════════════════════════════════

async def update_signal_cache(cycle_id=None):
    print("🔄 Starting signal cache update...")
    from agents.opportunity_agent import OpportunityAgent

    for category in CATEGORIES:
        try:
            print(f"🔄 Updating cache for {category}...")
            agent = OpportunityAgent()
            request_id = __import__("uuid").uuid4().hex
            result = agent.run(lang="ru", limit=2, category_filter=category, is_background=True, cycle_id=cycle_id, job_id=f"signal:{category}", request_id=request_id)

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
    import os, uuid
    if os.getenv("SIGNAL_CACHE_WORKER_ENABLED", "false").lower() not in {"1","true","yes","on"}:
        print("SIGNAL_CACHE_WORKER disabled by env")
        return
    await asyncio.sleep(int(os.getenv("SIGNAL_CACHE_INITIAL_DELAY_SECONDS", "300")))
    from db.database import acquire_distributed_lock, release_distributed_lock
    owner = os.getenv("RAILWAY_REPLICA_ID") or os.getenv("HOSTNAME") or str(uuid.uuid4())
    cycle_id = str(uuid.uuid4())
    if acquire_distributed_lock("signal_cache_worker", owner, 3600):
        try:
            await update_signal_cache(cycle_id=cycle_id)
        finally:
            release_distributed_lock("signal_cache_worker", owner)

    while True:
        try:
            await asyncio.sleep(21600)
            cycle_id = str(uuid.uuid4())
            if acquire_distributed_lock("signal_cache_worker", owner, 3600):
                try:
                    await update_signal_cache(cycle_id=cycle_id)
                finally:
                    release_distributed_lock("signal_cache_worker", owner)
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

                market_data = fetch_market_by_slug(slug)
                if not market_data:
                    errors += 1
                    continue

                if is_market_resolved(market_data):
                    await _handle_resolved_market(slug, item, market_data)
                    resolved += 1
                    continue

                current_prob = _get_current_probability(market_data)
                if current_prob is None:
                    errors += 1
                    continue

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
                await asyncio.sleep(1)

            except Exception as e:
                errors += 1
                print(f"⭐ Watchlist check error for {item.get('market_slug', '')[:30]}: {e}")
                await asyncio.sleep(0.5)

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

        return max(prices) * 100
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

    update_watchlist_probability(watchlist_id, current_prob)

    if not sub.get("notify_enabled"):
        return

    question = item.get("question", "")
    url = item.get("market_url", "")

    change = current_prob - initial_prob
    abs_change = abs(change)

    if abs_change >= threshold and not sub.get("notified_change"):
        direction = "📈" if change > 0 else "📉"
        fingerprint = f"{watchlist_id}:{round(current_prob, 1)}:{round(initial_prob, 1)}:{round(threshold, 1)}"
        charge = charge_watchlist_event(user_id, watchlist_id, item.get("market_slug", ""), "probability_change", fingerprint)
        if charge.get("reason") == "insufficient_tokens":
            try:
                await send_watchlist_pause_message(user_id, watchlist_id, question)
            except Exception as e:
                print(f"⭐ Failed to notify {user_id} about watchlist pause: {e}")
            return
        ai_block = ""
        if _watchlist_ai_summary_enabled():
            ai_summary = build_watchlist_ai_summary(
                event_type="probability_change",
                question=question,
                market_slug=item.get("market_slug", ""),
                market_url=url,
                initial_probability=initial_prob,
                current_probability=current_prob,
                probability_change=change,
            )
            ai_block = format_watchlist_ai_summary(ai_summary)
        text = (
            f"{direction} Watchlist — изменение рынка!\n\n"
            f"📌 {question}\n\n"
            f"Было: {initial_prob:.1f}%\n"
            f"Стало: {current_prob:.1f}%\n"
            f"Изменение: {'+' if change > 0 else ''}{change:.1f}%\n\n"
            f"🔗 {url}"
            f"{ai_block}"
            f"{format_watchlist_charge_footer(charge)}"
        )
        try:
            await telegram_bot.bot.send_message(user_id, text, disable_web_page_preview=True)
            reset_watchlist_change_notification(watchlist_id, current_prob)
        except Exception as e:
            print(f"⭐ Failed to notify {user_id} about change: {e}")

    end_date = sub.get("market_end_date") or item.get("market_end_date")
    if end_date and not sub.get("notified_closing_soon"):
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            hours_left = (end_dt - now).total_seconds() / 3600

            if 0 < hours_left <= closing_hours:
                fingerprint = f"{item.get('market_slug', '')}:{end_date}:{closing_hours}"
                charge = charge_watchlist_event(user_id, watchlist_id, item.get("market_slug", ""), "closing_soon", fingerprint)
                if charge.get("reason") == "insufficient_tokens":
                    try:
                        await send_watchlist_pause_message(user_id, watchlist_id, question)
                    except Exception as e:
                        print(f"⭐ Failed to notify {user_id} about watchlist pause: {e}")
                    return
                ai_block = ""
                if _watchlist_ai_summary_enabled():
                    ai_summary = build_watchlist_ai_summary(
                        event_type="closing_soon",
                        question=question,
                        market_slug=item.get("market_slug", ""),
                        market_url=url,
                        initial_probability=initial_prob,
                        current_probability=current_prob,
                        closing_hours=int(hours_left),
                    )
                    ai_block = format_watchlist_ai_summary(ai_summary)
                text = (
                    f"⏰ Watchlist — рынок скоро закроется!\n\n"
                    f"📌 {question}\n\n"
                    f"Осталось: ~{int(hours_left)} часов\n"
                    f"Текущая вероятность: {current_prob:.1f}%\n\n"
                    f"🔗 {url}"
                    f"{ai_block}"
                    f"{format_watchlist_charge_footer(charge)}"
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
                fingerprint = f"{slug}:{actual_outcome or 'unknown'}"
                charge = charge_watchlist_event(
                    sub["user_id"], sub["id"], slug, "resolved_recap", fingerprint
                )
                if charge.get("reason") == "insufficient_tokens":
                    try:
                        await send_watchlist_resolved_insufficient_tokens_message(
                            sub["user_id"], question, actual_outcome, url
                        )
                        mark_watchlist_notified(sub["id"], "resolved")
                    except Exception as e:
                        print(f"⭐ Failed to notify {sub.get('user_id')} about resolved insufficient tokens: {e}")
                    continue
                ai_block = ""
                if _watchlist_ai_summary_enabled():
                    ai_summary = build_watchlist_ai_summary(
                        event_type="resolved_recap",
                        question=question,
                        market_slug=slug,
                        market_url=url,
                        actual_outcome=actual_outcome,
                    )
                    ai_block = format_watchlist_ai_summary(ai_summary)
                text = (
                    f"🎯 Watchlist — рынок закрылся!\n\n"
                    f"📌 {question}\n\n"
                    f"Результат: {actual_outcome or 'неизвестен'}\n\n"
                    f"🔗 {url}\n\n"
                    f"Рынок удалён из watchlist."
                    f"{ai_block}"
                    f"{format_watchlist_charge_footer(charge)}"
                )
                await telegram_bot.bot.send_message(
                    sub["user_id"], text, disable_web_page_preview=True
                )
                mark_watchlist_notified(sub["id"], "resolved")
            except Exception as e:
                print(f"⭐ Failed to notify {sub.get('user_id')} about resolution: {e}")

        close_watchlist_market(slug)
        print(f"⭐ Market resolved: {slug[:40]} -> {actual_outcome}")

    except Exception as e:
        print(f"⭐ _handle_resolved_market error: {e}")


async def watchlist_worker():
    """Проверяет watchlist каждые N часов."""
    import os, uuid
    if os.getenv("WATCHLIST_WORKER_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        print("WATCHLIST_WORKER disabled by env")
        return
    from db.database import acquire_distributed_lock, release_distributed_lock
    owner = os.getenv("RAILWAY_REPLICA_ID") or os.getenv("HOSTNAME") or str(uuid.uuid4())

    async def _run_locked_once():
        if acquire_distributed_lock("watchlist_worker", owner, 3600):
            try:
                await check_watchlist()
            finally:
                release_distributed_lock("watchlist_worker", owner)

    await asyncio.sleep(900)
    if get_setting("watchlist_enabled", "on") == "on":
        await _run_locked_once()

    while True:
        try:
            interval_hours = int(get_setting("watchlist_check_interval_hours", "3"))
            await asyncio.sleep(interval_hours * 3600)
            if get_setting("watchlist_enabled", "on") == "on":
                await _run_locked_once()
            else:
                print("⭐ Watchlist disabled in settings")
        except Exception as e:
            print(f"WATCHLIST WORKER ERROR: {e}")
            await asyncio.sleep(60)


# ═══════════════════════════════════════════
# Gram PAYMENTS — обрабатывает 5 типов:
#   - tokens              → начисление токенов
#   - subscription        → активация подписки
#   - author_status       → выдача статуса автора
#   - watchlist_slots     → добавление +N слотов
#   - donation:<id>       → завершение доната
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

                # Поиск user_id + payment_type:
                # сперва через parse_payment (комментарий к транзакции),
                # затем через pending по времени и сумме
                if payment and payment.get("user_id"):
                    user_id = payment["user_id"]
                    # Даже если нашли через комментарий — проверим pending для payment_type
                    p = pending.get(user_id)
                    if p:
                        payment_type = p.get("payment_type", "tokens")
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

                # ═══ РЕФЕРАЛЬНЫЙ БОНУС (только для tokens/subscription) ═══
                # Для author_status/watchlist_slots/donation не даём рефералку
                referral_bonus_ton = 0
                referrer_id = None

                is_token_or_sub = payment_type in ("tokens", "subscription")

                if is_token_or_sub:
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
                                f"💎 Его покупка: {ton_amount:.2f} Gram\n"
                                f"🎁 Ваш бонус: {referral_bonus_ton:.4f} Gram "
                                f"({referral_tokens} токенов)"
                            )
                        except Exception as e:
                            print(f"REFERRAL NOTIFY ERROR: {e}")

                # ═══ ВЕТВЛЕНИЕ ПО ТИПУ ПЛАТЕЖА ═══

                if payment_type == "subscription":
                    # Активация подписки
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
                            f"💎 Оплачено: {ton_amount:.2f} Gram\n"
                            f"📅 Действует до: {until[:10]}\n\n"
                            f"Теперь у тебя:\n"
                            f"• 🔔 Ежедневные сигналы\n"
                            f"• 📊 До 15 анализов в день\n"
                            f"• 💡 До 3 сигналов в день\n"
                            f"• ⭐ Watchlist бесплатно"
                        )
                    except Exception as e:
                        print(f"SUB NOTIFY ERROR: {e}")

                elif payment_type == "author_status":
                    # Выдача статуса автора
                    set_author_status(user_id, True)

                    save_transaction(
                        tx_hash, user_id, ton_amount, 0,
                        referral_bonus_ton=0, referrer_id=None,
                    )

                    try:
                        await telegram_bot.bot.send_message(
                            user_id,
                            f"🎉 Поздравляем! Ты теперь Автор DeepAlpha!\n\n"
                            f"💎 Оплачено: {ton_amount:.2f} Gram\n\n"
                            f"Теперь ты можешь:\n"
                            f"• 📝 Публиковать свои прогнозы\n"
                            f"• 👥 Получать подписчиков\n"
                            f"• 💝 Получать донаты в Gram от юзеров\n\n"
                            f"Настрой профиль: /profile\n"
                            f"Установи bio: /edit_bio\n"
                            f"Добавь Gram кошелёк для выплат: /set_wallet"
                        )
                    except Exception as e:
                        print(f"AUTHOR STATUS NOTIFY ERROR: {e}")

                elif payment_type == "watchlist_slots":
                    # Покупка доп. слотов Watchlist
                    slots_count = int(get_setting("watchlist_extra_slots_count", "5"))
                    new_total = add_watchlist_extra_slots(user_id, slots_count)

                    save_transaction(
                        tx_hash, user_id, ton_amount, 0,
                        referral_bonus_ton=0, referrer_id=None,
                    )

                    try:
                        await telegram_bot.bot.send_message(
                            user_id,
                            f"✅ Доп. слоты Watchlist добавлены!\n\n"
                            f"💎 Оплачено: {ton_amount:.2f} Gram\n"
                            f"➕ Добавлено: {slots_count} слотов\n"
                            f"📊 Всего доп. слотов: {new_total}\n\n"
                            f"Теперь ты можешь добавить больше рынков!\n"
                            f"📋 /watchlist"
                        )
                    except Exception as e:
                        print(f"WATCHLIST SLOTS NOTIFY ERROR: {e}")

                elif payment_type.startswith("donation:"):
                    # Завершение доната автору
                    try:
                        donation_id = int(payment_type.split(":", 1)[1])
                    except (ValueError, IndexError):
                        print(f"⚠️ Invalid donation payment_type: {payment_type}")
                        delete_pending(user_id)
                        continue

                    donation = get_donation(donation_id)
                    if not donation:
                        print(f"⚠️ Donation {donation_id} not found, user {user_id}")
                        delete_pending(user_id)
                        continue

                    author_id = donation["author_id"]
                    post_id = donation.get("post_id")
                    comment = donation.get("comment", "") or ""

                    success = complete_donation(donation_id, tx_hash)

                    if not success:
                        print(f"⚠️ Failed to complete donation {donation_id}")
                        delete_pending(user_id)
                        continue

                    save_transaction(
                        tx_hash, user_id, ton_amount, 0,
                        referral_bonus_ton=0, referrer_id=None,
                    )

                    # Получаем актуальные данные после завершения
                    updated_donation = get_donation(donation_id)
                    author_received = updated_donation.get("author_received_ton", 0) if updated_donation else 0
                    platform_fee = updated_donation.get("platform_fee_ton", 0) if updated_donation else 0

                    # Уведомление донору
                    try:
                        author = get_author_profile(author_id)
                        author_name = (
                            author.get("username") or author.get("first_name") or str(author_id)
                        ) if author else str(author_id)

                        donor_text = (
                            f"💝 Донат отправлен!\n\n"
                            f"👤 Автору: @{author_name}\n"
                            f"💎 Сумма: {ton_amount:.4f} Gram\n"
                        )
                        if post_id:
                            donor_text += f"📝 Пост: /post_{post_id}\n"
                        donor_text += f"\nСпасибо за поддержку автора! 🙏"

                        await telegram_bot.bot.send_message(user_id, donor_text)
                    except Exception as e:
                        print(f"DONATION DONOR NOTIFY ERROR: {e}")

                    # Уведомление автору
                    try:
                        donor = get_user(user_id)
                        donor_name = (
                            donor.get("username") or donor.get("first_name") or str(user_id)
                        ) if donor else str(user_id)

                        author_text = (
                            f"💝 Ты получил донат!\n\n"
                            f"👤 От: @{donor_name}\n"
                            f"💎 Сумма доната: {ton_amount:.4f} Gram\n"
                            f"🏦 Комиссия платформы: {platform_fee:.4f} Gram\n"
                            f"💰 Тебе зачислено: {author_received:.4f} Gram\n"
                        )
                        if post_id:
                            author_text += f"📝 За пост: /post_{post_id}\n"
                        if comment:
                            author_text += f"\n💬 Комментарий:\n{comment[:300]}\n"
                        author_text += f"\n💰 Проверить баланс: нажми 💰 Баланс автора"

                        await telegram_bot.bot.send_message(author_id, author_text)
                    except Exception as e:
                        print(f"DONATION AUTHOR NOTIFY ERROR: {e}")

                else:
                    # Дефолт: tokens
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
                            f"💎 Gram: {ton_amount:.4f}\n"
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
    telegram_bot.initialize_database_once()
    log_channel_diagnostics_once()

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
