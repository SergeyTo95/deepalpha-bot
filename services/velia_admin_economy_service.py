from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from db.database import get_connection
from services.velia_admin_security_service import insert_admin_audit


PLAN_CODES = ("free", "plus", "pro")
FEATURE_CODES = (
    "velia_chat",
    "image_generation",
    "video_generation",
    "quick_analysis",
    "cached_signal",
    "opportunity_scan",
    "agent_task",
    "developer_task",
)

TOKEN_DEFINITION = {
    "name": "VELIA Token",
    "symbol": "token",
    "kind": "internal_usage_credit",
    "description": (
        "Internal VELIA usage credit used to meter premium product actions. "
        "It is not a blockchain token, is not a cryptocurrency, has no redemption value, "
        "and does not yet have a fixed USD value."
    ),
    "economics_status": "draft",
    "fixed_usd_value": None,
}

_PLAN_DEFAULTS = (
    ("free", "Free", None, None, "Draft tier. Final limits and included tokens are not decided."),
    ("plus", "Plus", None, None, "Draft tier. Price and included tokens are intentionally TBD."),
    ("pro", "Pro", None, None, "Draft tier. Price and included tokens are intentionally TBD."),
)

_FEATURE_DEFAULTS = (
    ("velia_chat", "VELIA chat", None, "per request", "Draft only; runtime billing is not connected."),
    ("image_generation", "Image generation", None, "per image", "Draft only; provider cost is tracked separately."),
    ("video_generation", "Video generation", None, "per video", "Draft only; provider cost is tracked separately."),
    ("quick_analysis", "Quick Analysis", None, "per analysis", "Draft future VELIA pricing."),
    ("cached_signal", "Cached Signal", None, "per signal", "Draft future VELIA pricing."),
    ("opportunity_scan", "Opportunity Scan", None, "per scan", "Draft future VELIA pricing."),
    ("agent_task", "Agent task", None, "per task", "Draft future Velyon Agent pricing."),
    ("developer_task", "Developer / Coding task", None, "per task", "Draft future Developer pricing."),
)

_RUNTIME_SETTING_KEYS = (
    "analysis_price_tokens",
    "cached_signal_price_tokens",
    "opportunity_price_tokens",
    "subscription_price_ton",
    "sub_daily_analyses",
    "sub_daily_opportunities",
    "web_analysis_price_usd",
    "web_subscription_enabled",
    "web_ton_enabled",
    "web_tron_usdt_enabled",
    "web_evm_usdt_enabled",
    "web_card_payments_enabled",
)


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _table_exists(cursor: Any, table_name: str) -> bool:
    cursor.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
    return bool(_row_value(cursor.fetchone(), "to_regclass", 0))


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def ensure_economy_tables() -> None:
    """Create additive Stage 2 economy tables and a fail-open token-balance ledger trigger.

    The trigger is deliberately observational. Any ledger insertion failure is swallowed
    inside PostgreSQL so a purchase, charge, refund, or admin balance change can never be
    blocked by the observability layer.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_token_ledger (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                delta_tokens INTEGER NOT NULL,
                balance_before INTEGER,
                balance_after INTEGER,
                source TEXT NOT NULL DEFAULT 'balance_change',
                reference_type TEXT,
                reference_id TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_token_ledger_user_created "
            "ON velia_token_ledger(user_id, created_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_token_ledger_created "
            "ON velia_token_ledger(created_at DESC)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_plans (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                monthly_price_usd NUMERIC(12, 2),
                monthly_tokens INTEGER,
                notes TEXT NOT NULL DEFAULT '',
                updated_by BIGINT,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_features (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                tokens_per_action INTEGER,
                unit_label TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                updated_by BIGINT,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        for code, name, price, tokens, notes in _PLAN_DEFAULTS:
            cursor.execute(
                """
                INSERT INTO velia_commercial_draft_plans
                    (code, name, monthly_price_usd, monthly_tokens, notes)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (code) DO NOTHING
                """,
                (code, name, price, tokens, notes),
            )
        for code, name, tokens, unit_label, notes in _FEATURE_DEFAULTS:
            cursor.execute(
                """
                INSERT INTO velia_commercial_draft_features
                    (code, name, tokens_per_action, unit_label, notes)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (code) DO NOTHING
                """,
                (code, name, tokens, unit_label, notes),
            )

        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION velia_capture_token_balance_change()
            RETURNS trigger AS $$
            BEGIN
                IF NEW.token_balance IS DISTINCT FROM OLD.token_balance THEN
                    BEGIN
                        INSERT INTO velia_token_ledger (
                            user_id, delta_tokens, balance_before, balance_after, source
                        ) VALUES (
                            NEW.user_id,
                            COALESCE(NEW.token_balance, 0) - COALESCE(OLD.token_balance, 0),
                            COALESCE(OLD.token_balance, 0),
                            COALESCE(NEW.token_balance, 0),
                            'balance_change'
                        );
                    EXCEPTION WHEN OTHERS THEN
                        NULL;
                    END;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        cursor.execute(
            """
            SELECT 1
            FROM pg_trigger
            WHERE tgname='trg_velia_token_balance_change'
              AND tgrelid='users'::regclass
              AND NOT tgisinternal
            LIMIT 1
            """
        )
        if not cursor.fetchone():
            cursor.execute(
                """
                CREATE TRIGGER trg_velia_token_balance_change
                AFTER UPDATE OF token_balance ON users
                FOR EACH ROW
                EXECUTE FUNCTION velia_capture_token_balance_change()
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _runtime_settings(cursor: Any) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not _table_exists(cursor, "settings"):
        return result
    cursor.execute(
        "SELECT key, value FROM settings WHERE key = ANY(%s)",
        (list(_RUNTIME_SETTING_KEYS),),
    )
    for row in cursor.fetchall() or []:
        result[str(_row_value(row, "key", 0, ""))] = str(_row_value(row, "value", 1, "") or "")
    defaults = {
        "analysis_price_tokens": "10",
        "cached_signal_price_tokens": "5",
        "opportunity_price_tokens": "20",
        "subscription_price_ton": "1",
        "sub_daily_analyses": "15",
        "sub_daily_opportunities": "3",
        "web_analysis_price_usd": "0",
        "web_subscription_enabled": "off",
        "web_ton_enabled": "off",
        "web_tron_usdt_enabled": "off",
        "web_evm_usdt_enabled": "off",
        "web_card_payments_enabled": "off",
    }
    for key, default in defaults.items():
        result.setdefault(key, default)
    return result


