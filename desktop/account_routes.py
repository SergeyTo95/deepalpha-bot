"""Bounded, same-origin access to the signed-in account's existing chat store."""
import json
import re
from aiohttp import ClientError, web
from velia_desktop_routes import AuthenticationUnavailable, FLASH_ID, MODEL_ID, flash_enabled

ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
REQUEST_ID = re.compile(r"[A-Za-z0-9:_-]{8,128}\Z")
ERRORS = {"conversation_not_found", "generation_in_progress", "flash_busy", "flash_unavailable",
    "flash_context_too_long", "flash_timeout", "flash_provider_error", "flash_invalid_response",
    "velia_chat_disabled", "pro_tokens_required", "token_balance_unavailable",
    "idempotency_mode_mismatch", "user_daily_limit_exceeded", "global_daily_limit_exceeded"}


def conversation(value):
    if not isinstance(value, dict) or not ID.fullmatch(str(value.get("id", ""))):
        raise ValueError("invalid_account_response")
    return {key: value.get(key) for key in ("id", "title", "is_pinned", "created_at", "updated_at")}


def message(value):
    if not isinstance(value, dict) or value.get("role") not in {"user", "assistant"}:
        raise ValueError("invalid_account_response")
    return {key: value.get(key) for key in ("id", "role", "content", "status", "chat_mode", "created_at")}


async def account_events(source, model):
    """Strip usage, internal plans, provider identifiers and account credentials."""
    pending = b""
    completed = False
    async for chunk in source:
        pending = (pending + chunk).replace(b"\r\n", b"\n")
        if len(pending) > 2 * 1024 * 1024:
            raise ValueError("invalid_account_stream")
        while b"\n\n" in pending:
            frame, pending = pending.split(b"\n\n", 1)
            data = b"\n".join(line[5:].lstrip() for line in frame.split(b"\n") if line.startswith(b"data:"))
            if not data:
                yield b": ping\n\n"
                continue
            event = json.loads(data)
            safe = None
            kind = event.get("type")
            if kind == "ready":
                yield b": ready\n\n"
            elif kind == "delta" and isinstance(event.get("text"), str):
                safe = {"model": model, "choices": [{"delta": {"content": event["text"]}}]}
            elif kind == "reset":
                safe = {"reset": True}
            elif kind == "error":
                code = event.get("error")
                safe = {"error": {"message": code if code in ERRORS else "model_request_failed"}}
            elif kind == "complete":
                result = event.get("result", {})
                final = result.get("assistant_message", {}).get("content") if isinstance(result, dict) else None
                if not isinstance(final, str) or not final.strip():
                    raise ValueError("invalid_account_stream")
                # Duplicate requests may contain no deltas; persisted content is canonical.
                safe = {"model": model, "reset": True,
                    "choices": [{"delta": {"content": final}, "finish_reason": "stop"}]}
                completed = True
            if safe is not None:
                yield ("data: " + json.dumps(safe, ensure_ascii=False) + "\n\n").encode()
            if completed:
                yield b"data: [DONE]\n\n"
                return
            if kind == "error":
                return
    if not completed:
        raise ValueError("incomplete_account_stream")


