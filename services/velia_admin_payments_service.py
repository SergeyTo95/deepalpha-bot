from __future__ import annotations

from typing import Any, Dict, List

import psycopg2.extras

from db.database import get_connection
from services.payments.config import crypto_checkout_enabled


def _table_exists(cursor: Any, table_name: str) -> bool:
    cursor.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
    row = cursor.fetchone()
    if not row:
        return False
    if isinstance(row, dict):
        return bool(next(iter(row.values()), None))
    return bool(row[0])


def _rows(cursor: Any) -> List[Dict[str, Any]]:
    return [dict(row) for row in (cursor.fetchall() or [])]


def payment_admin_snapshot() -> Dict[str, Any]:
    """Read-only payment telemetry for owner Control Center."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if not _table_exists(cur, "velia_payment_intents"):
            return {
                "available": False,
                "reason": "velia_payment_schema_missing",
                "public_checkout_enabled": crypto_checkout_enabled(),
                "signing_capability": False,
            }

        cur.execute(
            """
            SELECT
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') AS created_24h,
              COUNT(*) FILTER (WHERE status='created') AS created,
              COUNT(*) FILTER (WHERE status='awaiting_payment') AS awaiting_payment,
              COUNT(*) FILTER (WHERE status='detected') AS detected,
              COUNT(*) FILTER (WHERE status='confirming') AS confirming,
              COUNT(*) FILTER (WHERE status='confirmed') AS confirmed,
              COUNT(*) FILTER (WHERE status='fulfilled') AS fulfilled,
              COUNT(*) FILTER (WHERE status='failed') AS failed,
              COALESCE(SUM(expected_amount_usd) FILTER (
                WHERE status IN ('confirmed','fulfilled')
                  AND created_at >= NOW() - INTERVAL '30 days'
              ),0) AS confirmed_amount_30d_usd
            FROM velia_payment_intents
            """
        )
        summary = dict(cur.fetchone() or {})

        cur.execute(
            """
            SELECT channel, COUNT(*) AS intents,
                   COALESCE(SUM(expected_amount_usd) FILTER (WHERE status IN ('confirmed','fulfilled')),0) AS confirmed_amount_usd
            FROM velia_payment_intents
            GROUP BY channel
            ORDER BY intents DESC, channel
            """
        )
        channels = _rows(cur)

        cur.execute(
            """
            SELECT network,asset,enabled,mode,status,cursor_value,chain_height,lag_blocks,
                   last_poll_at,last_success_at,last_error_code,updated_at
            FROM velia_payment_worker_state
            ORDER BY CASE network
                WHEN 'tron' THEN 1 WHEN 'solana' THEN 2 WHEN 'ton' THEN 3
                WHEN 'bnb' THEN 4 WHEN 'polygon' THEN 5 ELSE 99 END, network
            """
        )
        networks = _rows(cur)

        cur.execute(
            """
            SELECT i.id,i.public_reference,i.user_id,u.username,u.first_name,
                   i.product_code,i.channel,i.network,i.asset,i.expected_amount_usd,
                   i.expected_amount_asset,i.deposit_address,i.status,i.expires_at,
                   i.created_at,i.updated_at
            FROM velia_payment_intents i
            LEFT JOIN users u ON u.user_id=i.user_id
            ORDER BY i.id DESC
            LIMIT 100
            """
        )
        intents = _rows(cur)

        cur.execute(
            """
            SELECT status,COUNT(*) AS count
            FROM velia_payment_fulfillments
            GROUP BY status ORDER BY status
            """
        )
        fulfillments = _rows(cur)

        legacy = {"available": False}
        if _table_exists(cur, "payment_intents"):
            cur.execute(
                "SELECT COUNT(*) AS intents, COUNT(*) FILTER (WHERE status='fulfilled') AS fulfilled FROM payment_intents"
            )
            legacy = {"available": True, **dict(cur.fetchone() or {})}
            if _table_exists(cur, "transactions"):
                cur.execute("SELECT COUNT(*) AS transactions FROM transactions")
                legacy["transactions"] = int((cur.fetchone() or {}).get("transactions") or 0)

        successful_poll_networks = [
            str(row.get("network") or "")
            for row in networks
            if row.get("last_success_at")
        ]
        return {
            "available": True,
            "public_checkout_enabled": crypto_checkout_enabled(),
            "signing_capability": False,
            "successful_poll_networks": successful_poll_networks,
            "summary": summary,
            "channels": channels,
            "networks": networks,
            "intents": intents,
            "fulfillments": fulfillments,
            "legacy_ton": legacy,
            "scope_note": (
                "Watch-only payment telemetry. Worker polling is evidenced by per-network poll timestamps and state; "
                "public checkout is a separate backend gate. No signing, seed, private-key or sweep capability is exposed here."
            ),
        }
    except Exception as exc:
        return {
            "available": False,
            "reason": exc.__class__.__name__,
            "public_checkout_enabled": crypto_checkout_enabled(),
            "signing_capability": False,
        }
    finally:
        cur.close()
        conn.close()