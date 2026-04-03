import os
import requests
from typing import List, Dict, Any, Optional

TONCENTER_API = "https://toncenter.com/api/v2"
TONCENTER_KEY = os.getenv("TONCENTER_API_KEY", "")
OWNER_ADDRESS = "UQB7mMWEGE4reqMvHG5zPcHl9fQUy6L91UJhiXgyx772kuUv"


def get_transactions(limit: int = 20) -> List[Dict[str, Any]]:
    """Получает последние входящие транзакции на адрес владельца."""
    try:
        response = requests.get(
            f"{TONCENTER_API}/getTransactions",
            params={
                "address": OWNER_ADDRESS,
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
