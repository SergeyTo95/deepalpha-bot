from __future__ import annotations

from typing import Any

from db.database import get_connection


_PAYMENT_SCHEMA_LOCK_ID = 1_450_731_711
NETWORKS = ("tron", "solana", "ton", "bnb", "polygon")


def ensure_payment_tables() -> None:
    """Install additive VELIA multi-rail payment tables.

    Existing legacy TON tables (`payment_intents`, `transactions`,
    `pending_payments`, `ton_purchase_intents`) are deliberately untouched.
    They contain TON-specific constraints/semantics and remain the source of
    truth for their current runtime until a separately validated migration.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_payment_products (
                code TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                base_price_usd NUMERIC(18,6),
                token_amount BIGINT,
                subscription_days INTEGER,
                active BOOLEAN NOT NULL DEFAULT FALSE,
                metadata_json TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_payment_intents (
                id BIGSERIAL PRIMARY KEY,
                public_reference TEXT NOT NULL UNIQUE,
                user_id BIGINT NOT NULL,
                product_code TEXT,
                channel TEXT NOT NULL,
                network TEXT,
                asset TEXT,
                expected_amount_usd NUMERIC(18,6),
                expected_amount_asset NUMERIC(36,18),
                expected_amount_atomic NUMERIC(78,0),
                asset_decimals INTEGER,
                deposit_address TEXT,
                payment_memo TEXT,
                status TEXT NOT NULL DEFAULT 'created',
                idempotency_key TEXT NOT NULL UNIQUE,
                legacy_payment_intent_id BIGINT,
                expires_at TIMESTAMP,
                detected_at TIMESTAMP,
                confirmed_at TIMESTAMP,
                fulfilled_at TIMESTAMP,
                failed_at TIMESTAMP,
                failure_code TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_payment_intents_user_created "
            "ON velia_payment_intents(user_id, created_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_payment_intents_status_created "
            "ON velia_payment_intents(status, created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_payment_intents_network_status "
            "ON velia_payment_intents(network, status)"
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_payment_events (
                id BIGSERIAL PRIMARY KEY,
                event_key TEXT NOT NULL UNIQUE,
                intent_id BIGINT,
                source TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                occurred_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_payment_events_intent_created "
            "ON velia_payment_events(intent_id, created_at DESC)"
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_payment_transactions (
                id BIGSERIAL PRIMARY KEY,
                intent_id BIGINT,
                network TEXT NOT NULL,
                asset TEXT NOT NULL,
                tx_hash TEXT NOT NULL,
                transfer_index INTEGER NOT NULL DEFAULT 0,
                block_ref TEXT,
                sender_address TEXT,
                recipient_address TEXT NOT NULL,
                amount_atomic NUMERIC(78,0) NOT NULL,
                amount_asset NUMERIC(36,18),
                confirmations INTEGER NOT NULL DEFAULT 0,
                finality TEXT NOT NULL DEFAULT 'detected',
                detected_at TIMESTAMP NOT NULL DEFAULT NOW(),
                confirmed_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE(network, asset, tx_hash, transfer_index)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_payment_transactions_intent "
            "ON velia_payment_transactions(intent_id, created_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_payment_transactions_recipient "
            "ON velia_payment_transactions(network, asset, recipient_address)"
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_payment_fulfillments (
                id BIGSERIAL PRIMARY KEY,
                intent_id BIGINT NOT NULL UNIQUE,
                fulfillment_key TEXT NOT NULL UNIQUE,
                fulfillment_type TEXT NOT NULL,
                token_delta BIGINT,
                subscription_days INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error_code TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                fulfilled_at TIMESTAMP
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_payment_fulfillments_status "
            "ON velia_payment_fulfillments(status, created_at)"
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_payment_worker_state (
                network TEXT PRIMARY KEY,
                asset TEXT NOT NULL DEFAULT 'USDT',
                enabled BOOLEAN NOT NULL DEFAULT FALSE,
                mode TEXT NOT NULL DEFAULT 'foundation_disabled',
                status TEXT NOT NULL DEFAULT 'disabled',
                cursor_value TEXT,
                chain_height BIGINT,
                lag_blocks BIGINT,
                last_poll_at TIMESTAMP,
                last_success_at TIMESTAMP,
                last_error_code TEXT,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        for network in NETWORKS:
            cursor.execute(
                """
                INSERT INTO velia_payment_worker_state(network, asset, enabled, mode, status)
                VALUES (%s, 'USDT', FALSE, 'foundation_disabled', 'disabled')
                ON CONFLICT (network) DO NOTHING
                """,
                (network,),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cursor.close()
        finally:
            conn.close()


def ensure_payment_tables_serialized() -> None:
    """Serialize payment DDL across web/worker replicas with one DB lock."""
    lock_conn = get_connection()
    cursor = lock_conn.cursor()
    locked = False
    try:
        cursor.execute("SELECT pg_advisory_lock(%s)", (_PAYMENT_SCHEMA_LOCK_ID,))
        locked = True
        ensure_payment_tables()
    finally:
        if locked:
            try:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (_PAYMENT_SCHEMA_LOCK_ID,))
            except Exception:
                pass
        try:
            cursor.close()
        finally:
            lock_conn.close()


def payment_schema_lock_id() -> int:
    return _PAYMENT_SCHEMA_LOCK_ID
