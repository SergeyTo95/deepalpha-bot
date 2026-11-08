import uuid

from aiohttp import web
import pytest

from services import velia_chat_topic_routes as topics


@pytest.mark.asyncio
async def test_topic_routes_validate_references_and_use_authenticated_owner(monkeypatch):
    calls = []

    def save(user_id, data):
        calls.append((user_id, data))
        return {"entry_id": data["entry_id"]}

    def listing(user_id, kind, conversation_id, offset):
        calls.append((user_id, kind, conversation_id, offset))
        return {"entries": [], "next_offset": None}

    monkeypatch.setattr(topics, "_save", save)
    monkeypatch.setattr(topics, "_list", listing)
    app = web.Application()
    topics.setup_chat_topic_routes(
        app, lambda request: {"user_id": 42} if request.headers.get("Authorization") else None, lambda: True
    )
    from aiohttp.test_utils import TestClient, TestServer

    path = "/mobile-api/v1/chat-topics"
    headers = {"Authorization": "Bearer fixture"}
    entry, conversation, reference = (str(uuid.uuid4()) for _ in range(3))
    data = {"entry_id": entry, "conversation_id": conversation, "kind": "medical_document", "reference": reference}
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(path)).status == 401
        assert (await client.post(path, json=data)).status == 401
        assert (await client.post(path, json={**data, "user_id": 99}, headers=headers)).status == 400
        assert (await client.post(path, json={**data, "kind": ["research"]}, headers=headers)).status == 400
        assert (await client.get(path + "?conversation_id=bad", headers=headers)).status == 400
        response = await client.post(path, json=data, headers=headers)
        assert (await response.json())["entry"]["entry_id"] == entry
        assert (await client.get(path + "?kind=medical&offset=50", headers=headers)).status == 200
        assert calls == [(42, data), (42, "medical", None, 50)]


def test_owner_scoped_links_and_legacy_documents_in_postgresql(monkeypatch):
    import os

    from services import velia_chat_service as chat
    from services import velia_project_service as projects
    from services import velia_research_center_service as research

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration is required in CI")
    import psycopg2

    schema = "chat_topics_test_" + uuid.uuid4().hex
    with psycopg2.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        return psycopg2.connect(url, options=f"-c search_path={schema}")

    monkeypatch.setattr(chat, "get_connection", connect)
    monkeypatch.setattr(projects, "get_connection", connect)
    monkeypatch.setattr(projects, "_READY", False)
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    try:
        chat.ensure_velia_chat_tables()
        projects.ensure_tables()
        research.ensure_tables()
        owner, other = 7, 8
        conversation = chat.create_conversation(owner)["id"]
        message, request, entry = (str(uuid.uuid4()) for _ in range(3))
        with projects.transaction() as cur:
            cur.execute(
                """INSERT INTO velia_messages(message_id, conversation_id, user_id, role, content, idempotency_key)
                   VALUES (%s,%s,%s,'user',%s,%s)""",
                (message, conversation, owner, "Объясни содержание медицинского заключения простым языком.", request),
            )
        data = {"entry_id": entry, "conversation_id": conversation, "kind": "medical_document", "reference": request}
        assert topics._list(owner, "medical", None, 0)["entries"][0]["message_id"] == message
        assert topics._list(other, "medical", None, 0)["entries"] == []
        assert topics._save(other, data) is None
        record = topics._save(owner, data)
        assert topics._save(owner, data) == record
        assert topics._list(owner, "medical", conversation, 0)["entries"] == [record]

        mission = research.create_mission(other, {"goal": "Study an open dataset"}, "topic-other-001")
        research_entry = {"entry_id": str(uuid.uuid4()), "conversation_id": conversation,
                          "kind": "research", "reference": mission["id"]}
        assert topics._save(owner, research_entry) is None
        own_mission = research.create_mission(owner, {"goal": "Compare open datasets"}, "topic-owner-001")
        saved = topics._save(owner, {**research_entry, "reference": own_mission["id"]})
        assert saved["label"] == "Compare open datasets"
        assert topics._list(owner, "research", None, 0)["entries"] == [saved]
        assert chat.delete_conversation(owner, conversation)
        assert topics._list(owner, None, None, 0)["entries"] == []
    finally:
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
