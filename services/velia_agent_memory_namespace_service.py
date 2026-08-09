from __future__ import annotations

import re
from typing import Any, Dict, Optional

from services import velia_agent_builder_service as builder

_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")


class AgentMemoryNamespaceError(RuntimeError):
    pass


def _namespace_for_agent(agent_id: str) -> str:
    clean = str(agent_id or "").strip()
    if not clean or len(clean) > 96 or not _NAMESPACE_RE.match(clean):
        raise AgentMemoryNamespaceError("velia_agent_memory_agent_id_invalid")
    namespace = f"velia-agent:{clean}"
    if len(namespace) > 120 or not _NAMESPACE_RE.match(namespace):
        raise AgentMemoryNamespaceError("velia_agent_memory_namespace_invalid")
    return namespace


def resolve_memory_namespace(user_id: int, conversation_id: str) -> Dict[str, Optional[str]]:
    """Resolve an internal Velyon Memory namespace from server-owned Agent state.

    Ordinary VELIA conversations intentionally return no override and therefore
    keep the existing configured main-memory agent id. A conversation linked to
    a custom VELIA Agent receives one stable namespace shared by all of that
    Agent's root/child conversations while the memory service's session_id
    remains the concrete conversation id.

    The caller must treat resolver failures as a memory-capture failure, not as
    permission to fall back an Agent conversation into the ordinary namespace.
    """

    clean_conversation = str(conversation_id or "").strip()
    if int(user_id) <= 0 or not clean_conversation:
        raise AgentMemoryNamespaceError("velia_agent_memory_identity_invalid")

    if not builder.builder_enabled():
        return {
            "scope": "velia",
            "agent_id": None,
            "session_id": clean_conversation,
        }

    session = builder.session_for_conversation(int(user_id), clean_conversation)
    if not session:
        return {
            "scope": "velia",
            "agent_id": None,
            "session_id": clean_conversation,
        }

    agent_id = str(session.get("agent_id") or "").strip()
    return {
        "scope": "agent",
        "agent_id": _namespace_for_agent(agent_id),
        "session_id": clean_conversation,
    }
