"""Opt-in desktop agent gateway; upstream credentials stay on the server.

Preview access is restricted to explicitly configured user ids. This route does
not execute tools: Harness executes them on the user's computer.
"""
import asyncio
import inspect
import json
import os
import time
from collections import deque
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from velia_request_understanding import understanding_messages

MAX_BODY = 1024 * 1024
MODEL_ID = "velia-pro"
FLASH_ID = "velia-flash"


def flash_endpoint():
    value = os.getenv("VELIA_DESKTOP_FLASH_BASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(value)
    private = (parsed.hostname or "").endswith(".railway.internal")
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
            or (parsed.scheme == "http" and not (private or parsed.hostname in {"127.0.0.1", "localhost", "::1"}))):
        return ""
    return value


def flash_enabled():
    return (os.getenv("VELIA_DESKTOP_FLASH_ENABLED", "").lower() in {"true", "1"}
            and bool(flash_endpoint()) and bool(os.getenv("VELIA_DESKTOP_FLASH_API_KEY", "").strip()))


def flash_messages(messages):
    # Bonsai's pinned template requires one leading system message. Reasoning
    # echoes impair its tool-loop cache; provider reasoning is disabled here.
    instructions, conversation = [], []
    for message in messages:
        if message["role"] in {"system", "developer"}:
            if message.get("content"):
                instructions.append(message["content"])
            continue
        item = {key: value for key, value in message.items()
                if key not in {"reasoning", "reasoning_content", "reasoning_text"}}
        if item.get("tool_calls") is not None:
            if not isinstance(item["tool_calls"], list) or len(item["tool_calls"]) > 32:
                raise ValueError("invalid_tool_calls")
            calls = []
            for call in item["tool_calls"]:
                if (not isinstance(call, dict) or call.get("type") != "function"
                        or not isinstance(call.get("id"), str) or not call["id"]
                        or not isinstance(call.get("function"), dict)
                        or not isinstance(call["function"].get("name"), str)):
                    raise ValueError("invalid_tool_calls")
                function = dict(call["function"])
                arguments = function.get("arguments")
                if arguments is None or arguments == "":
                    arguments = "{}"
                try:
                    parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
                except (ValueError, TypeError):
                    raise ValueError("invalid_tool_arguments") from None
                if not isinstance(parsed, dict):
                    raise ValueError("invalid_tool_arguments")
                function["arguments"] = json.dumps(parsed, ensure_ascii=False)
                calls.append({**call, "function": function})
            item["tool_calls"] = calls
        conversation.append(item)
    return [{"role": "system", "content": "\n\n".join(instructions)}] + conversation


class FlashContextTooLong(Exception):
    pass


