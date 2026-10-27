"""Durable, atomic reservations before a free-tier HTTP request.

Reservations survive restarts and are never refunded after an ambiguous timeout.
Use dedicated provider accounts/keys: usage outside this app is not observable.
"""
from __future__ import annotations

import hashlib
import os

from db.database import get_connection


class FreeAPIError(ValueError):
    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.code, self.status = code, status


def bounded_limit(name: str, default: int, maximum: int) -> int:
    try:
        return max(0, min(maximum, int(os.getenv(name, str(default)))))
    except (ValueError, TypeError):
        return default


def free_only() -> bool:
    return os.getenv("VELIA_FREE_APIS_ONLY", "true").strip().lower() not in {"false", "0", "off"}


def reserve(provider: str, account_key: str, units: int, limit: int, *, user_id: int | None = None,
            daily_user_limit: int | None = None) -> None:
    if not account_key or units < 1 or limit < units:
        raise FreeAPIError("free_api_quota_exhausted", 429)
    # Neither the API key, query, text nor audio is persisted in the ledger.
    account = hashlib.sha256(account_key.encode()).hexdigest()
    conn = cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # DDL is serialized across workers; the upsert below serializes actual
        # reservations on the same account/month even across replicas.
        cursor.execute("SELECT pg_advisory_xact_lock(1904202610)")
        cursor.execute("""CREATE TABLE IF NOT EXISTS velia_free_api_usage (
            provider TEXT NOT NULL, account TEXT NOT NULL, period TEXT NOT NULL,
            used BIGINT NOT NULL CHECK (used >= 0),
            PRIMARY KEY (provider, account, period))""")
        cursor.execute("SELECT to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM'), "
                       "to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD')")
        month, day = cursor.fetchone()
        buckets = [(provider, month, limit)]
        if user_id is not None and daily_user_limit is not None:
            buckets.append((f"{provider}:user:{int(user_id)}", day, daily_user_limit))
        for scope, period, cap in buckets:
            if units > cap:
                raise FreeAPIError("free_api_quota_exhausted", 429)
            cursor.execute("""INSERT INTO velia_free_api_usage (provider, account, period, used)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (provider, account, period) DO UPDATE
                SET used = velia_free_api_usage.used + EXCLUDED.used
                WHERE velia_free_api_usage.used + EXCLUDED.used <= %s
                RETURNING used""", (scope, account, period, units, cap))
            if cursor.fetchone() is None:
                raise FreeAPIError("free_api_quota_exhausted", 429)
        conn.commit()
    except FreeAPIError:
        if conn is not None:
            conn.rollback()
        raise
    except Exception:
        if conn is not None:
            conn.rollback()
        # A broken/missing database must not turn the budget into unlimited use.
        raise FreeAPIError("free_api_budget_unavailable") from None
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()
