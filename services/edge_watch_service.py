import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from db.database import (
    acquire_distributed_lock,
    charge_watchlist_event,
    get_active_watchlist_items,
    get_connection,
    get_watchlist_subscribers,
    release_distributed_lock,
)
from services.polymarket_resolver import fetch_market_by_slug, is_market_resolved

logger = logging.getLogger(__name__)

WATCH_EDGE_THRESHOLD_PP = 5.0
BUY_EDGE_THRESHOLD_PP = 8.0
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_STARTUP_DELAY_SECONDS = 75


@dataclass(frozen=True)
class EdgeSnapshot:
    side: str
    fair_probability: float
    market_probability: float
    edge_pp: float
    confidence: str
    decision: str
    independent: bool
    analysis_id: Optional[int] = None


def env_true(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def normalize_confidence(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"high", "высокая", "высокий"}:
        return "high"
    if raw in {"medium", "средняя", "средний"}:
        return "medium"
    if raw in {"low", "низкая", "низкий"}:
        return "low"
    return "none"


def parse_probability_text(value: Any) -> Tuple[str, Optional[float]]:
    text = str(value or "").strip()
    if not text:
        return "", None

    outcome_match = re.search(
        r"\b(YES|NO|Yes|No|ДА|НЕТ|Да|Нет)\b[^0-9]{0,12}([0-9]+(?:[.,][0-9]+)?)\s*%",
        text,
    )
    if outcome_match:
        raw_side = outcome_match.group(1).lower()
        side = "YES" if raw_side in {"yes", "да"} else "NO"
        return side, float(outcome_match.group(2).replace(",", "."))

    number_match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*%", text)
    if number_match:
        return "", float(number_match.group(1).replace(",", "."))
    return "", None


def parse_market_probability_text(value: Any, side: str) -> Optional[float]:
    text = str(value or "")
    side = str(side or "").upper()
    aliases = ("YES", "Yes", "ДА", "Да") if side == "YES" else ("NO", "No", "НЕТ", "Нет")
    for alias in aliases:
        match = re.search(
            rf"\b{re.escape(alias)}\b[^0-9]{{0,12}}([0-9]+(?:[.,][0-9]+)?)\s*%",
            text,
        )
        if match:
            return float(match.group(1).replace(",", "."))
    return None


def classify_decision(edge_pp: float, confidence: str, independent: bool = True) -> str:
    if not independent or edge_pp < WATCH_EDGE_THRESHOLD_PP:
        return "NO_TRADE"
    if edge_pp > BUY_EDGE_THRESHOLD_PP and confidence in {"medium", "high"}:
        return "BUY"
    return "WATCH"


def build_snapshot(
    *,
    side: str,
    fair_probability: float,
    market_probability: float,
    confidence: str,
    independent: bool,
    analysis_id: Optional[int] = None,
) -> EdgeSnapshot:
    side = str(side or "").upper()
    edge_pp = round(float(fair_probability) - float(market_probability), 2)
    confidence = normalize_confidence(confidence)
    return EdgeSnapshot(
        side=side,
        fair_probability=round(float(fair_probability), 2),
        market_probability=round(float(market_probability), 2),
        edge_pp=edge_pp,
        confidence=confidence,
        decision=classify_decision(edge_pp, confidence, independent),
        independent=bool(independent),
        analysis_id=int(analysis_id) if analysis_id is not None else None,
    )


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return [part.strip().strip('"\'') for part in raw.strip("[]").split(",") if part.strip()]
    return []


def extract_side_price(market_data: Dict[str, Any], side: str) -> Optional[float]:
    outcomes = [str(x).strip().upper() for x in _as_list((market_data or {}).get("outcomes"))]
    prices: List[float] = []
    for raw in _as_list((market_data or {}).get("outcomePrices")):
        try:
            prices.append(float(raw) * 100.0)
        except (TypeError, ValueError):
            prices.append(float("nan"))

    target = str(side or "").strip().upper()
    for idx, outcome in enumerate(outcomes):
        if outcome == target and idx < len(prices):
            price = prices[idx]
            return None if price != price else round(price, 4)

    if len(prices) == 2 and target in {"YES", "NO"}:
        idx = 0 if target == "YES" else 1
        price = prices[idx]
        return None if price != price else round(price, 4)
    return None


def init_edge_watch_schema() -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist_edge_state (
                watchlist_id BIGINT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                market_slug TEXT NOT NULL,
                side TEXT NOT NULL,
                fair_probability DOUBLE PRECISION NOT NULL,
                market_probability DOUBLE PRECISION NOT NULL,
                edge_pp DOUBLE PRECISION NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'none',
                decision TEXT NOT NULL DEFAULT 'NO_TRADE',
                source_analysis_id BIGINT NULL,
                last_notified_decision TEXT NULL,
                last_notification_fingerprint TEXT NULL,
                initialized_at TIMESTAMP NOT NULL DEFAULT NOW(),
                last_checked_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_watchlist_edge_state_slug ON watchlist_edge_state(market_slug)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_watchlist_edge_state_user ON watchlist_edge_state(user_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_latest_analysis(user_id: int, market_slug: str, question: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        slug_like = f"%{str(market_slug or '').strip()}%"
        cur.execute(
            """
            SELECT id, user_id, url, question, market_probability, system_probability,
                   confidence, reasoning, conclusion, created_at
            FROM analyses
            WHERE (user_id = %s OR user_id = 0)
              AND (
                    (%s <> '%%' AND COALESCE(url, '') ILIKE %s)
                    OR COALESCE(question, '') = %s
                  )
            ORDER BY CASE WHEN user_id = %s THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (int(user_id), slug_like, slug_like, str(question or ""), int(user_id)),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))
    except Exception as exc:
        logger.warning("EDGE_WATCH_ANALYSIS_LOOKUP_FAILED type=%s", exc.__class__.__name__)
        return None
    finally:
        cur.close()
        conn.close()


def snapshot_from_analysis(
    analysis: Dict[str, Any], market_data: Dict[str, Any]
) -> Optional[EdgeSnapshot]:
    side, fair_probability = parse_probability_text((analysis or {}).get("system_probability"))
    if side not in {"YES", "NO"} or fair_probability is None:
        return None

    current_market_probability = extract_side_price(market_data, side)
    if current_market_probability is None:
        return None

    stored_market_probability = parse_market_probability_text(
        (analysis or {}).get("market_probability"), side
    )
    independent = True
    if stored_market_probability is not None and abs(fair_probability - stored_market_probability) < 0.05:
        independent = False

    return build_snapshot(
        side=side,
        fair_probability=fair_probability,
        market_probability=current_market_probability,
        confidence=(analysis or {}).get("confidence"),
        independent=independent,
        analysis_id=(analysis or {}).get("id"),
    )


def get_edge_state(watchlist_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM watchlist_edge_state WHERE watchlist_id = %s", (int(watchlist_id),))
        row = cur.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))
    finally:
        cur.close()
        conn.close()


def upsert_edge_state(
    *,
    watchlist_id: int,
    user_id: int,
    market_slug: str,
    snapshot: EdgeSnapshot,
    last_notified_decision: Optional[str] = None,
    last_notification_fingerprint: Optional[str] = None,
) -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO watchlist_edge_state (
                watchlist_id, user_id, market_slug, side, fair_probability,
                market_probability, edge_pp, confidence, decision,
                source_analysis_id, last_notified_decision,
                last_notification_fingerprint, initialized_at,
                last_checked_at, updated_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW(),NOW())
            ON CONFLICT (watchlist_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                market_slug = EXCLUDED.market_slug,
                side = EXCLUDED.side,
                fair_probability = EXCLUDED.fair_probability,
                market_probability = EXCLUDED.market_probability,
                edge_pp = EXCLUDED.edge_pp,
                confidence = EXCLUDED.confidence,
                decision = EXCLUDED.decision,
                source_analysis_id = EXCLUDED.source_analysis_id,
                last_notified_decision = COALESCE(EXCLUDED.last_notified_decision, watchlist_edge_state.last_notified_decision),
                last_notification_fingerprint = COALESCE(EXCLUDED.last_notification_fingerprint, watchlist_edge_state.last_notification_fingerprint),
                last_checked_at = NOW(),
                updated_at = NOW()
            """,
            (
                int(watchlist_id), int(user_id), str(market_slug or ""), snapshot.side,
                snapshot.fair_probability, snapshot.market_probability, snapshot.edge_pp,
                snapshot.confidence, snapshot.decision, snapshot.analysis_id,
                last_notified_decision, last_notification_fingerprint,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def decision_rank(value: Any) -> int:
    return {"NO_TRADE": 0, "WATCH": 1, "BUY": 2}.get(str(value or "").upper(), 0)


def transition_fingerprint(
    watchlist_id: int, previous_decision: str, snapshot: EdgeSnapshot
) -> str:
    return (
        f"edge:{int(watchlist_id)}:{str(previous_decision or 'NONE').upper()}"
        f"->{snapshot.decision}:{snapshot.side}:{snapshot.market_probability:.2f}"
        f":{snapshot.fair_probability:.2f}:{snapshot.analysis_id or 0}"
    )


def should_notify_transition(previous: Dict[str, Any], snapshot: EdgeSnapshot) -> bool:
    old_decision = str((previous or {}).get("decision") or "NO_TRADE").upper()
    old_side = str((previous or {}).get("side") or "").upper()
    if old_side and old_side != snapshot.side:
        return True
    return old_decision != snapshot.decision


def format_edge_alert(
    *,
    question: str,
    market_url: str,
    previous: Dict[str, Any],
    snapshot: EdgeSnapshot,
    lang: str = "ru",
) -> str:
    old_decision = str((previous or {}).get("decision") or "NO_TRADE").upper()
    old_market = _safe_float((previous or {}).get("market_probability"))
    direction = "усилился" if decision_rank(snapshot.decision) > decision_rank(old_decision) else "ослаб"

    if lang == "en":
        lines = [
            "⚡ DeepAlpha Edge Alert",
            "",
            f"📌 {question}",
            "",
            f"Decision changed: {old_decision} → {snapshot.decision}",
            f"Side: {snapshot.side}",
            f"Fair probability: {snapshot.fair_probability:.1f}%",
            f"Market price: {snapshot.market_probability:.1f}%",
            f"Edge: {snapshot.edge_pp:+.1f} pp",
            f"Confidence: {snapshot.confidence}",
        ]
        if old_market is not None:
            lines.append(f"Previous market price: {old_market:.1f}%")
        if snapshot.decision == "WATCH" and snapshot.confidence == "low":
            lines.append("BUY remains blocked until confidence is at least medium.")
        elif snapshot.decision == "BUY":
            lines.append("The edge now satisfies the BUY policy (>8 pp, confidence medium or high).")
        elif snapshot.decision == "NO_TRADE":
            lines.append("The price no longer provides a sufficient independent edge.")
        if market_url:
            lines.extend(["", f"🔗 {market_url}"])
        return "\n".join(lines)

    lines = [
        "⚡ DeepAlpha Edge Alert",
        "",
        f"📌 {question}",
        "",
        f"Решение изменилось: {old_decision} → {snapshot.decision}",
        f"Сигнал {direction}.",
        f"Сторона: {snapshot.side}",
        f"Справедливая вероятность: {snapshot.fair_probability:.1f}%",
        f"Цена рынка: {snapshot.market_probability:.1f}%",
        f"Edge: {snapshot.edge_pp:+.1f} п.п.",
        f"Уверенность: {_ru_confidence(snapshot.confidence)}",
    ]
    if old_market is not None:
        lines.append(f"Предыдущая цена рынка: {old_market:.1f}%")
    if snapshot.decision == "WATCH" and snapshot.confidence == "low":
        lines.append("BUY пока недоступен: уверенность должна вырасти минимум до средней.")
    elif snapshot.decision == "BUY":
        lines.append("Перевес теперь соответствует политике BUY: более 8 п.п. при уверенности не ниже средней.")
    elif snapshot.decision == "NO_TRADE":
        lines.append("Цена больше не даёт достаточного независимого преимущества.")
    if market_url:
        lines.extend(["", f"🔗 {market_url}"])
    return "\n".join(lines)


async def check_edge_watch_once(bot: Any) -> Dict[str, int]:
    init_edge_watch_schema()
    stats = {"markets": 0, "subscribers": 0, "initialized": 0, "notified": 0, "errors": 0}
    items = get_active_watchlist_items(limit=500)
    for item in items or []:
        slug = str((item or {}).get("market_slug") or "").strip()
        if not slug:
            continue
        try:
            market_data = fetch_market_by_slug(slug)
            if not market_data or is_market_resolved(market_data):
                continue
            stats["markets"] += 1
            subscribers = get_watchlist_subscribers(slug)
            for sub in subscribers or []:
                stats["subscribers"] += 1
                if not sub.get("notify_enabled"):
                    continue
                try:
                    await _check_edge_subscriber(bot, item, sub, market_data, stats)
                except Exception as exc:
                    stats["errors"] += 1
                    logger.warning(
                        "EDGE_WATCH_SUBSCRIBER_FAILED watchlist_id=%s type=%s",
                        sub.get("id"), exc.__class__.__name__,
                    )
            await asyncio.sleep(0.15)
        except Exception as exc:
            stats["errors"] += 1
            logger.warning("EDGE_WATCH_MARKET_FAILED slug=%s type=%s", slug[:80], exc.__class__.__name__)
    return stats


async def _check_edge_subscriber(
    bot: Any,
    item: Dict[str, Any],
    sub: Dict[str, Any],
    market_data: Dict[str, Any],
    stats: Dict[str, int],
) -> None:
    user_id = int(sub["user_id"])
    watchlist_id = int(sub["id"])
    slug = str(item.get("market_slug") or "")
    question = str(item.get("question") or "")
    market_url = str(item.get("market_url") or "")

    analysis = get_latest_analysis(user_id, slug, question)
    if not analysis:
        return
    snapshot = snapshot_from_analysis(analysis, market_data)
    if not snapshot:
        return

    previous = get_edge_state(watchlist_id)
    if not previous:
        upsert_edge_state(
            watchlist_id=watchlist_id,
            user_id=user_id,
            market_slug=slug,
            snapshot=snapshot,
            last_notified_decision=snapshot.decision,
        )
        stats["initialized"] += 1
        logger.info(
            "EDGE_WATCH_INITIALIZED watchlist_id=%s side=%s decision=%s fair=%.2f market=%.2f edge=%.2f",
            watchlist_id, snapshot.side, snapshot.decision,
            snapshot.fair_probability, snapshot.market_probability, snapshot.edge_pp,
        )
        return

    if not should_notify_transition(previous, snapshot):
        upsert_edge_state(
            watchlist_id=watchlist_id,
            user_id=user_id,
            market_slug=slug,
            snapshot=snapshot,
        )
        return

    fingerprint = transition_fingerprint(watchlist_id, str(previous.get("decision") or ""), snapshot)
    if fingerprint == str(previous.get("last_notification_fingerprint") or ""):
        upsert_edge_state(
            watchlist_id=watchlist_id,
            user_id=user_id,
            market_slug=slug,
            snapshot=snapshot,
        )
        return

    charge = charge_watchlist_event(user_id, watchlist_id, slug, "edge_transition", fingerprint)
    if charge.get("reason") == "insufficient_tokens":
        logger.info("EDGE_WATCH_PAUSED_INSUFFICIENT_TOKENS watchlist_id=%s", watchlist_id)
        return

    lang = str(sub.get("language") or sub.get("lang") or "ru").lower()
    text = format_edge_alert(
        question=question,
        market_url=market_url,
        previous=previous,
        snapshot=snapshot,
        lang="en" if lang == "en" else "ru",
    )
    await bot.send_message(user_id, text, disable_web_page_preview=True)
    upsert_edge_state(
        watchlist_id=watchlist_id,
        user_id=user_id,
        market_slug=slug,
        snapshot=snapshot,
        last_notified_decision=snapshot.decision,
        last_notification_fingerprint=fingerprint,
    )
    stats["notified"] += 1
    logger.info(
        "EDGE_WATCH_NOTIFIED watchlist_id=%s transition=%s->%s side=%s edge=%.2f",
        watchlist_id, previous.get("decision"), snapshot.decision, snapshot.side, snapshot.edge_pp,
    )


async def edge_watch_worker(bot: Any) -> None:
    if not env_true(os.getenv("EDGE_WATCH_WORKER_ENABLED"), default=True):
        logger.info("EDGE_WATCH_WORKER disabled by env")
        return

    startup_delay = max(0, _safe_int(os.getenv("EDGE_WATCH_STARTUP_DELAY_SECONDS"), DEFAULT_STARTUP_DELAY_SECONDS))
    interval = max(60, _safe_int(os.getenv("EDGE_WATCH_INTERVAL_SECONDS"), DEFAULT_INTERVAL_SECONDS))
    await asyncio.sleep(startup_delay)

    owner = os.getenv("RAILWAY_REPLICA_ID") or os.getenv("HOSTNAME") or str(uuid.uuid4())
    while True:
        locked = False
        try:
            locked = acquire_distributed_lock("edge_watch_worker", owner, max(interval * 2, 600))
            if locked:
                stats = await check_edge_watch_once(bot)
                logger.info(
                    "EDGE_WATCH_DONE markets=%s subscribers=%s initialized=%s notified=%s errors=%s",
                    stats["markets"], stats["subscribers"], stats["initialized"],
                    stats["notified"], stats["errors"],
                )
        except Exception as exc:
            logger.exception("EDGE_WATCH_WORKER_ERROR type=%s", exc.__class__.__name__)
        finally:
            if locked:
                try:
                    release_distributed_lock("edge_watch_worker", owner)
                except Exception:
                    pass
        await asyncio.sleep(interval)


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _ru_confidence(value: str) -> str:
    return {"high": "высокая", "medium": "средняя", "low": "низкая", "none": "нет"}.get(
        str(value or "none"), str(value or "нет")
    )
