"""Final commercial-launch compatibility patch.

This layer keeps zero-valued spend caps enforceable and aligns health metrics/warnings with the
migrated invoice states and configured payment provider.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

from db.database import get_connection
from services import developer_api_commercial_launch_service as launch
from services import developer_api_commercial_launch_v2_service as v2
from services import developer_api_commercial_service as legacy


_FINAL_TABLES_READY = False
_ORIGINAL_ENSURE = v2.ensure_commercial_launch_tables


def ensure_commercial_launch_tables() -> None:
    global _FINAL_TABLES_READY
    if _FINAL_TABLES_READY:
        return
    _ORIGINAL_ENSURE()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION enforce_api_credit_spend_limits()
            RETURNS trigger AS $$
            DECLARE
                v_daily INTEGER;
                v_monthly INTEGER;
                v_daily_used BIGINT;
                v_monthly_used BIGINT;
            BEGIN
                SELECT daily_spend_limit_credits, monthly_spend_limit_credits
                  INTO v_daily, v_monthly
                FROM api_clients WHERE id=NEW.client_id FOR UPDATE;

                IF v_daily IS NOT NULL THEN
                    SELECT COALESCE(SUM(units), 0) INTO v_daily_used
                    FROM api_credit_reservations
                    WHERE client_id=NEW.client_id AND status IN ('reserved','charged')
                      AND created_at >= date_trunc('day', NOW());
                    IF v_daily_used + NEW.units > v_daily THEN
                        RAISE EXCEPTION 'daily_credit_spend_limit_reached:%:%:%',
                            v_daily, v_daily_used, NEW.units;
                    END IF;
                END IF;

                IF v_monthly IS NOT NULL THEN
                    SELECT COALESCE(SUM(units), 0) INTO v_monthly_used
                    FROM api_credit_reservations
                    WHERE client_id=NEW.client_id AND status IN ('reserved','charged')
                      AND created_at >= date_trunc('month', NOW());
                    IF v_monthly_used + NEW.units > v_monthly THEN
                        RAISE EXCEPTION 'monthly_credit_spend_limit_reached:%:%:%',
                            v_monthly, v_monthly_used, NEW.units;
                    END IF;
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql
            """
        )
        conn.commit()
        _FINAL_TABLES_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def automatic_payment_worker_required() -> bool:
    return (
        v2.credit_purchases_enabled()
        and v2.invoice_provider_name() == "ton_treasury"
    )


def get_commercial_runtime_health(*, include_workers: bool = False) -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    stale = legacy.commercial_worker_stale_seconds()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE status IN ('pending','awaiting_payment','payment_detected','paid','crediting')
                ) AS pending,
                COUNT(*) FILTER (WHERE status='expired') AS expired,
                COUNT(*) FILTER (
                    WHERE status='credited' AND credited_at >= NOW() - INTERVAL '24 hours'
                ) AS paid_24h,
                COALESCE(SUM(credits) FILTER (
                    WHERE status='credited' AND credited_at >= NOW() - INTERVAL '24 hours'
                ), 0) AS credits_24h,
                EXTRACT(EPOCH FROM (
                    NOW() - MIN(created_at) FILTER (
                        WHERE status IN ('pending','awaiting_payment','payment_detected','paid','crediting')
                    )
                )) AS oldest_pending_age_seconds
            FROM api_credit_invoices
            """
        )
        metrics = launch._row(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            SELECT worker_id, status, current_job_id, started_at, last_seen_at,
                   EXTRACT(EPOCH FROM (NOW() - last_seen_at)) AS heartbeat_age_seconds,
                   (last_seen_at >= NOW() - make_interval(secs => %s)) AS fresh
            FROM api_worker_heartbeats
            WHERE worker_type='commercial'
            ORDER BY last_seen_at DESC LIMIT 20
            """,
            (stale,),
        )
        workers = launch._rows(cursor, cursor.fetchall())
        cursor.execute(
            "SELECT COUNT(*) FROM api_live_access_requests WHERE status='live_requested'"
        )
        request_row = cursor.fetchone()
        live_pending = int(
            (
                request_row[0]
                if not isinstance(request_row, dict)
                else next(iter(request_row.values()))
            )
            or 0
        )
    finally:
        cursor.close()
        conn.close()

    fresh = sum(1 for item in workers if bool(item.get("fresh")))
    worker_required = automatic_payment_worker_required()
    warnings: List[str] = []
    if worker_required and fresh == 0:
        warnings.append("no_fresh_commercial_worker")
    if worker_required and not legacy.incoming_enabled():
        warnings.append("treasury_incoming_disabled")
    result: Dict[str, Any] = {
        "status": "operational" if not warnings else "degraded",
        "enabled": legacy.commercial_launch_enabled(),
        "credit_purchases_enabled": v2.credit_purchases_enabled(),
        "payment_provider": v2.invoice_provider_name(),
        "automatic_payment_verification": worker_required,
        "live_keys_enabled": legacy.live_keys_globally_enabled(),
        "treasury_incoming_enabled": legacy.incoming_enabled(),
        "network": legacy._runtime_network(),
        "worker_required": worker_required,
        "worker_available": fresh > 0,
        "fresh_workers": fresh,
        "pending_invoices": int(metrics.get("pending") or 0),
        "expired_invoices": int(metrics.get("expired") or 0),
        "paid_24h": int(metrics.get("paid_24h") or 0),
        "credits_sold_24h": int(metrics.get("credits_24h") or 0),
        "oldest_pending_age_seconds": round(
            float(metrics.get("oldest_pending_age_seconds") or 0), 1
        ),
        "pending_live_requests": live_pending,
        "warnings": warnings,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if include_workers:
        result["workers"] = workers
    return result


# Make dynamically resolved callers use the final functions.
launch.ensure_commercial_launch_tables = ensure_commercial_launch_tables
v2.ensure_commercial_launch_tables = ensure_commercial_launch_tables
legacy.get_commercial_runtime_health = get_commercial_runtime_health

# Public worker surface.
run_commercial_worker_forever = v2.run_commercial_worker_forever
scan_payments_once = v2.scan_payments_once
