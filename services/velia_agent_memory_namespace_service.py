from __future__ import annotations

import re
from typing import Dict, Optional

from db.database import get_connection

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


def _row_value(row, key: str, index: int = 0):
    if isinstance(row, dict):
        return row.get(key)
    if row is None:
        return None
    try:
        return row[index]
    except (IndexError, TypeError):
        return None


def resolve_memory_namespace(user_id: int, conversation_id: str) -> Dict[str, Optional[str]]:
    """Resolve an internal Velyon Memory namespace from persistent Agent state.

    Resolution intentionally does not depend on the Agent Builder feature flag or
    active session status. A queued memory event can be delivered after an Agent
    is archived or the product flag is temporarily disabled without being mixed
    into the ordinary VELIA namespace.

    Ordinary VELIA conversations return no agent override and keep the existing
    configured main-memory agent id. Root and child conversations belonging to
    one custom VELIA Agent share one memory agent namespace while retaining their
    concrete conversation id as the Velyon Memory session id.
    """

    clean_conversation = str(conversation_id or "").strip()
    if int(user_id) <= 0 or not clean_conversation:
        raise AgentMemoryNamespaceError("velia_agent_memory_identity_invalid")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT to_regclass('public.velia_agent_sessions')")
        table_row = cursor.fetchone()
        if not _row_value(table_row, "to_regclass", 0):
            return {
                "scope": "velia",
                "agent_id": None,
                "session_id": clean_conversation,
            }

        cursor.execute(
            """
            SELECT agent_id
            FROM velia_agent_sessions
            WHERE user_id=%s AND conversation_id=%s
            LIMIT 1
            """,
            (int(user_id), clean_conversation),
        )
        row = cursor.fetchone()
        if not row:
            return {
                "scope": "velia",
                "agent_id": None,
                "session_id": clean_conversation,
            }

        agent_id = str(_row_value(row, "agent_id", 0) or "").strip()
        return {
            "scope": "agent",
            "agent_id": _namespace_for_agent(agent_id),
            "session_id": clean_conversation,
        }
    except AgentMemoryNamespaceError:
        raise
    except Exception as exc:
        raise AgentMemoryNamespaceError(
            f"velia_agent_memory_lookup_{exc.__class__.__name__}"
        ) from exc
    finally:
        cursor.close()
        conn.close()
