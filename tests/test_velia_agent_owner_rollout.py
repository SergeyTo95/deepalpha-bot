from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aiohttp import web

from services import velia_agent_builder_service as builder
from services import velia_agent_memory_recall_chat_patch as recall_chat_patch
from services import velia_agent_memory_recall_runtime_service as recall_runtime
from services import velia_agent_memory_recall_service as recall_service
from services import velia_agent_owner_rollout_service as rollout


def _restore(module, name, value) -> None:
    setattr(module, name, value)


def test_owner_ids_use_admin_identity_and_optional_allowlist(monkeypatch):
    monkeypatch.setenv("VELIA_AGENT_OWNER_ROLLOUT_ENABLED", "true")
    monkeypatch.setenv("ADMIN_ID", "42")
    monkeypatch.setenv("VELIA_AGENT_OWNER_USER_IDS", "77, 88;bad -1 0")

    assert rollout.owner_user_ids() == {42, 77, 88}
    assert rollout.owner_rollout_enabled() is True
    assert rollout.owner_access_enabled(42) is True
    assert rollout.owner_access_enabled(88) is True
    assert rollout.owner_access_enabled(99) is False


def test_owner_rollout_fails_closed_without_valid_identity(monkeypatch):
    monkeypatch.setenv("VELIA_AGENT_OWNER_ROLLOUT_ENABLED", "true")
    monkeypatch.setenv("ADMIN_ID", "not-an-id")
    monkeypatch.setenv("VELIA_AGENT_OWNER_USER_IDS", "")

    assert rollout.owner_user_ids() == set()
    assert rollout.owner_rollout_enabled() is False
    assert rollout.owner_access_enabled(1) is False


def test_install_allows_only_owner_when_global_flags_are_off(monkeypatch):
    monkeypatch.setenv("VELIA_AGENT_OWNER_ROLLOUT_ENABLED", "true")
    monkeypatch.setenv("ADMIN_ID", "42")
    monkeypatch.delenv("VELIA_AGENT_OWNER_USER_IDS", raising=False)

    original_builder_enabled = builder.builder_enabled
    original_prompt_context = builder.prompt_context_for_conversation
    original_recall_enabled_service = recall_service.recall_enabled
    original_recall_enabled_runtime = recall_runtime.recall_enabled
    original_recall_context_runtime = recall_runtime.recall_context_for_conversation
    original_recall_context_chat = recall_chat_patch.recall_context_for_conversation

    builder.builder_enabled = lambda: False
    builder.prompt_context_for_conversation = lambda user_id, conversation_id: f"CTX:{user_id}:{conversation_id}"
    recall_service.recall_enabled = lambda: False
    recall_runtime.recall_enabled = lambda: False
    recall_runtime.recall_context_for_conversation = lambda user_id, conversation_id: f"MEM:{user_id}:{conversation_id}"
    recall_chat_patch.recall_context_for_conversation = recall_runtime.recall_context_for_conversation

    class Routes:
        @staticmethod
        def _require_mobile_auth(request):
            return {"user_id": request.user_id} if request.user_id else None

        @staticmethod
        def _json_response(payload, status=200):
            return {"status": status, "payload": payload}

    app = web.Application()
    try:
        rollout.install(app, Routes)

        assert builder.builder_enabled() is True
        assert recall_service.recall_enabled() is True
        assert recall_runtime.recall_enabled() is True
        assert builder.prompt_context_for_conversation(42, "owner-chat") == "CTX:42:owner-chat"
        assert builder.prompt_context_for_conversation(99, "other-chat") == ""
        assert recall_runtime.recall_context_for_conversation(42, "owner-chat") == "MEM:42:owner-chat"
        assert recall_runtime.recall_context_for_conversation(99, "other-chat") == ""
        assert recall_chat_patch.recall_context_for_conversation(99, "other-chat") == ""

        middleware = app.middlewares[-1]

        async def handler(_request):
            return "ok"

        owner_request = SimpleNamespace(path="/mobile-api/v1/agents/status", user_id=42)
        other_request = SimpleNamespace(path="/mobile-api/v1/agents/status", user_id=99)
        unrelated_request = SimpleNamespace(path="/mobile-api/v1/chat", user_id=99)

        assert asyncio.run(middleware(owner_request, handler)) == "ok"
        blocked = asyncio.run(middleware(other_request, handler))
        assert blocked["status"] == 503
        assert blocked["payload"]["error"] == "velia_agent_builder_disabled"
        assert asyncio.run(middleware(unrelated_request, handler)) == "ok"
    finally:
        _restore(builder, "builder_enabled", original_builder_enabled)
        _restore(builder, "prompt_context_for_conversation", original_prompt_context)
        _restore(recall_service, "recall_enabled", original_recall_enabled_service)
        _restore(recall_runtime, "recall_enabled", original_recall_enabled_runtime)
        _restore(recall_runtime, "recall_context_for_conversation", original_recall_context_runtime)
        _restore(recall_chat_patch, "recall_context_for_conversation", original_recall_context_chat)
        for module, names in (
            (
                builder,
                (
                    "_velia_owner_rollout_original_builder_enabled",
                    "_velia_owner_rollout_original_prompt_context",
                ),
            ),
            (recall_service, ("_velia_owner_rollout_original_recall_enabled",)),
            (recall_runtime, ("_velia_owner_rollout_original_recall_context",)),
        ):
            for name in names:
                if hasattr(module, name):
                    delattr(module, name)


