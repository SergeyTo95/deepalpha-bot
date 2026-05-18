import os
from decimal import Decimal, InvalidOperation
from typing import Any, Dict

import requests


def _network() -> str:
    return (os.getenv("TON_NETWORK") or "testnet").strip().lower()


def _base_url() -> str:
    override = (os.getenv("TONCENTER_BASE_URL") or "").strip()
    if override:
        return override.rstrip("/")
    if _network() == "mainnet":
        return "https://toncenter.com/api/v2"
    return "https://testnet.toncenter.com/api/v2"


def _params() -> Dict[str, Any]:
    key = (os.getenv("TONCENTER_API_KEY") or "").strip()
    return {"api_key": key} if key else {}


def normalize_ton_address(address: str) -> str:
    return (address or "").strip()


def validate_ton_address(address: str) -> bool:
    a = normalize_ton_address(address)
    return len(a) >= 32 and all(ch not in a for ch in (" ", "\n", "\t"))


def get_ton_balance(address: str) -> int:
    if not validate_ton_address(address):
        raise ValueError("invalid_address")
    r = requests.get(f"{_base_url()}/getAddressBalance", params={"address": address, **_params()}, timeout=20)
    data = r.json() if r.ok else {}
    if not r.ok or not data.get("ok"):
        raise RuntimeError("toncenter_unavailable")
    return int(data.get("result") or 0)


def send_boc_return_hash(boc: str) -> dict:
    r = requests.post(f"{_base_url()}/sendBoc", json={"boc": boc, **_params()}, timeout=20)
    data = r.json() if r.ok else {}
    if not r.ok or not data.get("ok"):
        return {"ok": False, "error": "send_failed"}
    return {"ok": True, "tx_hash": data.get("result")}


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