def _token_packages(cursor: Any) -> List[Dict[str, Any]]:
    if not _table_exists(cursor, "token_packages"):
        return []
    cursor.execute(
        """
        SELECT id, name, tokens, price_ton, discount_percent, is_active, sort_order
        FROM token_packages
        ORDER BY sort_order, id
        """
    )
    return [
        {
            "id": _as_int(_row_value(row, "id", 0)),
            "name": str(_row_value(row, "name", 1, "") or ""),
            "tokens": _as_int(_row_value(row, "tokens", 2)),
            "price_ton": _as_float(_row_value(row, "price_ton", 3)),
            "discount_percent": _as_int(_row_value(row, "discount_percent", 4)),
            "is_active": bool(_row_value(row, "is_active", 5, False)),
            "sort_order": _as_int(_row_value(row, "sort_order", 6)),
        }
        for row in cursor.fetchall() or []
    ]


def _ai_usage(cursor: Any) -> Dict[str, Any]:
    if not _table_exists(cursor, "velia_messages"):
        return {"available": False, "reason": "velia_messages_table_missing"}
    cursor.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),
          COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'),
          COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days'),
          COALESCE(SUM(estimated_cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'), 0),
          COALESCE(SUM(estimated_cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'), 0),
          COALESCE(SUM(estimated_cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days'), 0),
          COALESCE(SUM(prompt_tokens) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days'), 0),
          COALESCE(SUM(completion_tokens) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days'), 0)
        FROM velia_messages
        WHERE role='assistant' AND provider IS NOT NULL AND deleted_at IS NULL
        """
    )
    row = cursor.fetchone()
    return {
        "available": True,
        "requests_24h": _as_int(_row_value(row, "count", 0)),
        "requests_7d": _as_int(_row_value(row, "count", 1)),
        "requests_30d": _as_int(_row_value(row, "count", 2)),
        "cost_24h_usd": _as_float(_row_value(row, "coalesce", 3)),
        "cost_7d_usd": _as_float(_row_value(row, "coalesce", 4)),
        "cost_30d_usd": _as_float(_row_value(row, "coalesce", 5)),
        "input_tokens_30d": _as_int(_row_value(row, "coalesce", 6)),
        "output_tokens_30d": _as_int(_row_value(row, "coalesce", 7)),
        "scope": "persisted_velia_assistant_provider_usage",
    }


def _provider_breakdown(cursor: Any) -> List[Dict[str, Any]]:
    if not _table_exists(cursor, "velia_messages"):
        return []
    cursor.execute(
        """
        SELECT provider, model,
               COUNT(*) AS requests,
               COUNT(*) FILTER (WHERE status='error') AS failed,
               AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_latency_ms,
               COALESCE(SUM(prompt_tokens),0) AS input_tokens,
               COALESCE(SUM(completion_tokens),0) AS output_tokens,
               COALESCE(SUM(estimated_cost_usd),0) AS estimated_cost_usd
        FROM velia_messages
        WHERE role='assistant' AND provider IS NOT NULL
          AND deleted_at IS NULL
          AND created_at >= NOW() - INTERVAL '30 days'
        GROUP BY provider, model
        ORDER BY estimated_cost_usd DESC, requests DESC
        LIMIT 50
        """
    )
    items = []
    for row in cursor.fetchall() or []:
        requests = _as_int(_row_value(row, "requests", 2))
        failed = _as_int(_row_value(row, "failed", 3))
        items.append({
            "provider": str(_row_value(row, "provider", 0, "") or ""),
            "model": str(_row_value(row, "model", 1, "") or ""),
            "requests": requests,
            "failed": failed,
            "success_rate": round(((requests - failed) / requests) * 100, 2) if requests else None,
            "avg_latency_ms": round(_as_float(_row_value(row, "avg_latency_ms", 4)), 1)
            if _row_value(row, "avg_latency_ms", 4) is not None else None,
            "input_tokens": _as_int(_row_value(row, "input_tokens", 5)),
            "output_tokens": _as_int(_row_value(row, "output_tokens", 6)),
            "estimated_cost_usd": _as_float(_row_value(row, "estimated_cost_usd", 7)),
        })
    return items


def _generation_cost(cursor: Any, table_name: str) -> Dict[str, Any]:
    if table_name not in {"velia_generated_images", "velia_generated_videos"}:
        raise ValueError("unsupported_generation_table")
    if not _table_exists(cursor, table_name):
        return {"available": False, "reason": "table_missing"}
    cursor.execute(
        f"""
        SELECT
          COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),
          COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'),
          COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days'),
          COALESCE(SUM(estimated_cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'), 0),
          COALESCE(SUM(estimated_cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'), 0),
          COALESCE(SUM(estimated_cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days'), 0)
        FROM {table_name}
        """
    )
    row = cursor.fetchone()
    return {
        "available": True,
        "count_24h": _as_int(_row_value(row, "count", 0)),
        "count_7d": _as_int(_row_value(row, "count", 1)),
        "count_30d": _as_int(_row_value(row, "count", 2)),
        "cost_24h_usd": _as_float(_row_value(row, "coalesce", 3)),
        "cost_7d_usd": _as_float(_row_value(row, "coalesce", 4)),
        "cost_30d_usd": _as_float(_row_value(row, "coalesce", 5)),
    }


def _user_analytics(cursor: Any) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE LEFT(COALESCE(created_at,''),10) = TO_CHAR(CURRENT_DATE,'YYYY-MM-DD')),
               COUNT(*) FILTER (WHERE LEFT(COALESCE(created_at,''),10) >= TO_CHAR(CURRENT_DATE - INTERVAL '6 days','YYYY-MM-DD')),
               COUNT(*) FILTER (WHERE LEFT(COALESCE(created_at,''),10) >= TO_CHAR(CURRENT_DATE - INTERVAL '29 days','YYYY-MM-DD')),
               COUNT(*) FILTER (WHERE COALESCE(is_vip,0)=1),
               COUNT(*) FILTER (WHERE COALESCE(token_balance,0)>0),
               COALESCE(SUM(token_balance),0)
        FROM users
        """
    )
    row = cursor.fetchone()
    result = {
        "total_users": _as_int(_row_value(row, "count", 0)),
        "new_today": _as_int(_row_value(row, "count", 1)),
        "new_7d": _as_int(_row_value(row, "count", 2)),
        "new_30d": _as_int(_row_value(row, "count", 3)),
        "vip_users": _as_int(_row_value(row, "count", 4)),
        "users_with_tokens": _as_int(_row_value(row, "count", 5)),
        "token_balance_total": _as_int(_row_value(row, "coalesce", 6)),
        "chat_activity_available": False,
    }
    if _table_exists(cursor, "velia_messages"):
        cursor.execute(
            """
            SELECT
              COUNT(DISTINCT user_id) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),
              COUNT(DISTINCT user_id) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'),
              COUNT(DISTINCT user_id) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days')
            FROM velia_messages
            WHERE deleted_at IS NULL
            """
        )
        active = cursor.fetchone()
        result.update({
            "chat_activity_available": True,
            "chat_dau": _as_int(_row_value(active, "count", 0)),
            "chat_wau": _as_int(_row_value(active, "count", 1)),
            "chat_mau": _as_int(_row_value(active, "count", 2)),
            "activity_scope": "velia_messages_only",
        })
    return result


def _top_cost_users(cursor: Any) -> List[Dict[str, Any]]:
    if not _table_exists(cursor, "velia_messages"):
        return []
    cursor.execute(
        """
        SELECT m.user_id, u.username, u.first_name,
               COUNT(*) AS requests,
               COALESCE(SUM(m.estimated_cost_usd),0) AS estimated_cost_usd,
               COALESCE(SUM(m.prompt_tokens),0) AS input_tokens,
               COALESCE(SUM(m.completion_tokens),0) AS output_tokens
        FROM velia_messages m
        LEFT JOIN users u ON u.user_id=m.user_id
        WHERE m.role='assistant' AND m.provider IS NOT NULL
          AND m.deleted_at IS NULL
          AND m.created_at >= NOW() - INTERVAL '30 days'
        GROUP BY m.user_id, u.username, u.first_name
        ORDER BY estimated_cost_usd DESC, requests DESC
        LIMIT 25
        """
    )
    return [
        {
            "user_id": _as_int(_row_value(row, "user_id", 0)),
            "username": _row_value(row, "username", 1),
            "first_name": _row_value(row, "first_name", 2),
            "requests": _as_int(_row_value(row, "requests", 3)),
            "estimated_cost_usd": _as_float(_row_value(row, "estimated_cost_usd", 4)),
            "input_tokens": _as_int(_row_value(row, "input_tokens", 5)),
            "output_tokens": _as_int(_row_value(row, "output_tokens", 6)),
        }
        for row in cursor.fetchall() or []
    ]


def _ledger(cursor: Any, limit: int = 100) -> Dict[str, Any]:
    if not _table_exists(cursor, "velia_token_ledger"):
        return {"available": False, "items": [], "reason": "ledger_table_missing"}
    cursor.execute("SELECT MIN(created_at), COUNT(*) FROM velia_token_ledger")
    meta = cursor.fetchone()
    cursor.execute(
        """
        SELECT l.id, l.user_id, l.delta_tokens, l.balance_before, l.balance_after,
               l.source, l.reference_type, l.reference_id, l.created_at,
               u.username, u.first_name
        FROM velia_token_ledger l
        LEFT JOIN users u ON u.user_id=l.user_id
        ORDER BY l.id DESC
        LIMIT %s
        """,
        (max(1, min(int(limit), 250)),),
    )
    items = []
    for row in cursor.fetchall() or []:
        items.append({
            "id": _as_int(_row_value(row, "id", 0)),
            "user_id": _as_int(_row_value(row, "user_id", 1)),
            "delta_tokens": _as_int(_row_value(row, "delta_tokens", 2)),
            "balance_before": _as_int(_row_value(row, "balance_before", 3)),
            "balance_after": _as_int(_row_value(row, "balance_after", 4)),
            "source": str(_row_value(row, "source", 5, "balance_change") or "balance_change"),
            "reference_type": _row_value(row, "reference_type", 6),
            "reference_id": _row_value(row, "reference_id", 7),
            "created_at": str(_row_value(row, "created_at", 8, "") or ""),
            "username": _row_value(row, "username", 9),
            "first_name": _row_value(row, "first_name", 10),
        })
    return {
        "available": True,
        "tracking_started_at": str(_row_value(meta, "min", 0, "") or "") or None,
        "total_events": _as_int(_row_value(meta, "count", 1)),
        "items": items,
        "note": "canonical balance changes are tracked from Stage 2 deployment forward",
    }


def _draft_plans(cursor: Any) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT code, name, monthly_price_usd, monthly_tokens, notes, updated_by, updated_at
        FROM velia_commercial_draft_plans
        ORDER BY CASE code WHEN 'free' THEN 1 WHEN 'plus' THEN 2 WHEN 'pro' THEN 3 ELSE 99 END, code
        """
    )
    return [
        {
            "code": str(_row_value(row, "code", 0, "") or ""),
            "name": str(_row_value(row, "name", 1, "") or ""),
            "monthly_price_usd": None if _row_value(row, "monthly_price_usd", 2) is None else _as_float(_row_value(row, "monthly_price_usd", 2)),
            "monthly_tokens": None if _row_value(row, "monthly_tokens", 3) is None else _as_int(_row_value(row, "monthly_tokens", 3)),
            "notes": str(_row_value(row, "notes", 4, "") or ""),
            "updated_by": _row_value(row, "updated_by", 5),
            "updated_at": str(_row_value(row, "updated_at", 6, "") or ""),
        }
        for row in cursor.fetchall() or []
    ]


def _draft_features(cursor: Any) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT code, name, tokens_per_action, unit_label, notes, updated_by, updated_at
        FROM velia_commercial_draft_features
        ORDER BY code
        """
    )
    return [
        {
            "code": str(_row_value(row, "code", 0, "") or ""),
            "name": str(_row_value(row, "name", 1, "") or ""),
            "tokens_per_action": None if _row_value(row, "tokens_per_action", 2) is None else _as_int(_row_value(row, "tokens_per_action", 2)),
            "unit_label": str(_row_value(row, "unit_label", 3, "") or ""),
            "notes": str(_row_value(row, "notes", 4, "") or ""),
            "updated_by": _row_value(row, "updated_by", 5),
            "updated_at": str(_row_value(row, "updated_at", 6, "") or ""),
        }
        for row in cursor.fetchall() or []
    ]


def economy_snapshot() -> Dict[str, Any]:
    try:
        ensure_economy_tables()
    except Exception as exc:
        return {"available": False, "reason": exc.__class__.__name__}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        ai = _ai_usage(cursor)
        images = _generation_cost(cursor, "velia_generated_images")
        videos = _generation_cost(cursor, "velia_generated_videos")
        total_24 = _as_float(ai.get("cost_24h_usd")) + _as_float(images.get("cost_24h_usd")) + _as_float(videos.get("cost_24h_usd"))
        total_7d = _as_float(ai.get("cost_7d_usd")) + _as_float(images.get("cost_7d_usd")) + _as_float(videos.get("cost_7d_usd"))
        total_30d = _as_float(ai.get("cost_30d_usd")) + _as_float(images.get("cost_30d_usd")) + _as_float(videos.get("cost_30d_usd"))
        return {
            "available": True,
            "token_definition": dict(TOKEN_DEFINITION),
            "costs": {
                "persisted_estimated_total_24h_usd": total_24,
                "persisted_estimated_total_7d_usd": total_7d,
                "persisted_estimated_total_30d_usd": total_30d,
                "ai": ai,
                "images": images,
                "videos": videos,
                "scope_note": "Persisted estimated provider costs only; missing provider telemetry is not guessed.",
            },
            "providers": _provider_breakdown(cursor),
            "top_cost_users": _top_cost_users(cursor),
            "users": _user_analytics(cursor),
            "ledger": _ledger(cursor),
            "runtime_pricing": _runtime_settings(cursor),
            "token_packages": _token_packages(cursor),
            "draft_plans": _draft_plans(cursor),
            "draft_features": _draft_features(cursor),
            "draft_status": "not_enforced",
        }
    except Exception as exc:
        return {"available": False, "reason": exc.__class__.__name__}
    finally:
        cursor.close()
        conn.close()


def _audit_request_id(request_id: str) -> str:
    value = str(request_id or "").strip()
    return value[:160] if value else f"economy-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"


def update_draft_plan(
    *,
    admin_user_id: int,
    code: str,
    monthly_price_usd: Optional[float],
    monthly_tokens: Optional[int],
    notes: str,
    request_id: str = "",
    source: str = "web",
    ip: str = "",
    user_agent: str = "",
) -> Dict[str, Any]:
    normalized = str(code or "").strip().lower()
    if normalized not in PLAN_CODES:
        return {"ok": False, "error": "unknown_plan"}
    if monthly_price_usd is not None and (monthly_price_usd < 0 or monthly_price_usd > 1_000_000):
        return {"ok": False, "error": "invalid_price"}
    if monthly_tokens is not None and (monthly_tokens < 0 or monthly_tokens > 1_000_000_000):
        return {"ok": False, "error": "invalid_tokens"}
    safe_notes = str(notes or "").strip()[:1000]
    rid = _audit_request_id(request_id)
    ensure_economy_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT code,name,monthly_price_usd,monthly_tokens,notes FROM velia_commercial_draft_plans WHERE code=%s FOR UPDATE",
            (normalized,),
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return {"ok": False, "error": "unknown_plan"}
        before = {
            "code": str(_row_value(row, "code", 0, normalized)),
            "name": str(_row_value(row, "name", 1, "")),
            "monthly_price_usd": None if _row_value(row, "monthly_price_usd", 2) is None else _as_float(_row_value(row, "monthly_price_usd", 2)),
            "monthly_tokens": None if _row_value(row, "monthly_tokens", 3) is None else _as_int(_row_value(row, "monthly_tokens", 3)),
            "notes": str(_row_value(row, "notes", 4, "") or ""),
        }
        cursor.execute(
            """
            UPDATE velia_commercial_draft_plans
            SET monthly_price_usd=%s, monthly_tokens=%s, notes=%s, updated_by=%s, updated_at=NOW()
            WHERE code=%s
            """,
            (monthly_price_usd, monthly_tokens, safe_notes, int(admin_user_id), normalized),
        )
        after = {**before, "monthly_price_usd": monthly_price_usd, "monthly_tokens": monthly_tokens, "notes": safe_notes}
        insert_admin_audit(
            cursor,
            admin_user_id=int(admin_user_id),
            action="economy.draft_plan_update",
            target_type="commercial_draft_plan",
            target_id=normalized,
            request_id=rid,
            before=before,
            after=after,
            success=True,
            source=str(source or "web")[:40],
            ip=ip,
            user_agent=user_agent,
        )
        conn.commit()
        return {"ok": True, "before": before, "after": after, "request_id": rid}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": exc.__class__.__name__, "request_id": rid}
    finally:
        cursor.close()
        conn.close()


def update_draft_feature(
    *,
    admin_user_id: int,
    code: str,
    tokens_per_action: Optional[int],
    notes: str,
    request_id: str = "",
    source: str = "web",
    ip: str = "",
    user_agent: str = "",
) -> Dict[str, Any]:
    normalized = str(code or "").strip().lower()
    if normalized not in FEATURE_CODES:
        return {"ok": False, "error": "unknown_feature"}
    if tokens_per_action is not None and (tokens_per_action < 0 or tokens_per_action > 1_000_000_000):
        return {"ok": False, "error": "invalid_tokens"}
    safe_notes = str(notes or "").strip()[:1000]
    rid = _audit_request_id(request_id)
    ensure_economy_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT code,name,tokens_per_action,unit_label,notes FROM velia_commercial_draft_features WHERE code=%s FOR UPDATE",
            (normalized,),
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return {"ok": False, "error": "unknown_feature"}
        before = {
            "code": str(_row_value(row, "code", 0, normalized)),
            "name": str(_row_value(row, "name", 1, "")),
            "tokens_per_action": None if _row_value(row, "tokens_per_action", 2) is None else _as_int(_row_value(row, "tokens_per_action", 2)),
            "unit_label": str(_row_value(row, "unit_label", 3, "")),
            "notes": str(_row_value(row, "notes", 4, "") or ""),
        }
        cursor.execute(
            """
            UPDATE velia_commercial_draft_features
            SET tokens_per_action=%s, notes=%s, updated_by=%s, updated_at=NOW()
            WHERE code=%s
            """,
            (tokens_per_action, safe_notes, int(admin_user_id), normalized),
        )
        after = {**before, "tokens_per_action": tokens_per_action, "notes": safe_notes}
        insert_admin_audit(
            cursor,
            admin_user_id=int(admin_user_id),
            action="economy.draft_feature_update",
            target_type="commercial_draft_feature",
            target_id=normalized,
            request_id=rid,
            before=before,
            after=after,
            success=True,
            source=str(source or "web")[:40],
            ip=ip,
            user_agent=user_agent,
        )
        conn.commit()
        return {"ok": True, "before": before, "after": after, "request_id": rid}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": exc.__class__.__name__, "request_id": rid}
    finally:
        cursor.close()
        conn.close()
