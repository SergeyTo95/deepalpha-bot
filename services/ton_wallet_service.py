import base64
import hashlib
import os
from datetime import datetime
from typing import Optional

from db.database import get_connection
from services.ton_chain_service import (
    get_ton_balance,
    nano_to_ton_display,
    send_boc_return_hash,
    validate_ton_address,
)

try:
    from cryptography.fernet import Fernet
except Exception:
    Fernet = None


def _enabled() -> bool:
    return (os.getenv("TON_WALLET_ENABLED") or "false").lower() == "true"


def _now() -> str:
    return datetime.utcnow().isoformat()


def _get_fernet() -> Optional[Fernet]:
    if Fernet is None:
        return None
    key = (os.getenv("MASTER_ENCRYPTION_KEY") or "").strip()
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except Exception:
        return None


def encrypt_secret(plain: str) -> str:
    f = _get_fernet()
    if not f:
        raise RuntimeError("encryption_unavailable")
    return f.encrypt(plain.encode()).decode()


def decrypt_secret(cipher: str) -> str:
    f = _get_fernet()
    if not f:
        raise RuntimeError("encryption_unavailable")
    return f.decrypt(cipher.encode()).decode()


def _generate_wallet_stub(user_id: int) -> tuple[str, str, str]:
    # TODO: replace stub with tonsdk v4r2 generation/signing.
    entropy = hashlib.sha256(f"{user_id}:{os.urandom(16).hex()}".encode()).hexdigest()
    words = " ".join(entropy[i:i + 4] for i in range(0, 48, 4))
    address = "EQ" + base64.urlsafe_b64encode(hashlib.sha256(entropy.encode()).digest()).decode().rstrip("=")[:46]
    pub = hashlib.sha256((entropy + "pub").encode()).hexdigest()
    return words, address, pub


def get_user_ton_wallet(user_id: int) -> dict | None:
    conn = get_connection(); cur = conn.cursor()
    cur.execute("""SELECT user_id,wallet_address,network,wallet_version,last_balance_nano,last_balance_checked_at,seed_reveal_used,seed_revealed_at
                   FROM user_ton_wallets WHERE user_id=%s""", (user_id,))
    row = cur.fetchone(); conn.close()
    if not row:
        return None
    return dict(zip(["user_id","wallet_address","network","wallet_version","last_balance_nano","last_balance_checked_at","seed_reveal_used","seed_revealed_at"], row))


def get_or_create_user_ton_wallet(user_id: int) -> dict:
    if not _enabled():
        return {"ok": False, "disabled": True, "error": "disabled"}
    existing = get_user_ton_wallet(user_id)
    if existing:
        return {"ok": True, **existing}
    seed, addr, pub = _generate_wallet_stub(user_id)
    try:
        encrypted = encrypt_secret(seed)
    except Exception:
        return {"ok": False, "error": "wallet_unavailable"}
    now = _now()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("""INSERT INTO user_ton_wallets (user_id,network,wallet_address,wallet_version,public_key,seed_encrypted,seed_reveal_used,status,created_at,updated_at)
                   VALUES (%s,%s,%s,'v4r2',%s,%s,FALSE,'active',%s,%s)
                   ON CONFLICT (user_id) DO NOTHING""", (user_id, os.getenv("TON_NETWORK", "testnet"), addr, pub, encrypted, now, now))
    conn.commit(); conn.close()
    return {"ok": True, **(get_user_ton_wallet(user_id) or {})}


def get_user_ton_balance(user_id: int, refresh: bool = True) -> dict:
    w = get_or_create_user_ton_wallet(user_id)
    if not w.get("ok"):
        return w
    balance_nano = int(w.get("last_balance_nano") or 0)
    checked_at = w.get("last_balance_checked_at")
    if refresh:
        try:
            balance_nano = get_ton_balance(w["wallet_address"])
            checked_at = _now()
            conn = get_connection(); cur = conn.cursor()
            cur.execute("UPDATE user_ton_wallets SET last_balance_nano=%s,last_balance_checked_at=%s,updated_at=%s WHERE user_id=%s", (str(balance_nano), checked_at, _now(), user_id))
            conn.commit(); conn.close()
        except Exception:
            pass
    return {"ok": True, "balance_nano": str(balance_nano), "balance_display": nano_to_ton_display(balance_nano), "wallet_address": w["wallet_address"], "network": w["network"], "last_balance_checked_at": checked_at}


def send_ton_from_user_wallet(user_id: int, destination_address: str, amount_nano: int, comment: str = "") -> dict:
    if not _enabled():
        return {"ok": False, "error": "disabled"}
    if not validate_ton_address(destination_address):
        return {"ok": False, "error": "invalid_destination"}
    if int(amount_nano) <= 0:
        return {"ok": False, "error": "invalid_amount"}
    w = get_user_ton_wallet(user_id)
    if not w:
        return {"ok": False, "error": "wallet_not_found"}
    try:
        bal = get_ton_balance(w["wallet_address"])
    except Exception:
        return {"ok": False, "error": "balance_unavailable"}
    reserve = 50_000_000
    if bal < int(amount_nano) + reserve:
        return {"ok": False, "error": "insufficient_balance"}
    result = send_boc_return_hash("")
    status = "submitted" if result.get("ok") else "failed"
    conn = get_connection(); cur = conn.cursor()
    cur.execute("""INSERT INTO ton_wallet_transactions (user_id,wallet_address,direction,amount_nano,fee_nano,tx_hash,destination_address,status,comment,created_at,updated_at,error)
                   VALUES (%s,%s,'withdrawal',%s,'0',%s,%s,%s,%s,%s,%s,%s)""",
                (user_id, w["wallet_address"], str(amount_nano), result.get("tx_hash"), destination_address, status, comment, _now(), _now(), None if result.get("ok") else result.get("error")))
    conn.commit(); conn.close()
    return {"ok": bool(result.get("ok")), "tx_hash": result.get("tx_hash"), "amount_nano": str(amount_nano), "destination_address": destination_address, "status": status, "error": None if result.get("ok") else result.get("error")}


def reveal_user_ton_seed_once(user_id: int) -> dict:
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT seed_encrypted,seed_reveal_used FROM user_ton_wallets WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close(); return {"ok": False, "error": "wallet_not_found"}
    if row[1]:
        conn.close(); return {"ok": False, "error": "already_revealed"}
    try:
        seed = decrypt_secret(row[0])
    except Exception:
        conn.close(); return {"ok": False, "error": "wallet_unavailable"}
    now = _now()
    cur.execute("UPDATE user_ton_wallets SET seed_reveal_used=TRUE,seed_revealed_at=%s,updated_at=%s WHERE user_id=%s AND seed_reveal_used=FALSE", (now, now, user_id))
    conn.commit(); conn.close()
    return {"ok": True, "seed_phrase": seed}
