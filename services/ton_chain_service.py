import os
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Union

import requests

try:
    from tonsdk.utils import Address
except Exception:
    Address = None

logger = logging.getLogger(__name__)


def _network() -> str:
    return (os.getenv("TON_NETWORK") or "testnet").strip().lower()


def _extract_ton_tx_hash(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        h = payload.strip()
        return h if h and h != "-" else ""
    if isinstance(payload, dict):
        keys = ("tx_hash", "hash", "transaction_hash", "boc_hash", "message_hash")
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip() and value.strip() != "-":
                return value.strip()
        result = payload.get("result")
        if isinstance(result, dict):
            nested = _extract_ton_tx_hash(result)
            if nested:
                return nested
        if isinstance(result, str) and result.strip() and result.strip() != "-":
            return result.strip()
    return ""


def _base_url() -> str:
    override = (os.getenv("TONCENTER_BASE_URL") or "").strip()
    if override:
        return override.rstrip("/")
    return "https://toncenter.com/api/v2" if _network() == "mainnet" else "https://testnet.toncenter.com/api/v2"


def _params() -> Dict[str, Any]:
    network = _network()
    if network == "mainnet":
        key = (os.getenv("TONCENTER_MAINNET_API_KEY") or os.getenv("TONCENTER_API_KEY") or "").strip()
    else:
        key = (os.getenv("TONCENTER_TESTNET_API_KEY") or os.getenv("TONCENTER_API_KEY") or "").strip()
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


def _parse_seqno_value(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    s = str(raw).strip()
    if not s:
        return None
    if re.fullmatch(r"0x[0-9a-fA-F]+", s):
        return int(s, 16)
    if s.isdigit():
        return int(s)
    return None


def _extract_seqno_from_run_get_method(data: Dict[str, Any]) -> Optional[int]:
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    roots = [data.get("result"), data.get("result", {}).get("result") if isinstance(data.get("result"), dict) else None]
    for root in roots:
        if not isinstance(root, dict):
            continue
        stack = root.get("stack")
        if not isinstance(stack, list) or not stack:
            continue
        first = stack[0]
        if isinstance(first, list) and len(first) > 1:
            parsed = _parse_seqno_value(first[1])
            if parsed is not None:
                return parsed
        parsed = _parse_seqno_value(first)
        if parsed is not None:
            return parsed
    return None


def _extract_seqno_from_wallet_info(data: Dict[str, Any]) -> Optional[int]:
    if not isinstance(data, dict):
        return None
    candidates = [
        data.get("seqno"),
        data.get("wallet_seqno"),
        (data.get("wallet") or {}).get("seqno") if isinstance(data.get("wallet"), dict) else None,
        (data.get("result") or {}).get("seqno") if isinstance(data.get("result"), dict) else None,
        (data.get("result") or {}).get("wallet_seqno") if isinstance(data.get("result"), dict) else None,
    ]
    for candidate in candidates:
        parsed = _parse_seqno_value(candidate)
        if parsed is not None:
            return parsed
    return None


def get_wallet_seqno(address: str) -> Optional[int]:
    if not validate_ton_address(address):
        raise ValueError("invalid_address")
    try:
        r = requests.post(
            f"{_base_url()}/runGetMethod",
            params=_params(),
            json={"address": address, "method": "seqno", "stack": []},
            timeout=20,
        )
        data = r.json() if r.ok else {}
    except Exception as e:
        raise RuntimeError("seqno_unavailable") from e
    seqno = _extract_seqno_from_run_get_method(data) if (r.ok and data.get("ok")) else None
    if seqno is not None:
        return seqno

    try:
        wr = requests.get(
            f"{_base_url()}/getWalletInformation",
            params={"address": address, **_params()},
            timeout=20,
        )
        wdata = wr.json() if wr.ok else {}
    except Exception:
        return None
    return _extract_seqno_from_wallet_info(wdata)


def send_boc_return_hash(boc: str) -> dict:
    if not (boc or "").strip():
        return {"ok": False, "error": "send_failed", "error_detail": "unknown"}
    try:
        r = requests.post(f"{_base_url()}/sendBoc", params=_params(), json={"boc": boc}, timeout=20)
        try:
            data = r.json()
        except Exception:
            data = {}
        response_preview = str(data or r.text or "")[:400]
    except Exception as e:
        logger.warning("TON sendBoc request failed: %s", e.__class__.__name__)
        return {"ok": False, "error": "send_failed", "error_detail": "toncenter_unavailable"}
    if not r.ok or not data.get("ok"):
        response_lower = response_preview.lower()
        detail = "unknown"
        if any(x in response_lower for x in ("insufficient", "not enough", "low balance", "no funds")):
            detail = "insufficient_network_fee"
        elif any(x in response_lower for x in ("seqno", "account is not active", "cannot apply external message")):
            detail = "seqno_or_account_state"
        elif any(x in response_lower for x in ("liteserver", "not accepted", "rejected", "external message was not accepted")):
            detail = "toncenter_rejected"
        logger.warning("TON sendBoc rejected status=%s ok=%s detail=%s response=%s", r.status_code, data.get("ok"), detail, response_preview)
        return {
            "ok": False,
            "error": "send_failed",
            "error_detail": detail,
            "toncenter_status": r.status_code,
            "error_message": response_preview,
        }
    tx_hash = _extract_ton_tx_hash(data)
    return {"ok": True, "tx_hash": tx_hash or None}


def nano_to_ton_display(amount_nano: Union[int, str]) -> str:
    return f"{(Decimal(int(amount_nano)) / Decimal(1_000_000_000)):.6f}".rstrip("0").rstrip(".")


def ton_to_nano(amount_ton: Union[str, float, Decimal]) -> int:
    try:
        val = Decimal(str(amount_ton))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid_amount")
    if val <= 0:
        raise ValueError("invalid_amount")
    return int((val * Decimal(1_000_000_000)).to_integral_value())
