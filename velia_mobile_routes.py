import html
import json
import os
from typing import Any, Callable, Dict, Optional

from aiohttp import web

from db.database import get_user, get_subscription_until, is_subscribed
from services.velia_chat_service import (
    create_conversation,
    delete_conversation,
    get_conversation,
    get_usage_summary,
    is_debug_usage_enabled_for_user,
    is_velia_chat_enabled_for_user,
    list_conversations,
    list_messages,
    send_message,
    update_conversation,
)
from services.velia_mobile_auth_service import (
    authenticate_access_token,
    create_pairing_code,
    exchange_pairing_code,
    revoke_access_token,
    rotate_refresh_token,
)


MAX_JSON_BYTES = 64 * 1024


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _json_response(data: Dict[str, Any], status: int = 200) -> web.Response:
    response = web.Response(
        text=json.dumps(data, ensure_ascii=False, default=str),
        status=status,
        content_type="application/json",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _html_response(content: str, status: int = 200) -> web.Response:
    response = web.Response(text=content, status=status, content_type="text/html")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


async def _read_json(request: web.Request) -> Optional[Dict[str, Any]]:
    if request.content_length is not None and request.content_length > MAX_JSON_BYTES:
        return None
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _request_ip(request: web.Request) -> str:
    forwarded = str(request.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    return forwarded or str(request.remote or "")


def _bearer_token(request: web.Request) -> str:
    authorization = str(request.headers.get("Authorization") or "").strip()
    if not authorization.lower().startswith("bearer "):
        return ""
    return authorization[7:].strip()


def _require_mobile_auth(request: web.Request) -> Optional[Dict[str, Any]]:
    token = _bearer_token(request)
    return authenticate_access_token(token) if token else None


def _mobile_api_available() -> bool:
    return _env_bool("VELIA_MOBILE_API_ENABLED", False)


def _disabled_response() -> web.Response:
    return _json_response({"ok": False, "error": "velia_mobile_api_disabled"}, status=503)


def _pairing_page(*, authenticated: bool, pairing: Optional[Dict[str, Any]] = None) -> str:
    if not authenticated:
        body = """
        <h1>Подключение VELIA Android</h1>
        <p>Сначала войдите в DeepAlpha WebApp через Telegram или Google.</p>
        <p><a class="button" href="/app">Открыть WebApp</a></p>
        <p>После входа снова откройте <strong>/mobile-connect</strong>.</p>
        """
    elif not pairing or not pairing.get("ok"):
        body = """
        <h1>Подключение VELIA Android</h1>
        <p>Не удалось создать одноразовый код. Повторите попытку позже.</p>
        """
    else:
        code = html.escape(str(pairing.get("pairing_code") or ""))
        expires = int(pairing.get("expires_in") or 0) // 60
        body = f"""
        <h1>Подключение VELIA Android</h1>
        <p>Введи этот одноразовый код в приложении:</p>
        <div class="code">{code}</div>
        <p>Код действует примерно {expires} мин. и сработает только один раз.</p>
        <p class="warning">Никому не отправляй этот код.</p>
        <p><a class="button" href="/mobile-connect">Создать новый код</a></p>
        """
    return f"""<!doctype html>
    <html lang="ru"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>VELIA Android</title>
    <style>
      body{{background:#121116;color:#f3eff8;font-family:system-ui,sans-serif;margin:0;padding:28px;}}
      main{{max-width:620px;margin:8vh auto;background:#201e25;border-radius:24px;padding:28px;}}
      h1{{font-size:28px;}}
      p{{line-height:1.55;color:#d3ccd9;}}
      .code{{font-size:30px;letter-spacing:3px;font-weight:800;padding:20px;border-radius:18px;background:#332d48;text-align:center;margin:24px 0;word-break:break-all;}}
      .button{{display:inline-block;color:white;background:#6b4eff;text-decoration:none;padding:12px 18px;border-radius:14px;}}
      .warning{{color:#ffcf70;}}
    </style></head><body><main>{body}</main></body></html>"""


def setup_velia_mobile_routes(
    app: web.Application,
    web_user_resolver: Callable[[web.Request], Optional[int]],
) -> None:
    async def handle_mobile_connect(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _html_response(
                "<h1>VELIA Mobile API временно выключен</h1>",
                status=503,
            )
        try:
            user_id = int(web_user_resolver(request) or 0)
        except Exception:
            user_id = 0
        if user_id <= 0:
            return _html_response(_pairing_page(authenticated=False), status=401)
        pairing = create_pairing_code(
            user_id,
            user_agent=str(request.headers.get("User-Agent") or ""),
            ip=_request_ip(request),
        )
        return _html_response(_pairing_page(authenticated=True, pairing=pairing))

    async def handle_health(request: web.Request) -> web.Response:
        return _json_response(
            {
                "ok": True,
                "enabled": _mobile_api_available(),
                "chat_enabled": _env_bool("VELIA_CHAT_ENABLED", False),
                "version": "v1-beta",
            }
        )

    async def handle_auth_exchange(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _disabled_response()
        data = await _read_json(request)
        if data is None:
            return _json_response({"ok": False, "error": "invalid_json"}, status=400)
        result = exchange_pairing_code(
            str(data.get("pairing_code") or ""),
            device_id=str(data.get("device_id") or ""),
            device_name=str(data.get("device_name") or ""),
            user_agent=str(request.headers.get("User-Agent") or ""),
            ip=_request_ip(request),
        )
        return _json_response(result, status=200 if result.get("ok") else 401)

    async def handle_auth_refresh(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _disabled_response()
        data = await _read_json(request)
        if data is None:
            return _json_response({"ok": False, "error": "invalid_json"}, status=400)
        result = rotate_refresh_token(
            str(data.get("refresh_token") or ""),
            device_id=str(data.get("device_id") or ""),
            user_agent=str(request.headers.get("User-Agent") or ""),
            ip=_request_ip(request),
        )
        return _json_response(result, status=200 if result.get("ok") else 401)

    async def handle_auth_logout(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _disabled_response()
        token = _bearer_token(request)
        if not token:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        revoke_access_token(token)
        return _json_response({"ok": True})

    async def handle_me(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _disabled_response()
        auth = _require_mobile_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        user_id = int(auth["user_id"])
        user = get_user(user_id) or {}
        return _json_response(
            {
                "ok": True,
                "user": {
                    "id": user_id,
                    "username": user.get("username"),
                    "first_name": user.get("first_name"),
                    "is_vip": bool(user.get("is_vip")),
                    "is_subscribed": bool(is_subscribed(user_id)),
                    "subscription_until": get_subscription_until(user_id),
                },
                "session": {
                    "id": auth.get("session_id"),
                    "device_name": auth.get("device_name"),
                },
                "features": {
                    "chat": is_velia_chat_enabled_for_user(user_id),
                    "debug_usage": is_debug_usage_enabled_for_user(user_id),
                },
            }
        )

    async def handle_conversations_list(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _disabled_response()
        auth = _require_mobile_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        include_archived = str(request.query.get("archived") or "").lower() in {"1", "true", "yes"}
        try:
            limit = int(request.query.get("limit") or 50)
        except ValueError:
            limit = 50
        conversations = list_conversations(
            int(auth["user_id"]),
            include_archived=include_archived,
            limit=limit,
        )
        return _json_response({"ok": True, "conversations": conversations})

    async def handle_conversations_create(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _disabled_response()
        auth = _require_mobile_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        data = await _read_json(request)
        if data is None:
            return _json_response({"ok": False, "error": "invalid_json"}, status=400)
        conversation = create_conversation(
            int(auth["user_id"]),
            title=str(data.get("title") or ""),
        )
        return _json_response({"ok": True, "conversation": conversation}, status=201)

    async def handle_conversation_get(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _disabled_response()
        auth = _require_mobile_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        conversation = get_conversation(
            int(auth["user_id"]),
            request.match_info["conversation_id"],
        )
        if not conversation:
            return _json_response({"ok": False, "error": "conversation_not_found"}, status=404)
        return _json_response({"ok": True, "conversation": conversation})

    async def handle_conversation_patch(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _disabled_response()
        auth = _require_mobile_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        data = await _read_json(request)
        if data is None:
            return _json_response({"ok": False, "error": "invalid_json"}, status=400)
        try:
            conversation = update_conversation(
                int(auth["user_id"]),
                request.match_info["conversation_id"],
                title=data.get("title") if "title" in data else None,
                is_pinned=data.get("is_pinned") if "is_pinned" in data else None,
                is_archived=data.get("is_archived") if "is_archived" in data else None,
            )
        except ValueError as exc:
            return _json_response({"ok": False, "error": str(exc)}, status=400)
        if not conversation:
            return _json_response({"ok": False, "error": "conversation_not_found"}, status=404)
        return _json_response({"ok": True, "conversation": conversation})

    async def handle_conversation_delete(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _disabled_response()
        auth = _require_mobile_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        deleted = delete_conversation(
            int(auth["user_id"]),
            request.match_info["conversation_id"],
        )
        if not deleted:
            return _json_response({"ok": False, "error": "conversation_not_found"}, status=404)
        return _json_response({"ok": True})

    async def handle_messages_list(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _disabled_response()
        auth = _require_mobile_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            limit = int(request.query.get("limit") or 100)
        except ValueError:
            limit = 100
        messages = list_messages(
            int(auth["user_id"]),
            request.match_info["conversation_id"],
            limit=limit,
        )
        if messages is None:
            return _json_response({"ok": False, "error": "conversation_not_found"}, status=404)
        return _json_response({"ok": True, "messages": messages})

    async def handle_messages_send(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _disabled_response()
        auth = _require_mobile_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        data = await _read_json(request)
        if data is None:
            return _json_response({"ok": False, "error": "invalid_json"}, status=400)
        idempotency_key = str(
            request.headers.get("Idempotency-Key") or data.get("idempotency_key") or ""
        ).strip()
        result = send_message(
            int(auth["user_id"]),
            request.match_info["conversation_id"],
            str(data.get("content") or ""),
            idempotency_key=idempotency_key,
        )
        if result.get("ok"):
            return _json_response(result, status=200 if result.get("duplicate") else 201)
        error = str(result.get("error") or "generation_failed")
        status = 400
        if error in {"conversation_not_found"}:
            status = 404
        elif error in {"generation_in_progress"} or result.get("pending"):
            status = 409
        elif error.endswith("limit_exceeded"):
            status = 429
        elif error in {"velia_chat_disabled"}:
            status = 503
        elif error in {
            "timeout", "connection_error", "rate_limit", "server_error",
            "empty_200", "json_parse_error", "generation_exception",
            "generation_failed",
        }:
            status = 502
        return _json_response(result, status=status)

    async def handle_usage(request: web.Request) -> web.Response:
        if not _mobile_api_available():
            return _disabled_response()
        auth = _require_mobile_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        user_id = int(auth["user_id"])
        if not is_debug_usage_enabled_for_user(user_id):
            return _json_response({"ok": False, "error": "forbidden"}, status=403)
        return _json_response({"ok": True, "usage": get_usage_summary(user_id)})

    app.router.add_get("/mobile-connect", handle_mobile_connect)
    app.router.add_get("/mobile-api/v1/health", handle_health)
    app.router.add_post("/mobile-api/v1/auth/exchange", handle_auth_exchange)
    app.router.add_post("/mobile-api/v1/auth/refresh", handle_auth_refresh)
    app.router.add_post("/mobile-api/v1/auth/logout", handle_auth_logout)
    app.router.add_get("/mobile-api/v1/me", handle_me)
    app.router.add_get("/mobile-api/v1/conversations", handle_conversations_list)
    app.router.add_post("/mobile-api/v1/conversations", handle_conversations_create)
    app.router.add_get("/mobile-api/v1/conversations/{conversation_id}", handle_conversation_get)
    app.router.add_patch("/mobile-api/v1/conversations/{conversation_id}", handle_conversation_patch)
    app.router.add_delete("/mobile-api/v1/conversations/{conversation_id}", handle_conversation_delete)
    app.router.add_get(
        "/mobile-api/v1/conversations/{conversation_id}/messages",
        handle_messages_list,
    )
    app.router.add_post(
        "/mobile-api/v1/conversations/{conversation_id}/messages",
        handle_messages_send,
    )
    app.router.add_get("/mobile-api/v1/usage", handle_usage)
