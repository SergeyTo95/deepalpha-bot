"""Text share-card generation for Airdrop referral growth."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from db.database import get_connection
except Exception:  # pragma: no cover
    get_connection = None

def _get_referral_code(user_id: int) -> str:
    try:
        from services.airdrop_referral_service import get_or_create_referral_code
        return get_or_create_referral_code(user_id)
    except Exception:
        return hashlib.sha256(f"deepalpha:{int(user_id)}".encode()).hexdigest()[:12]

logger = logging.getLogger(__name__)
_TABLE_READY = False
_MEMORY_LATEST: Dict[int, dict] = {}
_MEMORY_CARDS: Dict[str, dict] = {}

UNSAFE_WORDS = ("ставь", "покупай", "грузи", "guaranteed", "easy profit", "free money")
SAFE_DISCLAIMER_RU = "Не финансовый совет. Проверь правила resolution, ликвидность и свежие данные."
SAFE_DISCLAIMER_EN = "Not financial advice. Check resolution rules, liquidity, and fresh data."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _bot_username() -> str:
    return (os.getenv("BOT_USERNAME") or "DeepAlphaAI_bot").lstrip("@") or "DeepAlphaAI_bot"


def _clean_text(value: Any, limit: int, fallback: str) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or "")).replace("\x00", " ")
    text = re.sub(r"\s+", " ", text).strip()
    for word in UNSAFE_WORDS:
        text = re.sub(re.escape(word), "", text, flags=re.IGNORECASE)
    text = text.replace("100%", "")
    text = text.strip(" -–—\n\t")
    if not text:
        text = fallback
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _safe_decision(value: Any) -> str:
    text = _clean_text(value, 40, "WATCH").upper()
    allowed = {"WATCH", "DATA NEEDED", "NO EDGE", "EDGE CANDIDATE"}
    if text in allowed:
        return text
    if "NO" in text and "EDGE" in text:
        return "NO EDGE"
    if "DATA" in text:
        return "DATA NEEDED"
    if "EDGE" in text:
        return "EDGE CANDIDATE"
    return "WATCH"


def _safe_score(value: Any) -> Optional[int]:
    try:
        score = int(float(str(value).strip()))
    except Exception:
        return None
    return max(0, min(100, score))


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _decode(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


def _ensure_tables(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS airdrop_share_cards (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            share_id TEXT NOT NULL UNIQUE,
            analysis_type TEXT,
            title TEXT,
            market_url TEXT,
            domain TEXT,
            decision TEXT,
            score INTEGER,
            short_summary TEXT,
            key_risk TEXT,
            referral_code TEXT,
            referral_link TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_airdrop_share_cards_user_created ON airdrop_share_cards(user_id, created_at)")


def _connect_ready():
    global _TABLE_READY
    if get_connection is None:
        raise RuntimeError("database unavailable")
    conn = get_connection(); cur = conn.cursor()
    if not _TABLE_READY:
        _ensure_tables(cur); conn.commit(); _TABLE_READY = True
    return conn, cur


def build_share_card_payload(user_id: int, analysis_type: str, title: str | None = None, market_url: str | None = None, domain: str | None = None, decision: str | None = None, score: int | None = None, short_summary: str | None = None, key_risk: str | None = None, metadata: dict | None = None, ui_language: str = "ru") -> dict:
    uid = int(user_id)
    code = _get_referral_code(uid)
    link = f"https://t.me/{_bot_username()}?start=ref_{code}"
    is_ru = ui_language == "ru"
    payload = {
        "user_id": uid,
        "analysis_type": _clean_text(analysis_type, 48, "analysis"),
        "title": _clean_text(title, 140, "DeepAlpha market insight"),
        "market_url": _clean_text(market_url, 240, "") if market_url else "",
        "domain": _clean_text(domain, 40, "market"),
        "decision": _safe_decision(decision),
        "score": _safe_score(score),
        "short_summary": _clean_text(short_summary, 180, "DeepAlpha found that this market needs context, rules, and fresh data before making a confident call."),
        "key_risk": _clean_text(key_risk, 160, "Resolution rules, liquidity, and missing data can materially change the interpretation."),
        "referral_link": link,
        "referral_code": code,
        "created_at": _now_iso(),
        "safe_disclaimer": SAFE_DISCLAIMER_RU if is_ru else SAFE_DISCLAIMER_EN,
        "metadata": metadata or {},
    }
    basis = {k: payload.get(k) for k in ("user_id", "analysis_type", "title", "market_url", "decision", "score")}
    basis["bucket"] = payload["created_at"][:13]
    payload["share_id"] = hashlib.sha256(_json(basis).encode()).hexdigest()[:16]
    return payload


def format_share_card_text(payload: dict, ui_language: str = "ru") -> str:
    p = payload or {}
    score = p.get("score") if p.get("score") is not None else "—"
    if ui_language == "en":
        return (f"📊 DeepAlpha AI Insight\n\nMarket:\n{p.get('title')}\n\nDeepAlpha Score:\n{score}/100\n\nDecision:\n{p.get('decision') or 'WATCH'}\n\nSummary:\n{p.get('short_summary')}\n\nKey risk:\n{p.get('key_risk')}\n\nCheck before deciding:\n• resolution rules\n• liquidity\n• fresh data/news\n\nOpen DeepAlpha:\n{p.get('referral_link')}\n\nDeepAlpha AI — AI analyst for prediction markets and event-driven markets.")
    return (f"📊 DeepAlpha AI Insight\n\nMarket:\n{p.get('title')}\n\nDeepAlpha Score:\n{score}/100\n\nDecision:\n{p.get('decision') or 'WATCH'}\n\nКоротко:\n{p.get('short_summary')}\n\nГлавный риск:\n{p.get('key_risk')}\n\nПроверь перед выводом:\n• resolution rules\n• liquidity\n• fresh news/data\n\nОткрыть DeepAlpha:\n{p.get('referral_link')}\n\nDeepAlpha AI — AI-аналитик для prediction markets и событийных рынков.")


def save_latest_share_card(user_id: int, payload: dict) -> dict:
    _MEMORY_LATEST[int(user_id)] = dict(payload or {})
    return _MEMORY_LATEST[int(user_id)]


def get_latest_share_card(user_id: int) -> dict | None:
    return _MEMORY_LATEST.get(int(user_id))


def record_share_card_generated(user_id: int, payload: dict) -> dict:
    uid = int(user_id); p = dict(payload or {}); sid = p.get("share_id") or hashlib.sha256(_json(p).encode()).hexdigest()[:16]; p["share_id"] = sid
    if sid in _MEMORY_CARDS:
        return {"ok": True, "deduped": True, "share_id": sid}
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("""
                INSERT INTO airdrop_share_cards (user_id, share_id, analysis_type, title, market_url, domain, decision, score, short_summary, key_risk, referral_code, referral_link, metadata_json, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()) ON CONFLICT (share_id) DO NOTHING
            """, (uid, sid, p.get("analysis_type"), p.get("title"), p.get("market_url"), p.get("domain"), p.get("decision"), p.get("score"), p.get("short_summary"), p.get("key_risk"), p.get("referral_code"), p.get("referral_link"), _json(p.get("metadata"))))
            inserted = cur.rowcount > 0; conn.commit()
            _MEMORY_CARDS[sid] = p
            return {"ok": True, "deduped": not inserted, "share_id": sid}
        finally:
            conn.close()
    except Exception:
        _MEMORY_CARDS[sid] = p
        return {"ok": True, "deduped": False, "share_id": sid}


def _stats_from_rows(rows: list[dict], user_id: Optional[int] = None) -> dict:
    today = _now().date().isoformat()
    filtered = [r for r in rows if user_id is None or int(r.get("user_id")) == int(user_id)]
    users = Counter(int(r.get("user_id")) for r in filtered)
    domains = Counter(str(r.get("domain") or "unknown") for r in filtered)
    return {"total_share_cards_generated": len(filtered), "share_cards_generated_today": sum(1 for r in filtered if str(r.get("created_at", ""))[:10] == today), "users_who_generated_cards": len(users), "top_share_card_users": [{"user_id": k, "count": v} for k, v in users.most_common(10)], "domains": dict(domains), "last_share_card_at": max([str(r.get("created_at")) for r in filtered] or [None])}


def get_share_card_stats(user_id: int) -> dict:
    return _stats_from_rows(list(_MEMORY_CARDS.values()), int(user_id))


def admin_get_share_card_stats() -> dict:
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT user_id, domain, created_at FROM airdrop_share_cards")
            rows = [{"user_id": r[0], "domain": r[1], "created_at": r[2].isoformat() if hasattr(r[2], "isoformat") else str(r[2])} for r in cur.fetchall()]
            return _stats_from_rows(rows)
        finally:
            conn.close()
    except Exception:
        return _stats_from_rows(list(_MEMORY_CARDS.values()))
