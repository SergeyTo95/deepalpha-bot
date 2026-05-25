import os
from datetime import datetime
from typing import Any, Dict, Optional

from db.database import get_connection, get_setting

HARDCODED_TON_FALLBACK = "UQB7mMWEGE4reqMvHG5zPcHl9fQUy6L91UJhiXgyx772kuUv"


def get_active_cashier_payment_wallet() -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, wallet_address, network, status, created_by, created_at, updated_at,
                   seed_reveal_used, seed_revealed_at
            FROM cashier_payment_wallets
            WHERE status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "wallet_address": row[1],
            "network": row[2],
            "status": row[3],
            "created_by": row[4],
            "created_at": row[5],
            "updated_at": row[6],
            "seed_reveal_used": bool(row[7]) if row[7] is not None else False,
            "seed_revealed_at": row[8],
        }
    finally:
        conn.close()


def create_cashier_payment_wallet(wallet_address: str, seed_encrypted: str, admin_user_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE cashier_payment_wallets SET status = 'inactive', updated_at = NOW() WHERE status = 'active'")
        cursor.execute(
            """
            INSERT INTO cashier_payment_wallets
            (wallet_address, seed_encrypted, network, status, created_by, created_at, updated_at, seed_reveal_used)
            VALUES (%s, %s, 'MAINNET', 'active', %s, NOW(), NOW(), FALSE)
            RETURNING id
            """,
            (wallet_address, seed_encrypted, admin_user_id),
        )
        wallet_id = cursor.fetchone()[0]
        conn.commit()
        return wallet_id
    finally:
        conn.close()


def reveal_cashier_payment_wallet_seed_once(admin_user_id: int) -> Optional[str]:
    _ = admin_user_id
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, seed_encrypted, seed_reveal_used
            FROM cashier_payment_wallets
            WHERE status = 'active'
            ORDER BY id DESC
            LIMIT 1
            FOR UPDATE
            """
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return None
        wallet_id, seed_encrypted, used = row
        if used:
            conn.rollback()
            return None
        cursor.execute(
            """
            UPDATE cashier_payment_wallets
            SET seed_reveal_used = TRUE, seed_revealed_at = NOW(), updated_at = NOW()
            WHERE id = %s AND seed_reveal_used = FALSE
            """,
            (wallet_id,),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return None
        conn.commit()
        return seed_encrypted
    finally:
        conn.close()


def resolve_ton_purchase_project_wallet() -> str:
    cashier_wallet = get_active_cashier_payment_wallet()
    if cashier_wallet and cashier_wallet.get("wallet_address"):
        return cashier_wallet["wallet_address"]

    env_wallet = os.getenv("TON_PROJECT_WALLET", "").strip()
    if env_wallet:
        return env_wallet

    wallet_setting = get_setting("ton_project_wallet", "").strip()
    if wallet_setting:
        return wallet_setting

    platform_wallet_setting = get_setting("ton_platform_wallet", "").strip()
    if platform_wallet_setting:
        return platform_wallet_setting

    return HARDCODED_TON_FALLBACK


def get_cashier_payment_wallet_balance() -> float:
    from services.ton_service import get_wallet_balance

    wallet = get_active_cashier_payment_wallet()
    if not wallet:
        return 0.0
    return get_wallet_balance(wallet["wallet_address"])