def test_global_builder_rollout_is_not_restricted_by_owner_middleware(monkeypatch):
    monkeypatch.setenv("VELIA_AGENT_OWNER_ROLLOUT_ENABLED", "true")
    monkeypatch.setenv("ADMIN_ID", "42")

    original_builder_enabled = builder.builder_enabled
    original_prompt_context = builder.prompt_context_for_conversation
    original_recall_enabled_service = recall_service.recall_enabled
    original_recall_enabled_runtime = recall_runtime.recall_enabled
    original_recall_context_runtime = recall_runtime.recall_context_for_conversation
    original_recall_context_chat = recall_chat_patch.recall_context_for_conversation

    builder.builder_enabled = lambda: True
    builder.prompt_context_for_conversation = lambda user_id, conversation_id: "CTX"
    recall_service.recall_enabled = lambda: True
    recall_runtime.recall_enabled = lambda: True
    recall_runtime.recall_context_for_conversation = lambda user_id, conversation_id: "MEM"
    recall_chat_patch.recall_context_for_conversation = recall_runtime.recall_context_for_conversation

    class Routes:
        @staticmethod
        def _require_mobile_auth(request):
            return {"user_id": request.user_id} if request.user_id else None

        @staticmethod
        def _json_response(payload, status=200):
            return {"status": status, "payload": payload}

    app = web.Application()
    try:
        rollout.install(app, Routes)
        middleware = app.middlewares[-1]

        async def handler(_request):
            return "ok"

        request = SimpleNamespace(path="/mobile-api/v1/agents/status", user_id=999)
        assert asyncio.run(middleware(request, handler)) == "ok"
        assert builder.prompt_context_for_conversation(999, "chat") == "CTX"
        assert recall_runtime.recall_context_for_conversation(999, "chat") == "MEM"
    finally:
        _restore(builder, "builder_enabled", original_builder_enabled)
        _restore(builder, "prompt_context_for_conversation", original_prompt_context)
        _restore(recall_service, "recall_enabled", original_recall_enabled_service)
        _restore(recall_runtime, "recall_enabled", original_recall_enabled_runtime)
        _restore(recall_runtime, "recall_context_for_conversation", original_recall_context_runtime)
        _restore(recall_chat_patch, "recall_context_for_conversation", original_recall_context_chat)
        for module, names in (
            (
                builder,
                (
                    "_velia_owner_rollout_original_builder_enabled",
                    "_velia_owner_rollout_original_prompt_context",
                ),
            ),
            (recall_service, ("_velia_owner_rollout_original_recall_enabled",)),
            (recall_runtime, ("_velia_owner_rollout_original_recall_context",)),
        ):
            for name in names:
                if hasattr(module, name):
                    delattr(module, name)
