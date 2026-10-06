"""Model-mode router for VELIA chat.

Keeps Pro, Flash and Quantum isolated. Quantum is fail-closed until its own
feature flag, private worker URL and API key are configured.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

from services import velia_flash_service as flash
from services import velia_quantum_service as quantum


VALID_CHAT_MODES = {"pro", "flash", "quantum"}


def public_capability() -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    result.update(flash.public_capability())
    result.update(quantum.public_capability())
    return result


def dispatch_send(
    sender: Callable[..., Dict[str, Any]],
    user_id: int,
    conversation_id: str,
    content: str,
    *,
    chat_mode: str = "pro",
    **kwargs: Any,
) -> Dict[str, Any]:
    mode = str(chat_mode or "pro").strip().lower()
    if mode == "pro":
        return sender(
            int(user_id),
            str(conversation_id),
            str(content),
            **kwargs,
        )
    if mode == "flash":
        return flash.dispatch_send(
            sender,
            int(user_id),
            str(conversation_id),
            str(content),
            chat_mode="flash",
            **kwargs,
        )
    if mode == "quantum":
        return quantum.dispatch_send(
            sender,
            int(user_id),
            str(conversation_id),
            str(content),
            chat_mode="quantum",
            **kwargs,
        )
    return {"ok": False, "error": "invalid_chat_mode"}
