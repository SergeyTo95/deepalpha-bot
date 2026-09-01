import os
import json
import shutil
import subprocess
from typing import Any, Dict, Optional, Tuple

from db.database import get_connection, get_setting

HARDCODED_TON_FALLBACK = "UQB7mMWEGE4reqMvHG5zPcHl9fQUy6L91UJhiXgyx772kuUv"
PREFERRED_TEMP_FALLBACK = "UQD7tACcY9se3vvNA5urQ-bvX1t0FGR3-t-tuha3yCOKQ0FM"


def normalize_ton_address(address: str) -> str:
    return (address or "").strip()


def _wallet_ready() -> bool:
    cmd = os.getenv("TON_WALLET_GENERATOR_CMD", "").strip()
    if not cmd:
        return False
    exe = cmd.split()[0]
    return bool(shutil.which(exe))


def encrypt_secret(secret: str) -> str:
    key = os.getenv("TON_WALLET_SECRET_KEY", "").encode("utf-8")
    if not key:
        raise RuntimeError("TON_WALLET_SECRET_KEY is not configured")
    raw = secret.encode("utf-8")
    out = bytes(raw[i] ^ key[i % len(key)] for i in range(len(raw)))
    return out.hex()


def decrypt_secret(secret_encrypted: str) -> str:
    key = os.getenv("TON_WALLET_SECRET_KEY", "").encode("utf-8")
    if not key:
        raise RuntimeError("TON_WALLET_SECRET_KEY is not configured")
    raw = bytes.fromhex(secret_encrypted)
    out = bytes(raw[i] ^ key[i % len(key)] for i in range(len(raw)))
    return out.decode("utf-8")


def _generate_wallet_real() -> Tuple[str, str]:
    cmd = os.getenv("TON_WALLET_GENERATOR_CMD", "").strip()
    if not cmd:
        raise RuntimeError("TON_WALLET_GENERATOR_CMD is not configured")
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise RuntimeError("wallet generator command failed")
    payload = json.loads((proc.stdout or "").strip() or "{}")
    wallet_address = normalize_ton_address(payload.get("wallet_address", ""))
    seed_phrase = (payload.get("seed_phrase", "") or "").strip()
    if not wallet_address or not seed_phrase:
        raise RuntimeError("wallet generator returned invalid payload")
    return wallet_address, seed_phrase


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


def create_cashier_payment_wallet(admin_user_id: int) -> Dict[str, Any]:
    if not _wallet_ready():
        return {"ok": False, "error": "wallet_generator_not_configured"}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        wallet_address, seed_phrase = _generate_wallet_real()
        seed_encrypted = encrypt_secret(seed_phrase)
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
        wallet_id = int(cursor.fetchone()[0])
        conn.commit()
        return {
            "ok": True,
            "wallet_address": wallet_address,
            "wallet": {"id": wallet_id, "wallet_address": wallet_address, "network": "MAINNET", "status": "active"},
        }
    except Exception as e:
        conn.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def reveal_cashier_payment_wallet_seed_once(admin_user_id: int) -> Dict[str, Any]:
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
            return {"ok": False, "error": "wallet_not_found"}
        wallet_id, seed_encrypted, used = row
        if used:
            conn.rollback()
            return {"ok": False, "error": "already_revealed"}
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
            return {"ok": False, "error": "reveal_race_condition"}
        seed_phrase = decrypt_secret(seed_encrypted)
        conn.commit()
        return {"ok": True, "seed_phrase": seed_phrase}
    except Exception as e:
        conn.rollback()
        return {"ok": False, "error": str(e)}
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

    preferred_fallback = normalize_ton_address(PREFERRED_TEMP_FALLBACK)
    if preferred_fallback:
        return preferred_fallback
    return HARDCODED_TON_FALLBACK


def get_cashier_payment_wallet_balance() -> float:
    from services.ton_service import get_wallet_balance

    wallet = get_active_cashier_payment_wallet()
    if not wallet:
        return 0.0
    return get_wallet_balance(wallet["wallet_address"])
