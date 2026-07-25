import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from db.database import (
    acquire_distributed_lock,
    charge_watchlist_event,
    get_connection,
    release_distributed_lock,
)
from services.edge_watch_market_resolver import resolve_watch_market
from services.polymarket_resolver import is_market_resolved

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
    match = re.search(
        r"\b(YES|NO|Yes|No|ДА|НЕТ|Да|Нет)\b[^0-9]{0,12}([0-9]+(?:[.,][0-9]+)?)\s*%",
        text,
    )
    if match:
        side = "YES" if match.group(1).lower() in {"yes", "да"} else "NO"
        return side, float(match.group(2).replace(",", "."))
    number = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*%", text)
    return ("", float(number.group(1).replace(",", "."))) if number else ("", None)


def parse_market_probability_text(value: Any, side: str) -> Optional[float]:
    text = str(value or "")
    aliases = ("YES", "Yes", "ДА", "Да") if str(side).upper() == "YES" else ("NO", "No", "НЕТ", "Нет")
    for alias in aliases:
        match = re.search(
            rf"\b{re.escape(alias)}\b[^0-9]{{0,12}}([0-9]+(?:[.,][0-9]+)?)\s*%",
            text,
        )
        if match:
            return float(match.group(1).replace(",", "."))
    return None


def classify_decision(edge_pp: float, confidence: str, independent: bool = True) -> str:
    if not independent or float(edge_pp) < WATCH_EDGE_THRESHOLD_PP:
        return "NO_TRADE"
    if float(edge_pp) > BUY_EDGE_THRESHOLD_PP and normalize_confidence(confidence) in {"medium", "high"}:
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
    fair = round(float(fair_probability), 2)
    market = round(float(market_probability), 2)
    confidence = normalize_confidence(confidence)
    edge = round(fair - market, 2)
    return EdgeSnapshot(
        side=str(side or "").upper(),
        fair_probability=fair,
        market_probability=market,
        edge_pp=edge,
        confidence=confidence,
        decision=classify_decision(edge, confidence, independent),
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
    for index, outcome in enumerate(outcomes):
        if outcome == target and index < len(prices):
            price = prices[index]
            return None if price != price else round(price, 4)
    if len(prices) == 2 and target in {"YES", "NO"}:
        price = prices[0 if target == "YES" else 1]
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
        cur.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_edge_state_slug ON watchlist_edge_state(market_slug)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_edge_state_user ON watchlist_edge_state(user_id)")
        cur.execute(
            """
            DELETE FROM watchlist_edge_state state
            WHERE NOT EXISTS (
                SELECT 1 FROM watchlist w
                WHERE w.id = state.watchlist_id AND w.is_closed = 0
            )
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_active_edge_watch_rows(limit: int = 500) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT w.id, w.user_id, w.market_slug, w.market_url, w.question,
                   COALESCE(u.language, 'ru') AS language
            FROM watchlist w
            LEFT JOIN users u ON u.user_id = w.user_id
            WHERE w.is_closed = 0
              AND COALESCE(w.notify_enabled, 1) = 1
              AND COALESCE(w.autopilot_enabled, 1) = 1
              AND (w.billing_status IS NULL OR w.billing_status = 'active')
            ORDER BY w.id ASC
            LIMIT %s
            """,
            (int(limit),),
        )
        rows = cur.fetchall() or []
        return [
            {
                "watchlist_id": row[0],
                "user_id": row[1],
                "market_slug": row[2],
                "market_url": row[3],
                "question": row[4],
                "language": row[5] or "ru",
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning("EDGE_WATCH_ROWS_FAILED type=%s", exc.__class__.__name__)
        return []
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
                    COALESCE(question, '') = %s
                    OR (%s <> '%%' AND COALESCE(url, '') ILIKE %s)
                  )
            ORDER BY
                CASE WHEN COALESCE(question, '') = %s THEN 0 ELSE 1 END,
                CASE WHEN user_id = %s THEN 0 ELSE 1 END,
                id DESC
            LIMIT 1
            """,
            (
                int(user_id), str(question or ""), slug_like, slug_like,
                str(question or ""), int(user_id),
            ),
        )
        row = cur.fetchone()
        if not row:
            return None
        columns = [description[0] for description in cur.description]
        return dict(zip(columns, row))
    except Exception as exc:
        logger.warning("EDGE_WATCH_ANALYSIS_LOOKUP_FAILED type=%s", exc.__class__.__name__)
        return None
    finally:
        cur.close()
        conn.close()


def snapshot_from_analysis(analysis: Dict[str, Any], market_data: Dict[str, Any]) -> Optional[EdgeSnapshot]:
    side, fair = parse_probability_text((analysis or {}).get("system_probability"))
    if side not in {"YES", "NO"} or fair is None:
        return None
    market = extract_side_price(market_data, side)
    if market is None:
        return None
    stored_market = parse_market_probability_text((analysis or {}).get("market_probability"), side)
    independent = not (stored_market is not None and abs(float(fair) - float(stored_market)) < 0.05)
    return build_snapshot(
        side=side,
        fair_probability=fair,
        market_probability=market,
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
        columns = [description[0] for description in cur.description]
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


def should_notify_transition(previous: Dict[str, Any], snapshot: EdgeSnapshot) -> bool:
    old_decision = str((previous or {}).get("decision") or "NO_TRADE").upper()
    old_side = str((previous or {}).get("side") or "").upper()
    return (bool(old_side) and old_side != snapshot.side) or old_decision != snapshot.decision


def transition_fingerprint(watchlist_id: int, previous_decision: str, snapshot: EdgeSnapshot) -> str:
    return (
        f"edge:{int(watchlist_id)}:{str(previous_decision or 'NONE').upper()}"
        f"->{snapshot.decision}:{snapshot.side}:{snapshot.market_probability:.2f}"
        f":{snapshot.fair_probability:.2f}:{snapshot.analysis_id or 0}"
    )


def charge_edge_transition(
    user_id: int,
    watchlist_id: int,
    market_slug: str,
    fingerprint: str,
) -> Dict[str, Any]:
    if not env_true(os.getenv("EDGE_WATCH_BILLING_ENABLED"), default=False):
        return {"charged": False, "reason": "edge_alert_included", "cost": 0}
    return charge_watchlist_event(
        user_id,
        watchlist_id,
        market_slug,
        "probability_change",
        fingerprint,
    )


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
    stronger = decision_rank(snapshot.decision) > decision_rank(old_decision)

    if str(lang).lower() == "en":
        lines = [
            "⚡ DeepAlpha Edge Alert", "", f"📌 {question}", "",
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
    else:
        lines = [
            "⚡ DeepAlpha Edge Alert", "", f"📌 {question}", "",
            f"Решение изменилось: {old_decision} → {snapshot.decision}",
            f"Сигнал {'усилился' if stronger else 'ослаб'}.",
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
            lines.append("Перевес соответствует политике BUY: более 8 п.п. при уверенности не ниже средней.")
        elif snapshot.decision == "NO_TRADE":
            lines.append("Цена больше не даёт достаточного независимого преимущества.")

    if market_url:
        lines.extend(["", f"🔗 {market_url}"])
    return "\n".join(lines)


async def check_edge_watch_once(bot: Any) -> Dict[str, int]:
    init_edge_watch_schema()
    stats = {"rows": 0, "initialized": 0, "notified": 0, "errors": 0}
    for row in get_active_edge_watch_rows(limit=500):
        stats["rows"] += 1
        try:
            market_data = resolve_watch_market(
                market_slug=str(row.get("market_slug") or ""),
                market_url=str(row.get("market_url") or ""),
                question=str(row.get("question") or ""),
            )
            if not market_data or is_market_resolved(market_data):
                continue
            await _check_edge_row(bot, row, market_data, stats)
            await asyncio.sleep(0.10)
        except Exception as exc:
            stats["errors"] += 1
            logger.warning(
                "EDGE_WATCH_ROW_FAILED watchlist_id=%s type=%s",
                row.get("watchlist_id"), exc.__class__.__name__,
            )
    return stats


async def _check_edge_row(
    bot: Any,
    row: Dict[str, Any],
    market_data: Dict[str, Any],
    stats: Dict[str, int],
) -> None:
    watchlist_id = int(row["watchlist_id"])
    user_id = int(row["user_id"])
    slug = str(row.get("market_slug") or "")
    question = str(row.get("question") or "")
    market_url = str(row.get("market_url") or "")

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

    charge = charge_edge_transition(user_id, watchlist_id, slug, fingerprint)
    if charge.get("reason") == "insufficient_tokens":
        logger.info("EDGE_WATCH_PAUSED_INSUFFICIENT_TOKENS watchlist_id=%s", watchlist_id)
        return

    text = format_edge_alert(
        question=question,
        market_url=market_url,
        previous=previous,
        snapshot=snapshot,
        lang="en" if str(row.get("language") or "ru").lower() == "en" else "ru",
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
        watchlist_id, previous.get("decision"), snapshot.decision,
        snapshot.side, snapshot.edge_pp,
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
                    "EDGE_WATCH_DONE rows=%s initialized=%s notified=%s errors=%s",
                    stats["rows"], stats["initialized"], stats["notified"], stats["errors"],
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
