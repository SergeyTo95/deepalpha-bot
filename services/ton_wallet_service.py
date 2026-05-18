import base64
import os
from datetime import datetime
from typing import Optional

from db.database import get_connection
from services.ton_chain_service import (
    get_ton_balance,
    get_wallet_seqno,
    nano_to_ton_display,
    normalize_ton_address,
    send_boc_return_hash,
    validate_ton_address,
)

try:
    from cryptography.fernet import Fernet
except Exception:
    Fernet = None

try:
    from tonsdk.contract.wallet import Wallets, WalletVersionEnum
    from tonsdk.crypto import mnemonic_is_valid, mnemonic_new, mnemonic_to_wallet_key
except Exception:
    Wallets = None
    WalletVersionEnum = None
    mnemonic_new = None
    mnemonic_to_wallet_key = None
    mnemonic_is_valid = None


def _enabled() -> bool:
    return (os.getenv("TON_WALLET_ENABLED") or "false").lower() == "true"


def _now() -> str:
    return datetime.utcnow().isoformat()


def _wallet_ready() -> bool:
    if not all([Wallets, WalletVersionEnum, mnemonic_new, mnemonic_to_wallet_key]):
        return False
    if not hasattr(WalletVersionEnum, "v4r2"):
        return False
    try:
        words = mnemonic_new()
        public_key, _private_key = mnemonic_to_wallet_key(words)
        wallet = _try_wallet_from_public_key(public_key)
        address = wallet.address.to_string(is_user_friendly=True, is_bounceable=False, is_url_safe=True)
        return bool(address)
    except Exception:
        return False


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


def _try_wallet_from_public_key(public_key: bytes):
    ver = WalletVersionEnum.v4r2

    def _valid_wallet(w):
        try:
            a = w.address.to_string(is_user_friendly=True, is_bounceable=False, is_url_safe=True)
            return bool(a)
        except Exception:
            return False

    if hasattr(Wallets, "ALL") and isinstance(getattr(Wallets, "ALL", None), dict) and ver in Wallets.ALL:
        wallet_cls = Wallets.ALL[ver]
        variants = [
            ((), {"publicKey": public_key, "wc": 0}),
            ((), {"publicKey": public_key, "workchain": 0}),
            ((), {"public_key": public_key, "wc": 0}),
            ((), {"public_key": public_key, "workchain": 0}),
            (({"publicKey": public_key, "wc": 0},), {}),
            (({"publicKey": public_key, "workchain": 0},), {}),
            (({"public_key": public_key, "wc": 0},), {}),
            (({"public_key": public_key, "workchain": 0},), {}),
            ((), {"publicKey": public_key}),
            (({"publicKey": public_key},), {}),
        ]
        for args, kwargs in variants:
            try:
                w = wallet_cls(*args, **kwargs)
                if _valid_wallet(w):
                    return w
            except Exception:
                pass

    if hasattr(Wallets, "from_public_key") and callable(getattr(Wallets, "from_public_key")):
        for kwargs in ({"workchain": 0}, {"wc": 0}):
            try:
                w = Wallets.from_public_key(public_key, ver, **kwargs)
                if _valid_wallet(w):
                    return w
            except Exception:
                pass

    raise RuntimeError("setup_required")


def _build_wallet_from_public_key(public_key: bytes):
    if not _wallet_ready():
        raise RuntimeError("setup_required")
    return _try_wallet_from_public_key(public_key)


def _build_wallet_from_mnemonics(words: list[str]):
    if not _wallet_ready():
        raise RuntimeError("setup_required")
    public_key, private_key = mnemonic_to_wallet_key(words)
    wallet = _build_wallet_from_public_key(public_key)
    return wallet, public_key, private_key


def _generate_wallet_real() -> tuple[str, str, str]:
    if not _wallet_ready():
        raise RuntimeError("setup_required")
    mnemonics = mnemonic_new()
    wallet, public_key, _private_key = _build_wallet_from_mnemonics(mnemonics)
    address = wallet.address.to_string(is_user_friendly=True, is_bounceable=False, is_url_safe=True)
    return " ".join(mnemonics), address, public_key.hex()


def _wallet_from_mnemonic(seed_phrase: str):
    words = [w for w in (seed_phrase or "").split() if w]
    if len(words) < 12:
        raise RuntimeError("setup_required")
    wallet, public_key, private_key = _build_wallet_from_mnemonics(words)
    address = wallet.address.to_string(is_user_friendly=True, is_bounceable=False, is_url_safe=True)
    return wallet, public_key, private_key, address


def _safe_wallet_data(row):
    return dict(zip(["user_id", "wallet_address", "network", "wallet_version", "last_balance_nano", "last_balance_checked_at", "seed_reveal_used", "seed_revealed_at"], row))


def get_user_ton_wallet(user_id: int) -> dict | None:
    conn = get_connection(); cur = conn.cursor()
    cur.execute("""SELECT user_id,wallet_address,network,wallet_version,last_balance_nano,last_balance_checked_at,seed_reveal_used,seed_revealed_at
                   FROM user_ton_wallets WHERE user_id=%s""", (user_id,))
    row = cur.fetchone(); conn.close()
    return _safe_wallet_data(row) if row else None


