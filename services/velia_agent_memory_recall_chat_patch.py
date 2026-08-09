from __future__ import annotations

import logging
from typing import Any

from services.velia_agent_memory_recall_runtime_service import recall_context_for_conversation

logger = logging.getLogger(__name__)


def install(velia_chat_service_module: Any) -> None:
    if getattr(velia_chat_service_module, "_velia_agent_memory_recall_patch_installed", False):
        return

    original_build_prompt = velia_chat_service_module._build_prompt

    def build_prompt_with_agent_memory(user_id: int, conversation_id: str) -> str:
        prompt = original_build_prompt(user_id, conversation_id)
        context = recall_context_for_conversation(int(user_id), str(conversation_id))
        if not context:
            return prompt
        marker = "\n\nConversation:\n"
        if marker not in prompt:
            logger.warning(
                "VELIA_AGENT_MEMORY_RECALL_CONTEXT_SKIPPED user_id=%s conversation_id=%s error=conversation_marker_missing",
                int(user_id),
                str(conversation_id)[:120],
            )
            return prompt
        return prompt.replace(marker, f"\n\n{context}{marker}", 1)

    velia_chat_service_module._build_prompt = build_prompt_with_agent_memory
    velia_chat_service_module._velia_agent_memory_recall_patch_installed = True
    logger.info("VELIA_AGENT_MEMORY_RECALL_CHAT_PATCH_INSTALLED")