def setup_account_routes(app, *, session_for, same_origin, upstream, upstream_stream,
                         authorize_model, handlers, json_response):
    def error(code, status):
        return json_response({"ok": False, "error": code}, status)

    async def read_body(request):
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("invalid_request")
        return data

    @web.middleware
    async def boundary(request, handler):
        if not request.path.startswith("/web-api/v1/conversations"):
            return await handler(request)
        if request.method != "GET" and not same_origin(request):
            return error("invalid_origin", 403)
        try:
            session = await session_for(request)
            if not session:
                return error("unauthorized", 401)
            request[ACCOUNT_SESSION] = session
            value = request.match_info.get("conversation_id")
            if value is not None and not ID.fullmatch(value):
                return error("conversation_not_found", 404)
            return await handler(request)
        except web.HTTPRequestEntityTooLarge:
            return error("request_too_large", 413)
        except (ValueError, TypeError, UnicodeDecodeError):
            return error("invalid_request", 400)
        except (AuthenticationUnavailable, ClientError, TimeoutError, OSError):
            return error("account_service_unavailable", 503)
    app.middlewares.append(boundary)

    async def relay(request, method, suffix="", data=None):
        session = request[ACCOUNT_SESSION]
        path = "/mobile-api/v1/conversations"
        if request.match_info.get("conversation_id"):
            path += "/" + request.match_info["conversation_id"]
        status, result = await upstream(method, path + suffix, token=session.access, data=data)
        if status in {401, 403}:
            return status, {"ok": False, "error": "unauthorized"}
        if not result.get("ok"):
            return status, {"ok": False, "error": "conversation_not_found" if status == 404 else "account_service_unavailable"}
        return status, result

    async def listing(request):
        status, result = await relay(request, "GET", "?limit=100")
        if result.get("ok"):
            result = {"ok": True, "conversations": [conversation(v) for v in result.get("conversations", [])[:100]]}
        return json_response(result, status)

    async def create(request):
        data = await read_body(request)
        if set(data) != {"title"} or not isinstance(data["title"], str) or not 1 <= len(data["title"]) <= 120:
            return error("invalid_request", 400)
        status, result = await relay(request, "POST", data=data)
        if result.get("ok"):
            result = {"ok": True, "conversation": conversation(result.get("conversation"))}
        return json_response(result, status)

    async def messages(request):
        status, result = await relay(request, "GET", "/messages?limit=200")
        if result.get("ok"):
            result = {"ok": True, "messages": [message(v) for v in result.get("messages", [])[:200]
                if v.get("role") in {"user", "assistant"}]}
        return json_response(result, status)

    async def delete(request):
        data = await read_body(request)
        if data:
            return error("invalid_request", 400)
        status, result = await relay(request, "DELETE")
        return json_response({"ok": bool(result.get("ok")), **({"error": result["error"]} if "error" in result else {})}, status)

    async def send(request):
        data = await read_body(request)
        if (set(data) != {"content", "model", "idempotency_key"} or data.get("model") not in {FLASH_ID, MODEL_ID}
                or not isinstance(data.get("content"), str) or not data["content"].strip()
                or len(data["content"]) > 12000 or not REQUEST_ID.fullmatch(str(data.get("idempotency_key", "")))):
            return error("invalid_request", 400)
        if data["model"] == FLASH_ID and not flash_enabled():
            return error("flash_unavailable", 503)
        session = request[ACCOUNT_SESSION]
        permission = await authorize_model(session.access, data["model"])
        if permission:
            return error(*permission)
        failure = handlers["reserve"](session.user_id)
        if failure is not None:
            return failure
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        try:
            source = upstream_stream("/mobile-api/v1/conversations/" + request.match_info["conversation_id"] + "/messages/stream",
                token=session.access, data={"content": data["content"], "chat_mode": "flash" if data["model"] == FLASH_ID else "pro",
                    "idempotency_key": data["idempotency_key"]})
            stream = account_events(source, data["model"])
            first = await anext(stream)
            await response.prepare(request)
            await response.write(first)
            async for chunk in stream:
                await response.write(chunk)
            await response.write_eof()
            return response
        except (AuthenticationUnavailable, ClientError, TimeoutError, ValueError, OSError, StopAsyncIteration):
            if response.prepared:
                response.force_close()
                return response
            return error("account_service_unavailable", 503)
        finally:
            try:
                await stream.aclose()
            finally:
                try:
                    await source.aclose()
                finally:
                    handlers["release"](session.user_id)

    app.router.add_get("/web-api/v1/conversations", listing)
    app.router.add_post("/web-api/v1/conversations", create)
    app.router.add_get("/web-api/v1/conversations/{conversation_id}/messages", messages)
    app.router.add_post("/web-api/v1/conversations/{conversation_id}/messages/stream", send)
    app.router.add_post("/web-api/v1/conversations/{conversation_id}/delete", delete)


ACCOUNT_SESSION = web.RequestKey("velia_account_session", object)