async def check_flash_context(client, endpoint, headers, payload):
    template = {"messages": payload["messages"], "chat_template_kwargs": {"enable_thinking": False},
                "add_generation_prompt": True}
    if payload.get("tools"):
        template["tools"] = payload["tools"]
    if "tool_choice" in payload:
        template["tool_choice"] = payload["tool_choice"]
    async def post(path, data):
        async with client.post(endpoint + path, json=data, headers=headers, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError("flash_context_validation_failed")
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > 4 * 1024 * 1024:
                    raise ValueError("flash_context_validation_failed")
            return json.loads(body)
    rendered = await post("/apply-template", template)
    if not isinstance(rendered, dict) or not isinstance(rendered.get("prompt"), str):
        raise ValueError("flash_context_validation_failed")
    result = await post("/tokenize", {"content": rendered["prompt"], "add_special": True})
    if not isinstance(result, dict) or not isinstance(result.get("tokens"), list):
        raise ValueError("flash_context_validation_failed")
    try:
        context = min(8192, max(2048, int(os.getenv("VELIA_DESKTOP_FLASH_CONTEXT_TOKENS", "8192"))))
    except ValueError:
        context = 8192
    if len(result["tokens"]) + payload["max_tokens"] + 64 > context:
        raise FlashContextTooLong()


class AuthenticationUnavailable(Exception):
    """The identity authority could not verify a device session."""


def validate_payload(data):
    if not isinstance(data, dict) or data.get("model") not in {MODEL_ID, FLASH_ID}:
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
    is_flash = data["model"] == FLASH_ID
    result = {"model": os.getenv("VELIA_DESKTOP_PRO_MODEL", "kimi-k3") if not is_flash else FLASH_ID,
              "messages": understanding_messages(normalized_messages), "max_completion_tokens": limit,
              "stream": data.get("stream", False)}
    if is_flash:
        result["messages"] = flash_messages(result["messages"])
        result["max_tokens"] = min(result.pop("max_completion_tokens"), 512)
        # Keep the qualified tool profile. Use a steadier plain-text profile:
        # repeated user terms must not be discouraged into malformed words.
        result.update(temperature=0.7 if tools else 0.3, top_p=0.8, top_k=20, min_p=0.05 if tools else 0.0,
                      chat_template_kwargs={"enable_thinking": False}, reasoning_effort="none",
                      reasoning_format="deepseek", thinking_budget_tokens=0, parallel_tool_calls=False)
        if not tools:
            result["presence_penalty"] = 0.0
    elif result["model"].lower().startswith("kimi-k3"):
        effort = os.getenv("VELIA_DESKTOP_REASONING_EFFORT", "low")
        result["reasoning_effort"] = effort if effort in {"low", "medium", "high"} else "low"
    if tools:
        result["tools"] = tools
    if "tool_choice" in data:
        choice = data["tool_choice"]
        if choice not in ("auto", "none", "required"):
            raise ValueError("invalid_tool_choice")
        result["tool_choice"] = choice
    return result


def setup_velia_desktop_routes(app, authenticate, *, prepare_payload=None, filter_stream=None, authorize_model=None, enrich_payload=None):
    """Mount a disabled-by-default, bounded preview using mobile access tokens."""
    calls = {}
    active = set()

    def error(code, status):
        return web.json_response({"error": {"message": code, "type": "velia_desktop_error"}},
                                 status=status, headers={"Cache-Control": "no-store"})

    def reserve(user_id):
        user_id = str(user_id)
        queue = calls.setdefault(user_id, deque())
        now = time.monotonic()
        while queue and queue[0] < now - 3600:
            queue.popleft()
        if user_id in active or len(active) >= 2:
            return error("preview_capacity_exceeded", 429)
        if len(queue) >= 30:
            return error("preview_hourly_request_limit", 429)
        active.add(user_id)
        queue.append(now)
        return None

    async def model_permission(request, model):
        if authorize_model is None:
            return None
        token = request.headers.get("Authorization", "")[7:]
        try:
            failure = await authorize_model(token, model)
            return error(*failure) if failure else None
        except AuthenticationUnavailable:
            return error("token_balance_unavailable", 503)

    async def authorize(request):
        if os.getenv("VELIA_DESKTOP_API_ENABLED", "").lower() not in {"true", "1"}:
            return None, error("desktop_api_disabled", 503)
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        try:
            if not token:
                identity = None
            elif inspect.iscoroutinefunction(authenticate):
                identity = await authenticate(token)
            else:
                identity = await asyncio.to_thread(authenticate, token)
        except AuthenticationUnavailable:
            return None, error("authentication_unavailable", 503)
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
        catalog = [] if await model_permission(request, MODEL_ID) is not None else [
            {"id": MODEL_ID, "object": "model", "owned_by": "velia"}
        ]
        if flash_enabled():
            catalog.append({"id": FLASH_ID, "object": "model", "owned_by": "velia"})
        return web.json_response({"object": "list", "data": catalog}, headers={"Cache-Control": "no-store"})

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
            data = json.loads(body)
            if prepare_payload is not None:
                data = prepare_payload(request, data)
            if isinstance(data, dict) and data.get("model") == FLASH_ID and not flash_enabled():
                return error("flash_unavailable", 503)
            payload = validate_payload(data)
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            return error(str(exc) if str(exc) in {"unsupported_model", "invalid_messages", "text_only_preview",
                         "invalid_tools", "invalid_output_limit", "invalid_stream", "invalid_tool_choice",
                         "invalid_tool_calls", "invalid_tool_arguments"}
                         else "invalid_json", 400)
        is_flash = data["model"] == FLASH_ID
        failure = await model_permission(request, data["model"])
        if failure is not None:
            return failure
        key = os.getenv("VELIA_DESKTOP_FLASH_API_KEY" if is_flash else "KIMI_API_KEY", "").strip()
        if not key:
            return error("model_unavailable", 503)
        # Body reading yields: repeat admission immediately before reserving.
        if user_id in active or len(active) >= 2 or len(queue) >= 30:
            return error("preview_capacity_exceeded", 429)
        # The desktop route has its own bounded preview budget (30 calls/hour,
        # 4096 output tokens/call). It must gain commercial accounting before GA.
        failure = reserve(user_id)
        if failure is not None:
            return failure
        response = None
        try:
            timeout = ClientTimeout(total=360, sock_read=300) if is_flash else ClientTimeout(total=180, sock_read=90)
            async with ClientSession(timeout=timeout) as client:
                endpoint = flash_endpoint() if is_flash else os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1").rstrip("/")
                headers = {"Authorization": "Bearer " + key}
                if enrich_payload is not None:
                    payload = await enrich_payload(request, payload)
                    if isinstance(payload, web.StreamResponse):
                        return payload
                if is_flash:
                    await check_flash_context(client, endpoint, headers, payload)
                async with client.post(endpoint + ("/v1/chat/completions" if is_flash else "/chat/completions"), json=payload,
                                       headers=headers,
                                       allow_redirects=False) as upstream:
                    if upstream.status != 200:
                        return error("model_request_failed", 502)
                    if payload["stream"]:
                        if "text/event-stream" not in upstream.headers.get("Content-Type", ""):
                            return error("invalid_model_stream", 502)
                        response = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                            "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
                        await response.prepare(request)
                        stream = filter_stream(request, upstream.content) if filter_stream else upstream.content.iter_chunked(65536)
                        async for chunk in stream:
                            await response.write(chunk)
                        await response.write_eof()
                        return response
                    result = await upstream.json()
                    if not isinstance(result, dict) or not isinstance(result.get("choices"), list):
                        return error("invalid_model_response", 502)
                    result["model"] = data["model"]
                    return web.json_response(result, headers={"Cache-Control": "no-store"})
        except FlashContextTooLong:
            return error("flash_context_too_long", 400)
        except (TimeoutError, OSError, ClientError, ValueError):
            if response is not None and response.prepared:
                response.force_close()
                return response
            return error("model_connection_failed", 502)
        finally:
            active.discard(user_id)

    app.router.add_get("/desktop-api/v1/models", models)
    app.router.add_post("/desktop-api/v1/chat/completions", complete)
    return {"models": models, "complete": complete, "reserve": reserve,
            "release": lambda user_id: active.discard(str(user_id))}
