from typing import Any

from aiohttp import web

from services.velia_conversation_links_service import (
    ConversationUxError,
    link_conversations,
    list_conversation_links,
    unlink_conversation,
)


def _error_response(mobile_routes_module: Any, error: ConversationUxError) -> web.Response:
    return mobile_routes_module._json_response(
        {"ok": False, "error": error.code},
        status=error.status,
    )


def setup_velia_conversation_links_routes(app: web.Application, mobile_routes_module: Any) -> None:
    if app.get("velia_conversation_links_routes_installed"):
        return

    async def handle_links_list(request: web.Request) -> web.Response:
        if not mobile_routes_module._mobile_api_available():
            return mobile_routes_module._disabled_response()
        auth = mobile_routes_module._require_mobile_auth(request)
        if not auth:
            return mobile_routes_module._json_response(
                {"ok": False, "error": "unauthorized"},
                status=401,
            )
        try:
            links = list_conversation_links(
                int(auth["user_id"]),
                request.match_info["conversation_id"],
            )
        except ConversationUxError as error:
            return _error_response(mobile_routes_module, error)
        return mobile_routes_module._json_response({"ok": True, "links": links})

    async def handle_links_add(request: web.Request) -> web.Response:
        if not mobile_routes_module._mobile_api_available():
            return mobile_routes_module._disabled_response()
        auth = mobile_routes_module._require_mobile_auth(request)
        if not auth:
            return mobile_routes_module._json_response(
                {"ok": False, "error": "unauthorized"},
                status=401,
            )
        data = await mobile_routes_module._read_json(request)
        if data is None or not isinstance(data.get("source_conversation_ids"), list):
            return mobile_routes_module._json_response(
                {"ok": False, "error": "invalid_json"},
                status=400,
            )
        try:
            links = link_conversations(
                int(auth["user_id"]),
                request.match_info["conversation_id"],
                data["source_conversation_ids"],
            )
        except ConversationUxError as error:
            return _error_response(mobile_routes_module, error)
        return mobile_routes_module._json_response({"ok": True, "links": links}, status=201)

    async def handle_link_delete(request: web.Request) -> web.Response:
        if not mobile_routes_module._mobile_api_available():
            return mobile_routes_module._disabled_response()
        auth = mobile_routes_module._require_mobile_auth(request)
        if not auth:
            return mobile_routes_module._json_response(
                {"ok": False, "error": "unauthorized"},
                status=401,
            )
        try:
            removed = unlink_conversation(
                int(auth["user_id"]),
                request.match_info["conversation_id"],
                request.match_info["source_conversation_id"],
            )
        except ConversationUxError as error:
            return _error_response(mobile_routes_module, error)
        if not removed:
            return mobile_routes_module._json_response(
                {"ok": False, "error": "conversation_link_not_found"},
                status=404,
            )
        return mobile_routes_module._json_response({"ok": True})

    app.router.add_get(
        "/mobile-api/v1/conversations/{conversation_id}/links",
        handle_links_list,
    )
    app.router.add_post(
        "/mobile-api/v1/conversations/{conversation_id}/links",
        handle_links_add,
    )
    app.router.add_delete(
        "/mobile-api/v1/conversations/{conversation_id}/links/{source_conversation_id}",
        handle_link_delete,
    )
    app["velia_conversation_links_routes_installed"] = True
