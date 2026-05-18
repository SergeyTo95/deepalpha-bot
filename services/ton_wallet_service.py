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
    from tonsdk.crypto import mnemonic_is_valid, mnemonic_new
except Exception:
    Wallets = None
    WalletVersionEnum = None
    mnemonic_new = None
    mnemonic_is_valid = None


def _enabled() -> bool:
    return (os.getenv("TON_WALLET_ENABLED") or "false").lower() == "true"


def _now() -> str:
    return datetime.utcnow().isoformat()


def _wallet_ready() -> bool:
    if not all([Wallets, WalletVersionEnum, mnemonic_new]):
        return False
    if not hasattr(WalletVersionEnum, "v4r2"):
        return False
    has_create = hasattr(Wallets, "create") and callable(getattr(Wallets, "create"))
    has_from_mnemonics = hasattr(Wallets, "from_mnemonics") and callable(getattr(Wallets, "from_mnemonics"))
    return has_create and has_from_mnemonics



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


def _generate_wallet_real() -> tuple[str, str, str]:
    if not _wallet_ready():
        raise RuntimeError("setup_required")

    for kwargs in (
        {"version": WalletVersionEnum.v4r2, "workchain": 0},
        {"version": WalletVersionEnum.v4r2, "wc": 0},
    ):
        try:
            mnemonics, public_key, _private_key, wallet = Wallets.create(**kwargs)
            address = wallet.address.to_string(is_user_friendly=True, is_bounceable=False, is_url_safe=True)
            return " ".join(mnemonics), address, public_key.hex()
        except Exception:
            pass
    raise RuntimeError("setup_required")


def _wallet_from_mnemonic(seed_phrase: str):
    if not _wallet_ready():
        raise RuntimeError("setup_required")
    words = [w for w in (seed_phrase or "").split() if w]
    if len(words) < 12:
        raise RuntimeError("setup_required")

    for kwargs in (
        {"version": WalletVersionEnum.v4r2, "workchain": 0},
        {"version": WalletVersionEnum.v4r2, "wc": 0},
    ):
        try:
            result = Wallets.from_mnemonics(words, **kwargs)
            wallet, public_key, private_key = _parse_wallet_from_mnemonics_result(result)
            address = wallet.address.to_string(is_user_friendly=True, is_bounceable=False, is_url_safe=True)
            return wallet, public_key, private_key, address
        except Exception:
            pass
    raise RuntimeError("setup_required")




def _parse_wallet_from_mnemonics_result(res):
    wallet = None
    public_key = None
    private_key = None

    if isinstance(res, tuple):
        if len(res) == 3:
            a, b, c = res
            if hasattr(c, "address"):
                public_key, private_key, wallet = a, b, c
            elif hasattr(a, "address"):
                wallet, public_key, private_key = a, b, c
        elif len(res) == 4:
            a, b, c, d = res
            if hasattr(d, "address"):
                wallet = d
                public_key = b
                private_key = c

        if wallet is None:
            for item in res:
                if hasattr(item, "address"):
                    wallet = item
                    break

        if public_key is None or private_key is None:
            byte_items = [x for x in res if isinstance(x, (bytes, bytearray))]
            if len(byte_items) >= 2:
                public_key = public_key or byte_items[0]
                private_key = private_key or byte_items[1]
    else:
        if hasattr(res, "address"):
            wallet = res

    if wallet is None or private_key is None:
        raise RuntimeError("setup_required")

    return wallet, public_key, private_key



def _build_signed_transfer_message(wallet, private_key, destination_address, amount_nano, seqno, comment=""):
    payload = (comment or "")[:256]
    variants = [
        ((), {"to_addr": destination_address, "amount": int(amount_nano), "seqno": int(seqno), "payload": payload, "send_mode": 3}),
        ((), {"to_addr": destination_address, "amount": int(amount_nano), "seqno": int(seqno), "payload": payload}),
        ((destination_address, int(amount_nano), int(seqno)), {"payload": payload, "send_mode": 3}),
        ((destination_address, int(amount_nano), int(seqno), payload), {}),
        ((), {"to_addr": destination_address, "amount": int(amount_nano), "seqno": int(seqno), "payload": payload, "send_mode": 3, "signing_key": private_key}),
        ((), {"to_addr": destination_address, "amount": int(amount_nano), "seqno": int(seqno), "payload": payload, "send_mode": 3, "private_key": private_key}),
    ]
    for args, kwargs in variants:
        try:
            return wallet.create_transfer_message(*args, **kwargs)
        except TypeError:
            continue
        except Exception:
            continue
    raise RuntimeError("signing_failed")


def _extract_boc_from_transfer(transfer) -> str:
    msg = None
    if isinstance(transfer, dict):
        msg = transfer.get("message") or transfer.get("external_message") or transfer.get("boc")
    elif hasattr(transfer, "message"):
        msg = transfer.message
    else:
        msg = transfer

    if isinstance(msg, str):
        if not msg.strip():
            raise RuntimeError("signing_failed")
        return msg

    if hasattr(msg, "to_boc"):
        boc_bytes = msg.to_boc(False)
        return base64.b64encode(boc_bytes).decode()

    if hasattr(transfer, "to_boc"):
        boc_bytes = transfer.to_boc(False)
        return base64.b64encode(boc_bytes).decode()

    raise RuntimeError("signing_failed")

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
        transfer = _build_signed_transfer_message(
            wallet=wallet,
            private_key=private_key,
            destination_address=destination,
            amount_nano=amount_nano,
            seqno=seqno,
            comment=comment,
        )
        boc_base64 = _extract_boc_from_transfer(transfer)
        if not boc_base64:
            raise RuntimeError("signing_failed")
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
