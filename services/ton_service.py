import os
import requests
from typing import List, Dict, Any, Optional
from services.treasury_service import get_public_treasury_address

TONCENTER_API = "https://testnet.toncenter.com/api/v2" if "test" in os.getenv("TON_NETWORK", "mainnet").lower() else "https://toncenter.com/api/v2"
TONCENTER_KEY = os.getenv("TONCENTER_API_KEY", "")


def get_transactions(limit: int = 20) -> List[Dict[str, Any]]:
    """Получает последние входящие транзакции на адрес владельца."""
    try:
        response = requests.get(
            f"{TONCENTER_API}/getTransactions",
            params={
                "address": (get_public_treasury_address().get("address") or ""),
                "limit": limit,
                "api_key": TONCENTER_KEY,
            },
            timeout=15,
        )
        if response.status_code != 200:
            return []
        data = response.json()
        return data.get("result", [])
    except Exception as e:
        print(f"TON API ERROR: {e}")
        return []


def _tx_lt_hash(tx: Dict[str, Any]) -> tuple[str, str]:
    tid = tx.get("transaction_id") or {}
    return str(tid.get("lt") or tx.get("lt") or ""), str(tid.get("hash") or tx.get("hash") or "")


def _get_transactions_page(limit: int = 100, lt: str = "", tx_hash: str = "") -> List[Dict[str, Any]]:
    try:
        params = {
            "address": (get_public_treasury_address().get("address") or ""),
            "limit": int(limit),
            "api_key": TONCENTER_KEY,
            "archival": "true",
        }
        if lt and tx_hash:
            params["lt"] = lt
            params["hash"] = tx_hash
        response = requests.get(f"{TONCENTER_API}/getTransactions", params=params, timeout=20)
        if response.status_code != 200:
            return []
        data = response.json()
        return data.get("result", []) or []
    except Exception as e:
        print(f"TON API PAGE ERROR: {e}")
        return []


def get_transactions_since_treasury_cursor(page_limit: int = 100, max_pages: int = 20) -> Dict[str, Any]:
    """Read treasury transactions page-by-page without advancing persisted cursor."""
    from db.database import get_setting
    last_lt = str(get_setting("treasury_last_processed_lt", "") or "")
    last_hash = str(get_setting("treasury_last_processed_hash", "") or "")
    start_lt = str(get_setting("treasury_scan_page_lt", "") or "")
    start_hash = str(get_setting("treasury_scan_page_hash", "") or "")
    pending_newest_lt = str(get_setting("treasury_scan_newest_lt", "") or "")
    pending_newest_hash = str(get_setting("treasury_scan_newest_hash", "") or "")
    pages: List[List[Dict[str, Any]]] = []
    cursor_lt = start_lt; cursor_hash = start_hash; found_cursor = False; next_page_cursor = None
    for _ in range(int(max_pages)):
        page = _get_transactions_page(int(page_limit), cursor_lt, cursor_hash)
        if not page:
            found_cursor = True if not last_lt and not last_hash else found_cursor
            break
        kept = []
        for tx in page:
            lt, h = _tx_lt_hash(tx)
            if last_lt and last_hash and lt == last_lt and h == last_hash:
                found_cursor = True
                break
            kept.append(tx)
        if kept:
            pages.append(kept)
        if found_cursor or len(page) < int(page_limit):
            if len(page) < int(page_limit):
                found_cursor = True if not last_lt and not last_hash else found_cursor
            break
        cursor_lt, cursor_hash = _tx_lt_hash(page[-1])
        if not cursor_lt or not cursor_hash:
            break
        next_page_cursor = {"lt": cursor_lt, "hash": cursor_hash}
    txs: List[Dict[str, Any]] = []
    for page in reversed(pages):
        txs.extend(reversed(page))
    history_complete = bool(found_cursor or (not last_lt and not last_hash and next_page_cursor is None))
    newest_seen = _tx_lt_hash(pages[0][0]) if pages and pages[0] else (pending_newest_lt, pending_newest_hash)
    return {
        "transactions": txs,
        "cursor_reached": bool(found_cursor),
        "history_complete": history_complete,
        "next_page_cursor": (None if history_complete else next_page_cursor),
        "saved_cursor": {"lt": last_lt, "hash": last_hash},
        "pending_newest": {"lt": pending_newest_lt, "hash": pending_newest_hash},
        "newest_seen": {"lt": newest_seen[0], "hash": newest_seen[1]},
    }


