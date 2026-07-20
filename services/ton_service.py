import os
import requests
from typing import List, Dict, Any, Optional
from services.treasury_service import get_public_treasury_address

TONCENTER_API = "https://toncenter.com/api/v2"
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


def get_transactions_since_treasury_cursor(page_limit: int = 100, max_pages: int = 20) -> List[Dict[str, Any]]:
    """Read treasury transactions page-by-page until the persisted cursor, returning old->new safely."""
    from db.database import get_setting, set_setting
    last_lt = str(get_setting("treasury_last_processed_lt", "") or "")
    last_hash = str(get_setting("treasury_last_processed_hash", "") or "")
    pages: List[List[Dict[str, Any]]] = []
    cursor_lt = ""; cursor_hash = ""; found_cursor = False
    for _ in range(int(max_pages)):
        page = _get_transactions_page(int(page_limit), cursor_lt, cursor_hash)
        if not page:
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
            break
        cursor_lt, cursor_hash = _tx_lt_hash(page[-1])
        if not cursor_lt or not cursor_hash:
            break
    txs: List[Dict[str, Any]] = []
    for page in reversed(pages):
        txs.extend(reversed(page))
    return txs


def mark_treasury_transactions_cursor(transactions: List[Dict[str, Any]]) -> None:
    """Persist cursor only after caller safely processes the returned old->new batch."""
    if not transactions:
        return
    from db.database import set_setting
    newest_lt, newest_hash = _tx_lt_hash(transactions[-1])
    if newest_lt and newest_hash:
        set_setting("treasury_last_processed_lt", newest_lt)
        set_setting("treasury_last_processed_hash", newest_hash)


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
