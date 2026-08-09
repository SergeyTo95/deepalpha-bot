from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from services import velia_agent_builder_service as builder

logger = logging.getLogger(__name__)
_PREFIX = "/mobile-api/v1/agents"


def _auth(routes_module: Any, request: web.Request) -> Optional[Dict[str, Any]]:
    return routes_module._require_mobile_auth(request)


def _public_agent(value: Any) -> Dict[str, Any]:
    item = dict(value or {}) if isinstance(value, dict) else {}
    item.pop("memory_mode", None)
    item["context_scope"] = "conversation"
    return item


def _json_error(routes_module: Any, exc: Exception) -> web.Response:
    if isinstance(exc, builder.AgentBuilderError):
        return routes_module._json_response(
            {"ok": False, "error": exc.code, "detail": exc.detail},
            status=exc.status,
        )
    logger.exception("VELIA_AGENT_BUILDER_ROUTE_FAILED")
    return routes_module._json_response(
        {"ok": False, "error": "velia_agent_builder_internal_error"},
        status=500,
    )


def _require_available(routes_module: Any) -> Optional[web.Response]:
    if not routes_module._mobile_api_available():
        return routes_module._disabled_response()
    if not builder.builder_enabled():
        return routes_module._json_response(
            {"ok": False, "error": "velia_agent_builder_disabled"},
            status=503,
        )
    return None


async def _body(request: web.Request) -> Dict[str, Any]:
    try:
        value = await request.json()
    except Exception as exc:
        raise builder.AgentBuilderError("velia_agent_builder_json_invalid") from exc
    if not isinstance(value, dict):
        raise builder.AgentBuilderError("velia_agent_builder_json_invalid")
    return value


def setup_velia_agent_builder_routes(app: web.Application, routes_module: Any) -> None:
    # Install the prompt layer at the final Agent bootstrap point. It is inert
    # while VELIA_AGENT_BUILDER_ENABLED is false and never changes ordinary chats.
    from services import velia_chat_service as chat_service
    from services.velia_agent_builder_chat_patch import install as install_builder_chat

    install_builder_chat(chat_service)
    if app.get("velia_agent_builder_routes_installed"):
        return

    async def status(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        return routes_module._json_response(
            {
                "ok": True,
                "enabled": builder.builder_enabled(),
                "product": "VELIA",
                "brain": "Velyon Core",
                "custom_agents": True,
                "conversation_scoped_agent_context": True,
                "dedicated_long_term_agent_memory": False,
                "child_conversations": True,
                "external_actions_still_permission_gated": True,
            }
        )

    async def capabilities(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            query = str(request.query.get("q") or "")
            category = str(request.query.get("category") or "")
            try:
                limit = int(request.query.get("limit", "50") or 50)
            except (TypeError, ValueError):
                limit = 50
            items = await asyncio.to_thread(
                builder.list_capabilities,
                query=query,
                category=category,
                limit=limit,
            )
            return routes_module._json_response({"ok": True, "capabilities": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def list_agents(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            items = await asyncio.to_thread(builder.list_agents, int(auth["user_id"]))
            return routes_module._json_response({"ok": True, "agents": [_public_agent(item) for item in items]})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def create_agent(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await _body(request)
            item = await asyncio.to_thread(
                builder.create_agent,
                int(auth["user_id"]),
                str(payload.get("name") or ""),
                description=str(payload.get("description") or ""),
                instructions=str(payload.get("instructions") or ""),
                capability_ids=payload.get("capability_ids"),
                can_create_chats=bool(payload.get("can_create_chats", False)),
            )
            return routes_module._json_response({"ok": True, "agent": _public_agent(item)}, status=201)
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def get_agent(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                builder.get_agent,
                int(auth["user_id"]),
                request.match_info["agent_id"],
            )
            return routes_module._json_response({"ok": True, "agent": _public_agent(item)})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def patch_agent(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await _body(request)
            item = await asyncio.to_thread(
                builder.update_agent,
                int(auth["user_id"]),
                request.match_info["agent_id"],
                payload,
            )
            return routes_module._json_response({"ok": True, "agent": _public_agent(item)})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def delete_agent(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            await asyncio.to_thread(
                builder.archive_agent,
                int(auth["user_id"]),
                request.match_info["agent_id"],
            )
            return routes_module._json_response({"ok": True})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def list_conversations(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            items = await asyncio.to_thread(
                builder.list_agent_sessions,
                int(auth["user_id"]),
                request.match_info["agent_id"],
            )
            return routes_module._json_response({"ok": True, "sessions": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def create_conversation(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await _body(request)
            result = await asyncio.to_thread(
                builder.create_agent_conversation,
                int(auth["user_id"]),
                request.match_info["agent_id"],
                title=str(payload.get("title") or ""),
            )
            return routes_module._json_response({"ok": True, **result}, status=201)
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def get_session(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                builder.get_session,
                int(auth["user_id"]),
                request.match_info["session_id"],
            )
            return routes_module._json_response({"ok": True, "session": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def list_children(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            items = await asyncio.to_thread(
                builder.list_child_sessions,
                int(auth["user_id"]),
                request.match_info["session_id"],
            )
            return routes_module._json_response({"ok": True, "sessions": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def create_child(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await _body(request)
            result = await asyncio.to_thread(
                builder.create_child_conversation,
                int(auth["user_id"]),
                request.match_info["session_id"],
                title=str(payload.get("title") or ""),
                purpose=str(payload.get("purpose") or ""),
                delegation_key=str(payload.get("idempotency_key") or ""),
            )
            return routes_module._json_response(
                {"ok": True, **result},
                status=200 if result.get("duplicate") else 201,
            )
        except Exception as exc:
            return _json_error(routes_module, exc)

    # Static routes must be registered before dynamic /{agent_id} routes.
    app.router.add_get(f"{_PREFIX}/status", status)
    app.router.add_get(f"{_PREFIX}/capabilities", capabilities)
    app.router.add_get(f"{_PREFIX}/sessions/{{session_id}}", get_session)
    app.router.add_get(f"{_PREFIX}/sessions/{{session_id}}/children", list_children)
    app.router.add_post(f"{_PREFIX}/sessions/{{session_id}}/children", create_child)
    app.router.add_get(_PREFIX, list_agents)
    app.router.add_post(_PREFIX, create_agent)
    app.router.add_get(f"{_PREFIX}/{{agent_id}}", get_agent)
    app.router.add_patch(f"{_PREFIX}/{{agent_id}}", patch_agent)
    app.router.add_delete(f"{_PREFIX}/{{agent_id}}", delete_agent)
    app.router.add_get(f"{_PREFIX}/{{agent_id}}/conversations", list_conversations)
    app.router.add_post(f"{_PREFIX}/{{agent_id}}/conversations", create_conversation)
    app["velia_agent_builder_routes_installed"] = True
