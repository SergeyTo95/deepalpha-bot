"""Opt-in desktop agent gateway; upstream credentials stay on the server.

Preview access is restricted to explicitly configured user ids. This route does
not execute tools: Harness executes them on the user's computer.
"""
import asyncio
import json
import os
import time
from collections import deque

from aiohttp import ClientError, ClientSession, ClientTimeout, web

MAX_BODY = 1024 * 1024
MODEL_ID = "velia-pro"


def validate_payload(data):
    if not isinstance(data, dict) or data.get("model") != MODEL_ID:
        raise ValueError("unsupported_model")
    messages = data.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 256:
        raise ValueError("invalid_messages")
    if any(not isinstance(m, dict) or m.get("role") not in
           {"system", "developer", "user", "assistant", "tool"} for m in messages):
        raise ValueError("invalid_messages")
    # Text-only preview. Reject images instead of silently charging for vision.
    normalized_messages = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            if any(not isinstance(part, dict) or part.get("type") != "text"
                   or not isinstance(part.get("text"), str) for part in content):
                raise ValueError("text_only_preview")
            content = "\n".join(part["text"] for part in content)
        elif content is not None and not isinstance(content, str):
            raise ValueError("text_only_preview")
        normalized_messages.append({**message, "content": content})
    tools = data.get("tools", [])
    if not isinstance(tools, list) or len(tools) > 128:
        raise ValueError("invalid_tools")
    for tool in tools:
        if (not isinstance(tool, dict) or tool.get("type") != "function"
                or not isinstance(tool.get("function"), dict)
                or not isinstance(tool["function"].get("name"), str)):
            raise ValueError("invalid_tools")
    limit = data.get("max_completion_tokens", data.get("max_tokens", 4096))
    if type(limit) is not int or not 1 <= limit <= 4096:
        raise ValueError("invalid_output_limit")
    if type(data.get("stream", False)) is not bool:
        raise ValueError("invalid_stream")
    result = {"model": os.getenv("VELIA_DESKTOP_PRO_MODEL", "kimi-k3"),
              "messages": normalized_messages, "max_completion_tokens": limit,
              "stream": data.get("stream", False)}
    if tools:
        result["tools"] = tools
    if "tool_choice" in data:
        choice = data["tool_choice"]
        if choice not in ("auto", "none", "required"):
            raise ValueError("invalid_tool_choice")
        result["tool_choice"] = choice
    return result


def setup_velia_desktop_routes(app, authenticate):
    """Mount a disabled-by-default, bounded preview using mobile access tokens."""
    calls = {}
    active = set()

    def error(code, status):
        return web.json_response({"error": {"message": code, "type": "velia_desktop_error"}},
                                 status=status, headers={"Cache-Control": "no-store"})

    async def authorize(request):
        if os.getenv("VELIA_DESKTOP_API_ENABLED", "").lower() not in {"true", "1"}:
            return None, error("desktop_api_disabled", 503)
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        identity = await asyncio.to_thread(authenticate, token) if token else None
        if not identity:
            return None, error("unauthorized", 401)
        allowed = {value.strip() for value in os.getenv("VELIA_DESKTOP_PREVIEW_USER_IDS", "").split(",")}
        user_id = str(identity["user_id"])
        if user_id not in allowed:
            return None, error("preview_access_required", 403)
        return user_id, None

    async def models(request):
        _, failure = await authorize(request)
        if failure is not None:
            return failure
        return web.json_response({"object": "list", "data": [
            {"id": MODEL_ID, "object": "model", "owned_by": "velia"}
        ]}, headers={"Cache-Control": "no-store"})

    async def complete(request):
        user_id, failure = await authorize(request)
        if failure is not None:
            return failure
        if user_id in active:
            return error("request_already_running", 429)
        queue = calls.setdefault(user_id, deque())
        now = time.monotonic()
        while queue and queue[0] < now - 3600:
            queue.popleft()
        if len(queue) >= 30:
            return error("preview_hourly_request_limit", 429)
        body = bytearray()
        async for chunk in request.content.iter_chunked(65536):
            body.extend(chunk)
            if len(body) > MAX_BODY:
                return error("request_too_large", 413)
        try:
            payload = validate_payload(json.loads(body))
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            return error(str(exc) if str(exc) in {"unsupported_model", "invalid_messages", "text_only_preview",
                         "invalid_tools", "invalid_output_limit", "invalid_stream", "invalid_tool_choice"}
                         else "invalid_json", 400)
        key = os.getenv("KIMI_API_KEY", "").strip()
        if not key:
            return error("model_unavailable", 503)
        # Body reading yields: repeat admission immediately before reserving.
        if user_id in active or len(active) >= 2 or len(queue) >= 30:
            return error("preview_capacity_exceeded", 429)
        # The desktop route has its own bounded preview budget (30 calls/hour,
        # 4096 output tokens/call). It must gain commercial accounting before GA.
        active.add(user_id)
        queue.append(now)
        response = None
        try:
            async with ClientSession(timeout=ClientTimeout(total=180, sock_read=90)) as client:
                endpoint = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1").rstrip("/")
                async with client.post(endpoint + "/chat/completions", json=payload,
                                       headers={"Authorization": "Bearer " + key}) as upstream:
                    if upstream.status != 200:
                        return error("model_request_failed", 502)
                    if payload["stream"]:
                        if "text/event-stream" not in upstream.headers.get("Content-Type", ""):
                            return error("invalid_model_stream", 502)
                        response = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                            "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
                        await response.prepare(request)
                        async for chunk in upstream.content.iter_chunked(65536):
                            await response.write(chunk)
                        await response.write_eof()
                        return response
                    result = await upstream.json()
                    if not isinstance(result, dict) or not isinstance(result.get("choices"), list):
                        return error("invalid_model_response", 502)
                    result["model"] = MODEL_ID
                    return web.json_response(result, headers={"Cache-Control": "no-store"})
        except (TimeoutError, OSError, ClientError, ValueError):
            if response is not None and response.prepared:
                response.force_close()
                return response
            return error("model_connection_failed", 502)
        finally:
            active.discard(user_id)

    app.router.add_get("/desktop-api/v1/models", models)
    app.router.add_post("/desktop-api/v1/chat/completions", complete)