def get_or_create_user_ton_wallet(user_id: int) -> dict:
    if not _enabled():
        return {"ok": False, "disabled": True, "error": "disabled"}
    if not _wallet_ready():
        return {"ok": False, "error": "setup_required"}
    existing = get_user_ton_wallet(user_id)
    if existing:
        return {"ok": True, **existing}
    try:
        seed_phrase, address, public_key = _generate_wallet_real()
        encrypted = encrypt_secret(seed_phrase)
    except Exception:
        return {"ok": False, "error": "wallet_unavailable"}
    now = _now()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("""INSERT INTO user_ton_wallets (user_id,network,wallet_address,wallet_version,public_key,seed_encrypted,seed_reveal_used,status,created_at,updated_at)
                   VALUES (%s,%s,%s,'v4r2',%s,%s,FALSE,'active',%s,%s)
                   ON CONFLICT (user_id) DO NOTHING""", (user_id, os.getenv("TON_NETWORK", "testnet"), address, public_key, encrypted, now, now))
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


def _record_tx(user_id: int, wallet_address: str, amount_nano: int, destination: str, status: str, tx_hash: Optional[str], comment: str, error: Optional[str]) -> None:
    conn = get_connection(); cur = conn.cursor()
    cur.execute("""INSERT INTO ton_wallet_transactions (user_id,wallet_address,direction,amount_nano,fee_nano,tx_hash,destination_address,status,comment,created_at,updated_at,error)
                   VALUES (%s,%s,'withdrawal',%s,'0',%s,%s,%s,%s,%s,%s,%s)""",
                (user_id, wallet_address, str(amount_nano), tx_hash, destination, status, comment, _now(), _now(), error))
    conn.commit(); conn.close()


def send_ton_from_user_wallet(user_id: int, destination_address: str, amount_nano: int, comment: str = "") -> dict:
    if not _enabled():
        return {"ok": False, "error": "disabled"}
    if not _wallet_ready():
        return {"ok": False, "error": "setup_required"}
    destination = normalize_ton_address(destination_address)
    if not validate_ton_address(destination):
        return {"ok": False, "error": "invalid_address"}
    if int(amount_nano) <= 0:
        return {"ok": False, "error": "invalid_amount"}
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT wallet_address, seed_encrypted FROM user_ton_wallets WHERE user_id=%s", (user_id,))
    row = cur.fetchone(); conn.close()
    if not row:
        return {"ok": False, "error": "wallet_not_found"}
    wallet_address, seed_encrypted = row

    try:
        balance = get_ton_balance(wallet_address)
    except Exception:
        _record_tx(user_id, wallet_address, amount_nano, destination, "failed", None, comment, "balance_unavailable")
        return {"ok": False, "error": "balance_unavailable"}
    reserve = 50_000_000
    if balance < int(amount_nano) + reserve:
        return {"ok": False, "error": "insufficient_balance"}

    try:
        seed_phrase = decrypt_secret(seed_encrypted)
        wallet, _public_key, private_key, derived_address = _wallet_from_mnemonic(seed_phrase)
        if normalize_ton_address(derived_address) != normalize_ton_address(wallet_address):
            raise RuntimeError("wallet_mismatch")
        seqno = get_wallet_seqno(wallet_address)
        transfer = wallet.create_transfer_message(
            to_addr=destination,
            amount=int(amount_nano),
            seqno=seqno,
            payload=(comment or "")[:256],
            send_mode=3,
            private_key=private_key,
        )
        boc_bytes = transfer["message"].to_boc(False)
        boc_base64 = base64.b64encode(boc_bytes).decode()
    except Exception:
        _record_tx(user_id, wallet_address, amount_nano, destination, "failed", None, comment, "signing_failed")
        return {"ok": False, "error": "signing_failed"}

    result = send_boc_return_hash(boc_base64)
    if not result.get("ok"):
        _record_tx(user_id, wallet_address, amount_nano, destination, "failed", None, comment, "send_failed")
        return {"ok": False, "error": "send_failed"}

    tx_hash = result.get("tx_hash")
    _record_tx(user_id, wallet_address, amount_nano, destination, "submitted", tx_hash, comment, None)
    return {"ok": True, "tx_hash": tx_hash, "amount_nano": str(amount_nano), "destination_address": destination, "status": "submitted"}


def reveal_user_ton_seed_once(user_id: int) -> dict:
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT seed_encrypted,seed_reveal_used FROM user_ton_wallets WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close(); return {"ok": False, "error": "wallet_not_found"}
    if row[1]:
        conn.close(); return {"ok": False, "error": "already_revealed"}
    try:
        seed = decrypt_secret(row[0]).strip()
        words = [w for w in seed.split() if w]
        if len(words) < 12:
            conn.close(); return {"ok": False, "error": "wallet_unavailable"}
        if mnemonic_is_valid and not mnemonic_is_valid(words):
            conn.close(); return {"ok": False, "error": "wallet_unavailable"}
    except Exception:
        conn.close(); return {"ok": False, "error": "wallet_unavailable"}
    now = _now()
    cur.execute("UPDATE user_ton_wallets SET seed_reveal_used=TRUE,seed_revealed_at=%s,updated_at=%s WHERE user_id=%s AND seed_reveal_used=FALSE", (now, now, user_id))
    conn.commit(); conn.close()
    return {"ok": True, "seed_phrase": seed}
