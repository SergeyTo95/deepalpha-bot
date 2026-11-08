"""Owner-scoped references from thematic history to messages and specialist jobs."""
import asyncio
import re

from aiohttp import web

from services.velia_project_service import transaction

_ID = re.compile(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}\Z")
_RESOURCES = {
    "research": ("velia_research_missions", "mission_id"),
    "medical_scan": ("velia_medical_cases", "case_id"),
}
_DOCUMENTS = {"medical_document", "research_document"}
_KINDS = {*_RESOURCES, *_DOCUMENTS}


def _valid_id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _entry(row):
    return {
        **{key: row[key] for key in ("entry_id", "conversation_id", "kind", "resource_id", "message_id", "label")},
        "created_at": row["created_at"].isoformat() + "Z",
    }


def _list(user_id, kind, conversation_id, offset):
    with transaction() as cur:
        cur.execute(
            """SELECT * FROM (
                   SELECT t.* FROM velia_chat_topics t
                   JOIN velia_conversations c ON c.conversation_id=t.conversation_id
                   WHERE c.user_id=%s AND c.deleted_at IS NULL
                     AND (%s::text IS NULL OR t.conversation_id=%s)
                     AND (%s::text IS NULL OR (%s='research')=(t.kind IN ('research','research_document')))
                   UNION ALL
                   SELECT m.message_id, m.conversation_id, 'medical_document', NULL, NULL,
                          m.message_id, m.created_at
                   FROM velia_messages m
                   JOIN velia_conversations c ON c.conversation_id=m.conversation_id
                   WHERE c.user_id=%s AND m.user_id=c.user_id AND c.deleted_at IS NULL
                     AND m.deleted_at IS NULL AND m.role='user'
                     AND (%s::text IS NULL OR m.conversation_id=%s)
                     AND %s::text IS DISTINCT FROM 'research'
                     AND left(m.content, 160) LIKE ANY(%s)
                     AND NOT EXISTS (SELECT 1 FROM velia_chat_topics t WHERE t.message_id=m.message_id)
               ) entries ORDER BY created_at DESC, entry_id DESC LIMIT 51 OFFSET %s""",
            (user_id, conversation_id, conversation_id, kind, kind,
             user_id, conversation_id, conversation_id, kind, [
                "Объясни содержание медицинского заключения простым языком.%",
                "Сравни прикреплённые медицинские заключения по датам.%",
                "Подготовь краткое резюме прикреплённых заключений и вопросы для обсуждения с врачом.%",
             ], offset),
        )
        rows = cur.fetchall()
    return {"entries": [_entry(row) for row in rows[:50]], "next_offset": offset + 50 if len(rows) > 50 else None}


def _save(user_id, data):
    conversation_id = data["conversation_id"]
    kind = data["kind"]
    reference = data["reference"]
    with transaction(user_id) as cur:
        cur.execute(
            """SELECT 1 FROM velia_conversations WHERE conversation_id=%s AND user_id=%s
               AND deleted_at IS NULL FOR UPDATE""",
            (conversation_id, user_id),
        )
        if not cur.fetchone():
            return None
        message_id = resource_id = label = None
        if kind in _DOCUMENTS:
            cur.execute(
                """SELECT message_id FROM velia_messages
                   WHERE conversation_id=%s AND user_id=%s AND role='user'
                     AND idempotency_key=%s AND deleted_at IS NULL""",
                (conversation_id, user_id, reference),
            )
            message = cur.fetchone()
            if not message:
                return None
            message_id = message["message_id"]
        else:
            table, id_column = _RESOURCES[kind]
            select = "goal" if kind == "research" else id_column
            cur.execute(f"SELECT {select} FROM {table} WHERE {id_column}=%s AND user_id=%s", (reference, user_id))
            resource = cur.fetchone()
            if not resource:
                return None
            if kind == "research":
                label = resource["goal"][:120]
            resource_id = reference
        cur.execute(
            """INSERT INTO velia_chat_topics AS existing
                   (entry_id, conversation_id, kind, resource_id, message_id, label)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (entry_id) DO UPDATE SET entry_id=EXCLUDED.entry_id
               WHERE existing.conversation_id=EXCLUDED.conversation_id AND existing.kind=EXCLUDED.kind
                 AND existing.resource_id IS NOT DISTINCT FROM EXCLUDED.resource_id
                 AND existing.message_id IS NOT DISTINCT FROM EXCLUDED.message_id
               RETURNING *""",
            (data["entry_id"], conversation_id, kind, resource_id, message_id, label),
        )
        row = cur.fetchone()
        return _entry(row) if row else None


def setup_chat_topic_routes(app, authenticate, enabled):
    async def list_entries(request):
        if not enabled():
            return web.json_response({"ok": False, "error": "velia_mobile_api_disabled"}, status=503)
        auth = authenticate(request)
        if not auth:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        kind = request.query.get("kind")
        conversation_id = request.query.get("conversation_id")
        try:
            offset = int(request.query.get("offset", "0"))
        except ValueError:
            offset = -1
        if (kind not in (None, "research", "medical") or not 0 <= offset <= 10000
                or (conversation_id is not None and not _valid_id(conversation_id))):
            return web.json_response({"ok": False, "error": "invalid_request"}, status=400)
        result = await asyncio.to_thread(_list, int(auth["user_id"]), kind, conversation_id, offset)
        return web.json_response({"ok": True, **result})

    async def save_entry(request):
        if not enabled():
            return web.json_response({"ok": False, "error": "velia_mobile_api_disabled"}, status=503)
        auth = authenticate(request)
        if not auth:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            data = None
        if (not isinstance(data, dict) or set(data) != {"entry_id", "conversation_id", "kind", "reference"}
                or not isinstance(data["kind"], str) or data["kind"] not in _KINDS
                or not all(_valid_id(data[key]) for key in ("entry_id", "conversation_id", "reference"))):
            return web.json_response({"ok": False, "error": "invalid_request"}, status=400)
        result = await asyncio.to_thread(_save, int(auth["user_id"]), data)
        if not result:
            return web.json_response({"ok": False, "error": "topic_reference_not_found"}, status=404)
        return web.json_response({"ok": True, "entry": result})

    app.router.add_get("/mobile-api/v1/chat-topics", list_entries)
    app.router.add_post("/mobile-api/v1/chat-topics", save_entry)
