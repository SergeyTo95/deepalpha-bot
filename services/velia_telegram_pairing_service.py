import hashlib
import logging
import os
import secrets
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from aiogram.dispatcher.handler import CancelHandler
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.types import Message

from db.database import ensure_user, get_connection
from services.velia_chat_service import is_velia_chat_enabled_for_user
from services.velia_mobile_auth_service import format_pairing_code

logger = logging.getLogger(__name__)

VELIA_TELEGRAM_START_PAYLOAD = "velia_connect"
VELIA_TELEGRAM_PAIRING_TTL_SECONDS = 5 * 60
VELIA_TELEGRAM_PAIRING_COOLDOWN_SECONDS = 10
_PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def extract_start_payload(text: str) -> str:
    parts = str(text or "").strip().split(maxsplit=1)
    if not parts:
        return ""
    command = parts[0].split("@", 1)[0].lower()
    if command != "/start":
        return ""
    return parts[1].strip() if len(parts) > 1 else ""


def is_velia_connect_start(text: str) -> bool:
    return extract_start_payload(text) == VELIA_TELEGRAM_START_PAYLOAD


def _hash_code(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _new_raw_code() -> str:
    return "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(16))


def create_telegram_pairing_code(
    user_id: int,
    *,
    username: str = "",
    first_name: str = "",
) -> Dict[str, Any]:
    """Create a one-time mobile pairing code bound to a Telegram user.

    The code is compatible with the existing Android exchange endpoint, expires
    after five minutes, and invalidates any older unconsumed code for the user.
    """
    ensure_user(
        int(user_id),
        username=str(username or ""),
        first_name=str(first_name or ""),
        source="velia_telegram_pairing",
    )

    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=VELIA_TELEGRAM_PAIRING_TTL_SECONDS)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM velia_mobile_pairing_codes "
            "WHERE expires_at < %s OR (consumed_at IS NOT NULL AND consumed_at < %s)",
            (now, now - timedelta(days=1)),
        )
        cursor.execute(
            "UPDATE velia_mobile_pairing_codes SET consumed_at=%s "
            "WHERE user_id=%s AND consumed_at IS NULL",
            (now, int(user_id)),
        )

        for _ in range(8):
            raw_code = _new_raw_code()
            cursor.execute(
                """
                INSERT INTO velia_mobile_pairing_codes (
                    code_hash, user_id, created_at, expires_at,
                    created_user_agent, created_ip_hash
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (code_hash) DO NOTHING
                """,
                (
                    _hash_code(raw_code),
                    int(user_id),
                    now,
                    expires_at,
                    "telegram-deep-link",
                    "",
                ),
            )
            if cursor.rowcount == 1:
                conn.commit()
                return {
                    "ok": True,
                    "pairing_code": format_pairing_code(raw_code),
                    "expires_at": expires_at.isoformat() + "Z",
                    "expires_in": VELIA_TELEGRAM_PAIRING_TTL_SECONDS,
                }

        conn.rollback()
        return {"ok": False, "error": "pairing_code_generation_failed"}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def build_pairing_message(code: str, expires_in: int) -> str:
    minutes = max(1, int(expires_in or 0) // 60)
    return (
        "🔐 <b>Код подключения VELIA</b>\n\n"
        f"<code>{code}</code>\n\n"
        f"⏳ Код действует {minutes} минут и сработает только один раз.\n"
        "Новый код автоматически отменит предыдущий.\n\n"
        "Нажми на код, чтобы скопировать его.\n"
        "Затем вернись в VELIA системной кнопкой «Назад» или через список последних приложений.\n"
        "Приложение автоматически попробует подставить код из буфера.\n\n"
        "Никому не пересылай этот код."
    )


class VeliaTelegramPairingMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        super().__init__()
        self._last_request_at: Dict[int, float] = {}

    async def on_pre_process_message(self, message: Message, data: Dict[str, Any]) -> None:
        if not is_velia_connect_start(message.text or ""):
            return

        user = message.from_user
        user_id = int(user.id) if user else 0
        if user_id <= 0:
            raise CancelHandler()

        if str(message.chat.type or "") != "private":
            await message.answer("Открой @DeepAlphaAI_bot в личных сообщениях и повтори подключение.")
            raise CancelHandler()

        if not _env_bool("VELIA_MOBILE_API_ENABLED", False):
            await message.answer("VELIA Mobile API сейчас временно выключен.")
            raise CancelHandler()

        if not is_velia_chat_enabled_for_user(user_id):
            await message.answer("Твой аккаунт пока не добавлен в закрытую beta VELIA.")
            raise CancelHandler()

        now = time.monotonic()
        previous = self._last_request_at.get(user_id, 0.0)
        if now - previous < VELIA_TELEGRAM_PAIRING_COOLDOWN_SECONDS:
            wait_seconds = max(
                1,
                int(VELIA_TELEGRAM_PAIRING_COOLDOWN_SECONDS - (now - previous)),
            )
            await message.answer(f"Подожди {wait_seconds} сек. перед созданием нового кода.")
            raise CancelHandler()
        self._last_request_at[user_id] = now

        try:
            import asyncio

            result = await asyncio.to_thread(
                create_telegram_pairing_code,
                user_id,
                username=str(user.username or ""),
                first_name=str(user.first_name or ""),
            )
        except Exception:
            logger.exception("VELIA_TELEGRAM_PAIRING_FAILED user_id=%s", user_id)
            await message.answer("Не удалось создать код подключения. Повтори попытку через минуту.")
            raise CancelHandler()

        if not result.get("ok"):
            await message.answer("Не удалось создать код подключения. Повтори попытку через минуту.")
            raise CancelHandler()

        await message.answer(
            build_pairing_message(
                str(result.get("pairing_code") or ""),
                int(result.get("expires_in") or VELIA_TELEGRAM_PAIRING_TTL_SECONDS),
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info("VELIA_TELEGRAM_PAIRING_CREATED user_id=%s ttl_seconds=%s", user_id, result.get("expires_in"))
        raise CancelHandler()


def install(telegram_bot_module: Any) -> None:
    if getattr(telegram_bot_module, "_velia_telegram_pairing_installed", False):
        return
    telegram_bot_module.dp.middleware.setup(VeliaTelegramPairingMiddleware())
    telegram_bot_module._velia_telegram_pairing_installed = True
    logger.info("VELIA_TELEGRAM_PAIRING_INSTALLED")
