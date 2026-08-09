from __future__ import annotations

import logging

from services import velia_agent_builder_service as builder
from services.velia_agent_memory_recall_service import recall_context_for_conversation as _recall_context

logger = logging.getLogger(__name__)


def recall_context_for_conversation(user_id: int, conversation_id: str) -> str:
    """Allow recall only for a live custom-Agent session.

    Durable memory capture intentionally survives Agent archival and temporary
    feature disablement. Prompt recall is stricter: both the Agent Builder and
    recall flags must be enabled, and the conversation must still be an active
    Agent session. Ordinary/archived conversations remain on the normal VELIA
    chat path.
    """

    if not builder.builder_enabled():
        return ""
    try:
        session = builder.session_for_conversation(int(user_id), str(conversation_id))
    except Exception as exc:
        logger.warning(
            "VELIA_AGENT_MEMORY_RECALL_SESSION_SKIPPED user_id=%s conversation_id=%s error=%s",
            int(user_id),
            str(conversation_id)[:120],
            exc.__class__.__name__,
        )
        return ""
    if not session or str(session.get("status") or "") != "active":
        return ""
    return _recall_context(int(user_id), str(conversation_id))
