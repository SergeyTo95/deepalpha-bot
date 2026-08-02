import logging
from typing import Any

from services.velia_user_profile_service import get_user_profile_context


logger = logging.getLogger(__name__)


def install(velia_chat_service_module: Any) -> None:
    if getattr(velia_chat_service_module, "_velia_user_profile_patch_installed", False):
        return

    original_build_prompt = velia_chat_service_module._build_prompt

    def build_prompt_with_user_profile(user_id: int, conversation_id: str) -> str:
        prompt = original_build_prompt(user_id, conversation_id)
        try:
            profile_context = get_user_profile_context(int(user_id))
        except Exception as exc:
            logger.warning(
                "VELIA_USER_PROFILE_CONTEXT_SKIPPED user_id=%s error=%s",
                int(user_id),
                exc.__class__.__name__,
            )
            return prompt
        if not profile_context:
            return prompt

        marker = "\n\nConversation:\n"
        if marker not in prompt:
            logger.warning(
                "VELIA_USER_PROFILE_CONTEXT_SKIPPED user_id=%s error=conversation_marker_missing",
                int(user_id),
            )
            return prompt
        return prompt.replace(
            marker,
            f"\n\n{profile_context}{marker}",
            1,
        )

    velia_chat_service_module._build_prompt = build_prompt_with_user_profile
    velia_chat_service_module._velia_user_profile_patch_installed = True
    logger.info("VELIA_USER_PROFILE_RUNTIME_PATCH_INSTALLED")
