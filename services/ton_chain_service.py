import os
from decimal import Decimal, InvalidOperation
from typing import Any, Dict

import requests

try:
    from tonsdk.utils import Address
except Exception:
    Address = None


def _network() -> str:
    return (os.getenv("TON_NETWORK") or "testnet").strip().lower()


def _base_url() -> str:
    override = (os.getenv("TONCENTER_BASE_URL") or "").strip()
    if override:
        return override.rstrip("/")
    return "https://toncenter.com/api/v2" if _network() == "mainnet" else "https://testnet.toncenter.com/api/v2"


def _params() -> Dict[str, Any]:
    key = (os.getenv("TONCENTER_API_KEY") or "").strip()
    return {"api_key": key} if key else {}


def normalize_ton_address(address: str) -> str:
    a = (address or "").strip()
    if not a:
        return ""
    if Address is not None:
        try:
            parsed = Address(a)
            return parsed.to_string(is_user_friendly=True, is_bounceable=False, is_url_safe=True)
        except Exception:
            return a
    return a


def validate_ton_address(address: str) -> bool:
    a = (address or "").strip()
    if len(a) < 32 or any(ch in a for ch in (" ", "\n", "\t")):
        return False
    if Address is not None:
        try:
            Address(a)
            return True
        except Exception:
            return False
    return True


def get_ton_balance(address: str) -> int:
    if not validate_ton_address(address):
        raise ValueError("invalid_address")
    try:
        r = requests.get(f"{_base_url()}/getAddressBalance", params={"address": address, **_params()}, timeout=20)
        data = r.json() if r.ok else {}
    except Exception as e:
        raise RuntimeError("balance_unavailable") from e
    if not r.ok or not data.get("ok"):
        raise RuntimeError("balance_unavailable")
    return int(data.get("result") or 0)


def get_wallet_seqno(address: str) -> int:
    if not validate_ton_address(address):
        raise ValueError("invalid_address")
    try:
        r = requests.get(
            f"{_base_url()}/runGetMethod",
            params={"address": address, "method": "seqno", "stack": "[]", **_params()},
            timeout=20,
        )
        data = r.json() if r.ok else {}
    except Exception as e:
        raise RuntimeError("seqno_unavailable") from e
    if not r.ok or not data.get("ok"):
        return 0
    stack = (data.get("result") or {}).get("stack") or []
    if not stack:
        return 0
    try:
        item = stack[0]
        # usually ['num', '0x1']
        raw = item[1] if isinstance(item, list) and len(item) > 1 else item
        return int(str(raw), 0)
    except Exception:
        raise RuntimeError("seqno_unavailable")


def send_boc_return_hash(boc: str) -> dict:
    if not (boc or "").strip():
        return {"ok": False, "error": "send_failed"}
    try:
        r = requests.post(f"{_base_url()}/sendBoc", params=_params(), json={"boc": boc}, timeout=20)
        data = r.json() if r.ok else {}
    except Exception:
        return {"ok": False, "error": "send_failed"}
    if not r.ok or not data.get("ok"):
        return {"ok": False, "error": "send_failed"}
    result = data.get("result")
    if isinstance(result, str):
        return {"ok": True, "tx_hash": result}
    if isinstance(result, dict):
        return {"ok": True, "tx_hash": result.get("hash") or result.get("message_hash")}
    return {"ok": True, "tx_hash": None}


def nano_to_ton_display(amount_nano: int | str) -> str:
    return f"{(Decimal(int(amount_nano)) / Decimal(1_000_000_000)):.6f}".rstrip("0").rstrip(".")


def ton_to_nano(amount_ton: str | float | Decimal) -> int:
    try:
        val = Decimal(str(amount_ton))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid_amount")
    if val <= 0:
        raise ValueError("invalid_amount")
    return int((val * Decimal(1_000_000_000)).to_integral_value())