def mark_treasury_transactions_cursor(scan_result: Dict[str, Any], processed_count: int) -> None:
    """Persist only safely processed cursor state without moving canonical cursor past retryable gaps."""
    transactions = list((scan_result or {}).get("transactions") or [])
    safe_count = max(0, min(int(processed_count or 0), len(transactions)))
    history_complete = bool((scan_result or {}).get("history_complete"))
    saved = (scan_result or {}).get("saved_cursor") or {}
    from db.database import set_treasury_transaction_cursor

    # Retryable gap inside an incomplete scan: reset scan state and keep canonical cursor unchanged.
    # This also applies to safe_count == 0 so a stale page backlog cannot skip the failed tx on restart.
    if (not history_complete) and safe_count < len(transactions):
        set_treasury_transaction_cursor(str(saved.get("lt") or ""), str(saved.get("hash") or ""), "", "", "", "")
        return

    if safe_count <= 0:
        return

    if safe_count < len(transactions):
        newest_lt, newest_hash = _tx_lt_hash(transactions[safe_count - 1])
        if newest_lt and newest_hash:
            set_treasury_transaction_cursor(newest_lt, newest_hash, "", "", "", "")
        return

    backlog = (scan_result or {}).get("next_page_cursor") or {}
    pending = (scan_result or {}).get("pending_newest") or {}
    newest_seen = (scan_result or {}).get("newest_seen") or {}
    if history_complete:
        final_lt = str(pending.get("lt") or newest_seen.get("lt") or "")
        final_hash = str(pending.get("hash") or newest_seen.get("hash") or "")
        if final_lt and final_hash:
            set_treasury_transaction_cursor(final_lt, final_hash, "", "", "", "")
        return

    # Full safe batch from incomplete scan: keep canonical cursor unchanged and continue old-history pagination later.
    pending_lt = str(pending.get("lt") or newest_seen.get("lt") or "")
    pending_hash = str(pending.get("hash") or newest_seen.get("hash") or "")
    set_treasury_transaction_cursor(str(saved.get("lt") or ""), str(saved.get("hash") or ""), str(backlog.get("lt") or ""), str(backlog.get("hash") or ""), pending_lt, pending_hash)


def process_treasury_payment_scan_once(scan_result: Dict[str, Any], process_transaction) -> Dict[str, Any]:
    """Testable one-batch processor: stops on retryable failure and marks only safe prefix."""
    transactions = list((scan_result or {}).get("transactions") or [])
    safe_count = 0
    for tx in transactions:
        result = process_transaction(tx)
        ok = bool(result is True or (isinstance(result, dict) and result.get("ok")))
        if not ok:
            break
        safe_count += 1
    mark_treasury_transactions_cursor(scan_result, safe_count)
    return {"ok": safe_count == len(transactions), "safe_cursor_count": safe_count}


def parse_payment(tx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Парсит транзакцию и извлекает:
    - user_id из комментария
    - сумму в TON
    - hash транзакции
    """
    try:
        in_msg = tx.get("in_msg", {})
        if not in_msg:
            return None

        # Сумма в нанотонах -> TON
        value = int(in_msg.get("value", 0))
        if value <= 0:
            return None
        ton_amount = value / 1_000_000_000

        # Комментарий — должен быть Telegram ID
        comment = ""
        msg_data = in_msg.get("msg_data", {})
        if isinstance(msg_data, dict):
            text = msg_data.get("text", "")
            if text:
                import base64
                try:
                    comment = base64.b64decode(text).decode("utf-8").strip()
                except Exception:
                    comment = text.strip()

        if not comment:
            return None

        # Проверяем что комментарий — числовой ID
        try:
            user_id = int(comment)
        except ValueError:
            return None

        tx_hash = tx.get("transaction_id", {}).get("hash", "")

        return {
            "user_id": user_id,
            "ton_amount": ton_amount,
            "tx_hash": tx_hash,
        }
    except Exception as e:
        print(f"TON PARSE ERROR: {e}")
        return None


def calculate_tokens(ton_amount: float) -> int:
    """Считает сколько токенов дать за TON."""
    from db.database import get_setting
    try:
        token_price = float(get_setting("token_price_ton", "0.1"))
        if token_price <= 0:
            return 0
        return int(ton_amount / token_price)
    except Exception:
        return 0
