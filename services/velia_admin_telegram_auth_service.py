import asyncio
import logging
import os
import re
import time
from typing import Any, Dict

from aiogram.dispatcher.handler import CancelHandler
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.types import Message

from services.velia_admin_security_service import create_admin_login_code, is_admin_user


logger = logging.getLogger(__name__)
VELIA_ADMIN_START_PAYLOAD = "velia_admin_login"
VELIA_ADMIN_LOGIN_COOLDOWN_SECONDS = 10


def extract_start_payload(text: str) -> str:
    parts = str(text or "").strip().split(maxsplit=1)
    if not parts:
        return ""
    command = parts[0].split("@", 1)[0].lower()
    if command != "/start":
        return ""
    return parts[1].strip() if len(parts) > 1 else ""


def is_velia_admin_login_start(text: str) -> bool:
    return extract_start_payload(text) == VELIA_ADMIN_START_PAYLOAD


def bot_username() -> str:
    candidate = str(os.getenv("BOT_USERNAME", "DeepAlphaAI_bot") or "DeepAlphaAI_bot").lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", candidate):
        return candidate
    return "DeepAlphaAI_bot"


def build_admin_login_url() -> str:
    return f"https://t.me/{bot_username()}?start={VELIA_ADMIN_START_PAYLOAD}"


def build_admin_login_message(code: str, expires_in: int) -> str:
    minutes = max(1, int(expires_in or 0) // 60)
    return (
        "🔐 <b>VELIA Control Center</b>\n\n"
        f"Код входа: <code>{code}</code>\n\n"
        f"⏳ Действует {minutes} минут и только один раз.\n"
        "Новый код автоматически отменяет предыдущий.\n\n"
        "Вернись на deepalpha-ai.com/admin и введи код.\n"
        "Никому не пересылай его."
    )


class VeliaAdminTelegramAuthMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        super().__init__()
        self._last_request_at: Dict[int, float] = {}

    async def on_pre_process_message(self, message: Message, data: Dict[str, Any]) -> None:
        if not is_velia_admin_login_start(message.text or ""):
            return

        user = message.from_user
        user_id = int(user.id) if user else 0
        if user_id <= 0:
            raise CancelHandler()

        if str(message.chat.type or "") != "private":
            await message.answer("Открой бота в личных сообщениях и повтори вход.")
            raise CancelHandler()

        if not is_admin_user(user_id):
            logger.warning("VELIA_ADMIN_LOGIN_DENIED telegram_user_id=%s", user_id)
            await message.answer("Доступ к VELIA Control Center запрещён.")
            raise CancelHandler()

        now = time.monotonic()
        previous = self._last_request_at.get(user_id, 0.0)
        if now - previous < VELIA_ADMIN_LOGIN_COOLDOWN_SECONDS:
            wait_seconds = max(
                1,
                int(VELIA_ADMIN_LOGIN_COOLDOWN_SECONDS - (now - previous)),
            )
            await message.answer(f"Подожди {wait_seconds} сек. перед созданием нового кода.")
            raise CancelHandler()
        self._last_request_at[user_id] = now

        try:
            result = await asyncio.to_thread(create_admin_login_code, user_id)
        except Exception:
            logger.exception("VELIA_ADMIN_LOGIN_CODE_FAILED admin_user_id=%s", user_id)
            await message.answer("Не удалось создать код входа. Повтори попытку через минуту.")
            raise CancelHandler()

        if not result.get("ok"):
            logger.warning(
                "VELIA_ADMIN_LOGIN_CODE_REJECTED admin_user_id=%s reason=%s",
                user_id,
                str(result.get("error") or "unknown")[:80],
            )
            await message.answer("Не удалось создать код входа.")
            raise CancelHandler()

        await message.answer(
            build_admin_login_message(
                str(result.get("login_code") or ""),
                int(result.get("expires_in") or 0),
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info(
            "VELIA_ADMIN_LOGIN_CODE_CREATED admin_user_id=%s ttl_seconds=%s",
            user_id,
            int(result.get("expires_in") or 0),
        )
        raise CancelHandler()


def install(telegram_bot_module: Any) -> None:
    if getattr(telegram_bot_module, "_velia_admin_telegram_auth_installed", False):
        return
    telegram_bot_module.dp.middleware.setup(VeliaAdminTelegramAuthMiddleware())
    telegram_bot_module._velia_admin_telegram_auth_installed = True
    logger.info("VELIA_ADMIN_TELEGRAM_AUTH_INSTALLED")
