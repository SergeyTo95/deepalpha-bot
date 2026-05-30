from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras

from db.database import get_connection


MISSING_TEXT = "Данных пока недостаточно."


def _empty_snapshot(days: int, reason: str) -> Dict[str, Any]:
    metrics = {
        "total_users": None,
        "new_users_24h": None,
        "new_users_days": None,
        "active_users_days": None,
        "total_analyses": None,
        "analyses_24h": None,
        "analyses_days": None,
        "total_token_purchases": None,
        "token_purchases_days": None,
        "revenue_ton_total": None,
        "revenue_ton_days": None,
        "purchase_intents_total": None,
        "purchase_intents_days": None,
        "referral_count": None,
        "referrals_days": None,
        "created_checks_total": None,
        "created_checks_days": None,
        "claimed_checks_total": None,
        "claimed_checks_days": None,
        "watchlist_count": None,
        "watchlist_active_count": None,
        "top_user_actions": None,
        "activation_rate_days": None,
        "new_user_growth_rate_days": None,
        "analysis_conversion_days": None,
        "analyses_per_user_days": None,
        "analyses_per_active_user_days": None,
        "purchase_conversion_days": None,
        "revenue_per_user_days": None,
        "revenue_per_active_user_days": None,
        "referral_ratio": None,
        "growth_quality": None,
    }
    snapshot = {
        "period_days": days,
        "generated_at": datetime.utcnow().isoformat(),
        "metrics": metrics,
        "missing_metrics": sorted(metrics.keys()),
        "notes": [reason],
        "errors": [],
    }
    _add_derived_metrics(snapshot)
    snapshot["missing_metrics"] = sorted(snapshot["missing_metrics"])
    return snapshot


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        )
        """,
        (table_name,),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _columns(cursor, table_name: str) -> List[str]:
    cursor.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table_name,),
    )
    return [str(r[0]) for r in cursor.fetchall()]


def _count(cursor, table_name: str, where: str = "", params: Tuple[Any, ...] = ()) -> Optional[int]:
    query = f"SELECT COUNT(*) FROM {table_name}"
    if where:
        query += f" WHERE {where}"
    cursor.execute(query, params)
    row = cursor.fetchone()
    return int(row[0] or 0) if row else 0


def _sum(cursor, table_name: str, expression: str, where: str = "", params: Tuple[Any, ...] = ()) -> Optional[float]:
    query = f"SELECT COALESCE(SUM({expression}), 0) FROM {table_name}"
    if where:
        query += f" WHERE {where}"
    cursor.execute(query, params)
    row = cursor.fetchone()
    return float(row[0] or 0) if row else 0.0


def _timestamp_filter(column: str, since: datetime) -> Tuple[str, Tuple[Any, ...]]:
    # Existing schema stores many timestamps as TEXT. The regex keeps the cast read-only and safe for ISO-like values.
    return (
        f"{column} IS NOT NULL AND {column} <> '' AND {column} ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}' AND {column}::timestamp >= %s",
        (since,),
    )


def _set_metric(snapshot: Dict[str, Any], key: str, value: Any) -> None:
    snapshot["metrics"][key] = value
    if value is not None and key in snapshot["missing_metrics"]:
        snapshot["missing_metrics"].remove(key)


def _safe_metric(snapshot: Dict[str, Any], key: str, func) -> None:
    try:
        _set_metric(snapshot, key, func())
    except Exception as exc:
        snapshot["errors"].append({"metric": key, "error": exc.__class__.__name__})


def _ratio(numerator: Any, denominator: Any, multiplier: float = 1.0, precision: int = 2) -> Optional[float]:
    if numerator is None or denominator is None:
        return None
    try:
        denominator_value = float(denominator)
        if denominator_value == 0:
            return None
        return round((float(numerator) / denominator_value) * multiplier, precision)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _growth_quality(metrics: Dict[str, Any]) -> Optional[str]:
    total_users = metrics.get("total_users")
    new_users = metrics.get("new_users_days")
    activation_rate = metrics.get("activation_rate_days")
    analyses = metrics.get("analyses_days")
    referral_ratio = metrics.get("referral_ratio")

    if total_users is None or new_users is None:
        return None
    if total_users == 0:
        return "no_user_base"
    if new_users == 0:
        return "no_new_users"
    if activation_rate is not None and activation_rate < 10:
        return "low_quality_growth_activation_bottleneck"
    if analyses is not None and analyses <= 1 and total_users >= 10:
        return "low_quality_growth_first_value_bottleneck"
    if referral_ratio is not None and referral_ratio >= 0.3:
        return "organic_growth_signal"
    return "early_growth_signal"


def _add_derived_metrics(snapshot: Dict[str, Any]) -> None:
    metrics = snapshot.get("metrics") or {}
    derived = {
        "activation_rate_days": _ratio(metrics.get("active_users_days"), metrics.get("total_users"), 100, 1),
        "new_user_growth_rate_days": _ratio(metrics.get("new_users_days"), metrics.get("total_users"), 100, 1),
        "analysis_conversion_days": _ratio(metrics.get("analyses_days"), metrics.get("total_users"), 100, 1),
        "analyses_per_user_days": _ratio(metrics.get("analyses_days"), metrics.get("total_users"), 1, 2),
        "analyses_per_active_user_days": _ratio(metrics.get("analyses_days"), metrics.get("active_users_days"), 1, 2),
        "purchase_conversion_days": _ratio(metrics.get("token_purchases_days"), metrics.get("total_users"), 100, 1),
        "revenue_per_user_days": _ratio(metrics.get("revenue_ton_days"), metrics.get("total_users"), 1, 2),
        "revenue_per_active_user_days": _ratio(metrics.get("revenue_ton_days"), metrics.get("active_users_days"), 1, 2),
        "referral_ratio": _ratio(metrics.get("referrals_days"), metrics.get("new_users_days"), 1, 2),
    }
    for key, value in derived.items():
        _set_metric(snapshot, key, value)
    _set_metric(snapshot, "growth_quality", _growth_quality(metrics))


def _add_action(actions: List[Dict[str, Any]], name: str, total: Optional[int], in_period: Optional[int]) -> None:
    if total is None and in_period is None:
        return
    actions.append({"name": name, "total": total, "in_period": in_period})


def get_project_metrics_snapshot(days: int = 7) -> Dict[str, Any]:
    """Return read-only project metrics from existing tables.

    Missing or untrackable metrics are returned as None and listed in missing_metrics.
    This function intentionally does not mutate balances, purchases, referrals, checks, or analyses.
    """
    safe_days = max(1, min(int(days or 7), 365))
    now = datetime.utcnow()
    since_days = now - timedelta(days=safe_days)
    since_24h = now - timedelta(hours=24)

    metrics = {
        "total_users": None,
        "new_users_24h": None,
        "new_users_days": None,
        "active_users_days": None,
        "total_analyses": None,
        "analyses_24h": None,
        "analyses_days": None,
        "total_token_purchases": None,
        "token_purchases_days": None,
        "revenue_ton_total": None,
        "revenue_ton_days": None,
        "purchase_intents_total": None,
        "purchase_intents_days": None,
        "referral_count": None,
        "referrals_days": None,
        "created_checks_total": None,
        "created_checks_days": None,
        "claimed_checks_total": None,
        "claimed_checks_days": None,
        "watchlist_count": None,
        "watchlist_active_count": None,
        "top_user_actions": None,
        "activation_rate_days": None,
        "new_user_growth_rate_days": None,
        "analysis_conversion_days": None,
        "analyses_per_user_days": None,
        "analyses_per_active_user_days": None,
        "purchase_conversion_days": None,
        "revenue_per_user_days": None,
        "revenue_per_active_user_days": None,
        "referral_ratio": None,
        "growth_quality": None,
    }
    snapshot: Dict[str, Any] = {
        "period_days": safe_days,
        "generated_at": now.isoformat(),
        "metrics": metrics,
        "missing_metrics": list(metrics.keys()),
        "notes": [],
        "errors": [],
    }

    try:
        conn = get_connection()
    except Exception as exc:
        return _empty_snapshot(safe_days, f"База данных недоступна: {exc.__class__.__name__}")

    actions: List[Dict[str, Any]] = []
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        plain_cursor = conn.cursor()
        table_columns: Dict[str, List[str]] = {}

        def has_table(table: str) -> bool:
            if table not in table_columns:
                if _table_exists(plain_cursor, table):
                    table_columns[table] = _columns(plain_cursor, table)
                else:
                    table_columns[table] = []
            return bool(table_columns[table])

        def has_column(table: str, column: str) -> bool:
            return has_table(table) and column in table_columns[table]

        if has_table("users"):
            _safe_metric(snapshot, "total_users", lambda: _count(plain_cursor, "users"))
            if has_column("users", "created_at"):
                where_24, params_24 = _timestamp_filter("created_at", since_24h)
                where_days, params_days = _timestamp_filter("created_at", since_days)
                _safe_metric(snapshot, "new_users_24h", lambda: _count(plain_cursor, "users", where_24, params_24))
                _safe_metric(snapshot, "new_users_days", lambda: _count(plain_cursor, "users", where_days, params_days))
            if has_column("users", "referred_by"):
                _safe_metric(snapshot, "referral_count", lambda: _count(plain_cursor, "users", "referred_by IS NOT NULL"))
                if has_column("users", "created_at"):
                    where_ref, params_ref = _timestamp_filter("created_at", since_days)
                    _safe_metric(snapshot, "referrals_days", lambda: _count(plain_cursor, "users", f"referred_by IS NOT NULL AND {where_ref}", params_ref))

        analysis_total = None
        analysis_days = None
        if has_table("analyses"):
            _safe_metric(snapshot, "total_analyses", lambda: _count(plain_cursor, "analyses"))
            analysis_total = snapshot["metrics"].get("total_analyses")
            if has_column("analyses", "created_at"):
                where_24, params_24 = _timestamp_filter("created_at", since_24h)
                where_days, params_days = _timestamp_filter("created_at", since_days)
                _safe_metric(snapshot, "analyses_24h", lambda: _count(plain_cursor, "analyses", where_24, params_24))
                _safe_metric(snapshot, "analyses_days", lambda: _count(plain_cursor, "analyses", where_days, params_days))
                analysis_days = snapshot["metrics"].get("analyses_days")
            _add_action(actions, "analyses", analysis_total, analysis_days)

        if has_table("web_analysis_history"):
            web_total = None
            web_days = None
            if has_column("web_analysis_history", "created_at"):
                where_days, params_days = _timestamp_filter("created_at", since_days)
                try:
                    web_total = _count(plain_cursor, "web_analysis_history")
                    web_days = _count(plain_cursor, "web_analysis_history", where_days, params_days)
                    _add_action(actions, "web_analysis_history", web_total, web_days)
                except Exception as exc:
                    snapshot["errors"].append({"metric": "web_analysis_history", "error": exc.__class__.__name__})

        if has_table("ton_purchase_intents"):
            _safe_metric(snapshot, "purchase_intents_total", lambda: _count(plain_cursor, "ton_purchase_intents"))
            if has_column("ton_purchase_intents", "created_at"):
                where_days, params_days = _timestamp_filter("created_at", since_days)
                _safe_metric(snapshot, "purchase_intents_days", lambda: _count(plain_cursor, "ton_purchase_intents", where_days, params_days))
            if has_column("ton_purchase_intents", "status") and has_column("ton_purchase_intents", "product_type"):
                fulfilled_where = "status = 'fulfilled' AND product_type = 'token_purchase'"
                _safe_metric(snapshot, "total_token_purchases", lambda: _count(plain_cursor, "ton_purchase_intents", fulfilled_where))
                if has_column("ton_purchase_intents", "fulfilled_at"):
                    where_days, params_days = _timestamp_filter("fulfilled_at", since_days)
                    _safe_metric(snapshot, "token_purchases_days", lambda: _count(plain_cursor, "ton_purchase_intents", f"{fulfilled_where} AND {where_days}", params_days))
                if has_column("ton_purchase_intents", "expected_amount_nano"):
                    _safe_metric(snapshot, "revenue_ton_total", lambda: _sum(plain_cursor, "ton_purchase_intents", "NULLIF(expected_amount_nano, '')::numeric", fulfilled_where) / 1_000_000_000)
                    if has_column("ton_purchase_intents", "fulfilled_at"):
                        where_days, params_days = _timestamp_filter("fulfilled_at", since_days)
                        _safe_metric(snapshot, "revenue_ton_days", lambda: _sum(plain_cursor, "ton_purchase_intents", "NULLIF(expected_amount_nano, '')::numeric", f"{fulfilled_where} AND {where_days}", params_days) / 1_000_000_000)
            _add_action(actions, "purchase_intents", snapshot["metrics"].get("purchase_intents_total"), snapshot["metrics"].get("purchase_intents_days"))

        if snapshot["metrics"].get("total_token_purchases") is None and has_table("transactions"):
            _safe_metric(snapshot, "total_token_purchases", lambda: _count(plain_cursor, "transactions"))
            if has_column("transactions", "created_at"):
                where_days, params_days = _timestamp_filter("created_at", since_days)
                _safe_metric(snapshot, "token_purchases_days", lambda: _count(plain_cursor, "transactions", where_days, params_days))
            if has_column("transactions", "ton_amount"):
                _safe_metric(snapshot, "revenue_ton_total", lambda: _sum(plain_cursor, "transactions", "ton_amount"))
                if has_column("transactions", "created_at"):
                    where_days, params_days = _timestamp_filter("created_at", since_days)
                    _safe_metric(snapshot, "revenue_ton_days", lambda: _sum(plain_cursor, "transactions", "ton_amount", where_days, params_days))

        if has_table("analysis_checks"):
            _safe_metric(snapshot, "created_checks_total", lambda: _count(plain_cursor, "analysis_checks"))
            if has_column("analysis_checks", "created_at"):
                where_days, params_days = _timestamp_filter("created_at", since_days)
                _safe_metric(snapshot, "created_checks_days", lambda: _count(plain_cursor, "analysis_checks", where_days, params_days))
            _add_action(actions, "created_checks", snapshot["metrics"].get("created_checks_total"), snapshot["metrics"].get("created_checks_days"))

        if has_table("analysis_check_claims"):
            _safe_metric(snapshot, "claimed_checks_total", lambda: _count(plain_cursor, "analysis_check_claims"))
            if has_column("analysis_check_claims", "claimed_at"):
                where_days, params_days = _timestamp_filter("claimed_at", since_days)
                _safe_metric(snapshot, "claimed_checks_days", lambda: _count(plain_cursor, "analysis_check_claims", where_days, params_days))
            _add_action(actions, "claimed_checks", snapshot["metrics"].get("claimed_checks_total"), snapshot["metrics"].get("claimed_checks_days"))

        if has_table("watchlist"):
            _safe_metric(snapshot, "watchlist_count", lambda: _count(plain_cursor, "watchlist"))
            if has_column("watchlist", "is_closed"):
                _safe_metric(snapshot, "watchlist_active_count", lambda: _count(plain_cursor, "watchlist", "is_closed = 0"))
            _add_action(actions, "watchlist", snapshot["metrics"].get("watchlist_count"), None)

        active_sources: List[str] = []
        union_parts: List[str] = []
        if has_column("analyses", "user_id") and has_column("analyses", "created_at"):
            where_days, _ = _timestamp_filter("created_at", since_days)
            union_parts.append(f"SELECT user_id FROM analyses WHERE user_id IS NOT NULL AND user_id <> 0 AND {where_days}")
            active_sources.append("analyses")
        if has_column("web_analysis_history", "user_id") and has_column("web_analysis_history", "created_at"):
            where_days, _ = _timestamp_filter("created_at", since_days)
            union_parts.append(f"SELECT user_id FROM web_analysis_history WHERE user_id IS NOT NULL AND {where_days}")
            active_sources.append("web_analysis_history")
        if has_column("watchlist", "user_id") and has_column("watchlist", "created_at"):
            where_days, _ = _timestamp_filter("created_at", since_days)
            union_parts.append(f"SELECT user_id FROM watchlist WHERE user_id IS NOT NULL AND {where_days}")
            active_sources.append("watchlist")
        if union_parts:
            try:
                query = "SELECT COUNT(DISTINCT user_id) FROM (" + " UNION ALL ".join(union_parts) + ") active_users"
                plain_cursor.execute(query, tuple([since_days] * len(union_parts)))
                row = plain_cursor.fetchone()
                _set_metric(snapshot, "active_users_days", int(row[0] or 0) if row else 0)
                snapshot["active_user_sources"] = active_sources
            except Exception as exc:
                snapshot["errors"].append({"metric": "active_users_days", "error": exc.__class__.__name__})

        if actions:
            actions.sort(key=lambda item: (item.get("in_period") if item.get("in_period") is not None else -1, item.get("total") or 0), reverse=True)
            _set_metric(snapshot, "top_user_actions", actions[:5])
        else:
            snapshot["notes"].append("Событийная аналитика пока не подключена.")

        _add_derived_metrics(snapshot)
        snapshot["missing_metrics"] = sorted(snapshot["missing_metrics"])
        return snapshot
    except Exception as exc:
        return _empty_snapshot(safe_days, f"Не удалось собрать метрики: {exc.__class__.__name__}")
    finally:
        conn.close()
