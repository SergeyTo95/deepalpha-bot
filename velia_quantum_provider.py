"""Quantum's provider boundary, shared by account chat and the desktop gateway.

Clients use velia-quantum / chat_mode=quantum. Only operator configuration knows
the upstream model. Experiential requires a verified zero-spend key; there is no retry,
provider fallback, or use of another model's credentials.
"""
from dataclasses import dataclass, field
from decimal import Decimal
import asyncio
import json
import logging
import os
import re
import time
from urllib.parse import urlsplit

MODEL_ID = "velia-quantum"
PROVIDER_ID = "velia-quantum"
# Historical export name: the $0 key cap enforces free access, not a suffix.
FREE_MODEL = "glm-5.3-uncensored"
FREE_CORE_MODEL = "deepseek-v4.1-flash"
FREE_CORE_ROUTE = FREE_CORE_MODEL + ":free"
EXPERIENTIAL_MODELS = {
    FREE_MODEL: "GLM-5.3 через Experiential",
    FREE_CORE_MODEL: "DeepSeek V4.1 Flash через Experiential",
    FREE_CORE_ROUTE: "DeepSeek V4.1 Flash через Experiential",
}
EXPERIENTIAL_URL = "https://api.experientiallabs.ai/v1"
ZAI_URL = "https://api.z.ai/api/paas/v4"
ZAI_FREE_MODEL = "glm-4.7-flash"
KEY_INFO_URL = "https://api.experientiallabs.ai/api/v1/key"
KEY_INFO_MAX_BYTES = 8192
LOG = logging.getLogger(__name__)
ERRORS = frozenset({"quantum_unavailable", "quantum_quota_exhausted", "quantum_access_required",
    "quantum_busy", "quantum_timeout", "quantum_provider_error",
    "quantum_invalid_response", "quantum_context_too_long",
    "quantum_attachments_unsupported", "quantum_daily_user_limit_exceeded",
    "quantum_daily_global_limit_exceeded", "quantum_free_guard_required",
    "quantum_spend_cap_reached"})
PROVIDER_ERROR_CODES = frozenset({
    "invalid_key", "model_not_granted", "model_location_not_supported",
    "insufficient_quota", "rate_limit_exceeded", "free_limit_reached",
    "free_tier_requires_payment", "model_requires_payment", "model_requires_purchase",
    "card_required", "email_unverified", "org_under_review", "unavailable_route",
    "unsupported_capability", "unsupported_parameter", "invalid_parameter",
    "capability_requires_payment", "capability_requires_purchase", "provider_internal",
    "all_routes_failed", "gateway_draining", "deadline_exceeded", "internal_error",
})
PROVIDER_QUOTA_REASONS = frozenset({
    "key_daily_cap", "insufficient_credits", "free_limit_reached", "promo_byok_only",
    "org_rate_limit", "org_token_rate_limit", "org_token_rate_limit_hour",
    "org_token_rate_limit_day",
})


def provider_error_details(raw):
    """Return fixed diagnostic tokens only, never provider prose or account data."""
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result

    def finite(_value):
        raise ValueError("nonfinite")

    try:
        if not isinstance(raw, (bytes, bytearray)):
            return "unreadable_error", "none"
        if len(raw) > 8192:
            return "oversized_error", "none"
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite)
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        if not isinstance(code, str) or code not in PROVIDER_ERROR_CODES:
            return "unknown_error", "none"
        reason = "none"
        message = error.get("message")
        if code in {"insufficient_quota", "rate_limit_exceeded", "free_limit_reached"} and isinstance(message, str):
            prefix = re.match(r"([a-z_]+)(?=$|[\s:])", message.lstrip())
            if prefix and prefix[1] in PROVIDER_QUOTA_REASONS:
                reason = prefix[1]
        return code, reason
    except (ValueError, TypeError, RecursionError, OverflowError):
        return "unreadable_error", "none"


async def read_provider_error(response):
    """Bounded diagnostics shared by planning and direct Quantum transport."""
    async def read():
        raw = bytearray()
        async for chunk in response.content.iter_chunked(2048):
            raw.extend(chunk)
            if len(raw) > 8192:
                return "oversized_error", "none"
        return provider_error_details(raw)
    try:
        return await asyncio.wait_for(read(), timeout=3)
    except Exception:
        return "unreadable_error", "none"


def bounded_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        value = default
    return min(maximum, max(minimum, value))


@dataclass(frozen=True)
class QuantumConfig:
    provider: str
    base_url: str
    model: str
    api_key: str = field(repr=False)
    max_tokens: int = 8192

    @property
    def source(self):
        if self.provider == "zai":
            return "GLM-4.7-Flash через Z.ai"
        return EXPERIENTIAL_MODELS.get(self.model, "Quantum через Experiential") if self.provider == "experiential" else "Собственная модель VELIA"


def configuration():
    if os.getenv("VELIA_QUANTUM_ENABLED", "").strip().lower() not in {"true", "1", "yes", "on"}:
        return None
    provider = os.getenv("VELIA_QUANTUM_PROVIDER", "experiential").strip().lower()
    if provider not in {"experiential", "zai", "self_hosted"}:
        return None
    external = provider == "experiential"
    base = os.getenv("VELIA_QUANTUM_BASE_URL", ZAI_URL if provider == "zai" else EXPERIENTIAL_URL if external else "").strip().rstrip("/")
    model = os.getenv("VELIA_QUANTUM_MODEL", ZAI_FREE_MODEL if provider == "zai" else FREE_MODEL if external else "").strip()
    key = os.getenv("VELIA_QUANTUM_API_KEY", "").strip()
    if external and not key:
        key = os.getenv("EXPLABS_API_KEY", "").strip()
    try:
        url = urlsplit(base)
        private = (url.hostname or "").endswith(".railway.internal") or url.hostname in {"localhost", "127.0.0.1", "::1"}
        valid = (url.scheme == "https" or url.scheme == "http" and private)
        valid = (valid and bool(url.hostname) and not url.username and not url.password
            and not url.query and not url.fragment and url.path in ({"/api/paas/v4"} if provider == "zai" else {"", "/v1"})
            and (url.port is None or 1 <= url.port <= 65535))
    except ValueError:
        return None
    if (not valid or not key or any(c in key for c in "\r\n")
            or not re.fullmatch(r"[A-Za-z0-9_./:+-]{1,200}", model)):
        return None
    # Only supported IDs and the documented free-only alias. Every transport must verify
    # the presented key's hard $0 spending cap before any completion request.
    if external and (base != EXPERIENTIAL_URL or model not in EXPERIENTIAL_MODELS):
        return None
    if provider == "zai" and (base != ZAI_URL or model != ZAI_FREE_MODEL or "${{" in key):
        return None
    if provider == "self_hosted" and url.hostname in {"api.experientiallabs.ai", "api.z.ai"}:
        return None
    return QuantumConfig(provider, base, model, key,
        bounded_int("VELIA_QUANTUM_MAX_OUTPUT_TOKENS", 8192, 256, 8192))


def available():
    return configuration() is not None


class QuantumError(Exception):
    def __init__(self, code, status=502):
        self.code, self.status = code, status
        super().__init__(code)


def require_zero_spend_key(raw):
    """Only an explicit numeric zero cap is safe; null is unlimited, not zero."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_key')
            result[key] = value
        return result

    def constant(_value):
        raise ValueError('nonfinite')

    try:
        if not isinstance(raw, (bytes, bytearray)) or len(raw) > KEY_INFO_MAX_BYTES:
            raise ValueError('invalid_key_metadata')
        body = json.loads(raw.decode('utf-8'), parse_float=Decimal,
            parse_constant=constant, object_pairs_hook=pairs)
        cap = body['data']['limit']
        if isinstance(cap, bool) or not isinstance(cap, (int, Decimal)) or cap != 0:
            raise ValueError('zero_spend_cap_required')
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise QuantumError('quantum_free_guard_required', 503) from None


async def verify_zero_spend_async(client, config):
    if config.provider == 'zai':
        require_free_zai(config)
        return
    if config.provider != 'experiential':
        return
    status = 0
    async def read():
        nonlocal status
        async with client.get(KEY_INFO_URL, headers={'Authorization':'Bearer '+config.api_key,'Cache-Control':'no-cache'},
                allow_redirects=False) as response:
            status = response.status
            if status != 200:
                raise QuantumError('quantum_free_guard_required', 503)
            raw = bytearray()
            async for chunk in response.content.iter_chunked(2048):
                raw.extend(chunk)
                if len(raw) > KEY_INFO_MAX_BYTES:
                    raise QuantumError('quantum_free_guard_required', 503)
            require_zero_spend_key(raw)
    try:
        await asyncio.wait_for(read(), timeout=10)
    except Exception:
        LOG.warning('VELIA_QUANTUM_FREE_GUARD ok=false status=%d', status)
        raise QuantumError('quantum_free_guard_required', 503) from None
    LOG.info('VELIA_QUANTUM_FREE_GUARD ok=true status=%d', status)


def verify_zero_spend(client, config):
    if config.provider == 'zai':
        require_free_zai(config)
        return
    if config.provider != 'experiential':
        return
    status = 0
    deadline = time.monotonic() + 10
    try:
        with client.get(KEY_INFO_URL, headers={'Authorization':'Bearer '+config.api_key,'Cache-Control':'no-cache'},
                timeout=(3, 7), stream=True, allow_redirects=False) as response:
            status = response.status_code
            if status != 200:
                raise QuantumError('quantum_free_guard_required', 503)
            raw = bytearray()
            for chunk in response.iter_content(1):
                raw.extend(chunk)
                if len(raw) > KEY_INFO_MAX_BYTES or time.monotonic() >= deadline:
                    raise QuantumError('quantum_free_guard_required', 503)
            require_zero_spend_key(raw)
    except Exception:
        LOG.warning('VELIA_QUANTUM_FREE_GUARD ok=false status=%d', status)
        raise QuantumError('quantum_free_guard_required', 503) from None
    LOG.info('VELIA_QUANTUM_FREE_GUARD ok=true status=%d', status)


def require_free_zai(config):
    """Official zero-priced model only; never send Z.ai keys to an aggregator.

    Z.ai does not expose Experiential's key-cap metadata. This guard pins the
    published free model and endpoint, not an inferred credit balance.
    """
    if config.base_url != ZAI_URL or config.model != ZAI_FREE_MODEL:
        raise QuantumError('quantum_free_guard_required', 503)


def response_error(status, *, provider_code=None, quota_reason=None):
    if status == 402:
        return QuantumError("quantum_access_required", 503)
    if status == 429:
        if provider_code == "insufficient_quota" and quota_reason == "key_daily_cap":
            # A monetary cap can reject the very first request at $0. It does
            # not prove that the user consumed a daily message allowance.
            return QuantumError("quantum_spend_cap_reached", 503)
        return QuantumError("quantum_quota_exhausted", 429)
    if status in {401, 403}:
        return QuantumError("quantum_unavailable", 503)
    return QuantumError("quantum_provider_error")


def payload(config, messages, *, stream=False, max_tokens=None, tools=None, tool_choice=None):
    if sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) > bounded_int(
            "VELIA_QUANTUM_MAX_CONTEXT_CHARS", 96000, 12000, 256000):
        raise QuantumError("quantum_context_too_long", 400)
    result = {"model": config.model, "messages": messages, "stream": stream,
        "max_tokens": min(config.max_tokens, max_tokens or config.max_tokens)}
    if config.provider == "experiential":
        result["reasoning_effort"] = "low"
        result["gateway"] = {"retry":{"max_attempts_per_route":1,"max_total_attempts":1},
            "routing":{"allow_fallbacks":False}}
    if config.provider == "zai":
        require_free_zai(config)
        # Avoid hidden reasoning latency, including in the voice planner.
        result["thinking"] = {"type": "disabled"}
        if tools and any(t.get("type") != "function" for t in tools):
            raise QuantumError("quantum_free_guard_required", 503)
        # Z.ai documents only auto. The caller still strictly validates the
        # required function result; a plain-text answer fails closed.
        if tool_choice not in (None, "auto", "required"):
            raise QuantumError("quantum_invalid_response")
        if tool_choice == "required":
            tool_choice = "auto"
    if tools:
        result["tools"] = tools
    if tool_choice is not None:
        result["tool_choice"] = tool_choice
    return result


def public_result(value):
    """Never expose provider metadata or separate reasoning fields to clients."""
    if not isinstance(value, dict) or value.get("error"):
        raise QuantumError("quantum_invalid_response")
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise QuantumError("quantum_invalid_response")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise QuantumError("quantum_invalid_response")
    safe = {"role": "assistant", "content": message.get("content") if isinstance(message.get("content"), str) else ""}
    if isinstance(message.get("tool_calls"), list):
        safe["tool_calls"] = message["tool_calls"]
    if not safe["content"].strip() and not safe.get("tool_calls"):
        raise QuantumError("quantum_invalid_response")
    return {"model": MODEL_ID, "choices": [{"index": 0, "message": safe,
        "finish_reason": choice.get("finish_reason") if choice.get("finish_reason") in {"stop", "length", "tool_calls", "content_filter"} else None}]}


class StreamDecoder:
    """Bounded SSE decoder; accepts split UTF-8/CRLF and requires completion."""
    def __init__(self):
        self.pending = b""
        self.total = 0
        self.done = False
        self.answer = False
        self.finish_reason = None
        self.usage = {}

    def feed(self, chunk):
        self.total += len(chunk)
        if self.total > 4 * 1024 * 1024:
            raise QuantumError("quantum_invalid_response")
        self.pending += chunk
        events = []
        while True:
            boundary = re.search(br"\r?\n\r?\n", self.pending)
            if boundary is None:
                break
            frame, self.pending = self.pending[:boundary.start()], self.pending[boundary.end():]
            if len(frame) > 256 * 1024:
                raise QuantumError("quantum_invalid_response")
            data = b"\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith(b"data:"))
            if not data:
                continue
            if data == b"[DONE]":
                self.done = True
                continue
            if self.done:
                raise QuantumError("quantum_invalid_response")
            try:
                value = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                raise QuantumError("quantum_invalid_response") from None
            if not isinstance(value, dict) or value.get("error"):
                raise QuantumError("quantum_provider_error")
            if isinstance(value.get("usage"), dict):
                self.usage = {k: v for k, v in value["usage"].items()
                    if k in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(v) is int and v >= 0}
            choices = value.get("choices", [])
            if not isinstance(choices, list):
                raise QuantumError("quantum_invalid_response")
            if not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict) or not isinstance(choice.get("delta", {}), dict):
                raise QuantumError("quantum_invalid_response")
            delta = choice.get("delta", {})
            safe = {k: v for k, v in delta.items()
                if k == "content" and isinstance(v, str) or k == "tool_calls" and isinstance(v, list)}
            if safe.get("content", "").strip() or safe.get("tool_calls"):
                self.answer = True
            reason = choice.get("finish_reason")
            if reason in {"stop", "length", "tool_calls", "content_filter"}:
                self.finish_reason = reason
            else:
                reason = None
            events.append({"model": MODEL_ID, "choices": [{"index": 0, "delta": safe, "finish_reason": reason}]})
        if len(self.pending) > 256 * 1024:
            raise QuantumError("quantum_invalid_response")
        return events

    def finish(self):
        if self.pending.strip() or not self.done or not self.answer or not self.finish_reason:
            raise QuantumError("quantum_invalid_response")


async def public_stream(source):
    decoder = StreamDecoder()
    async for chunk in source.iter_chunked(65536):
        for event in decoder.feed(chunk):
            yield ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
    decoder.finish()
    yield b"data: [DONE]\n\n"


class PublicContent:
    def __init__(self, source):
        self.source = source

    def iter_chunked(self, size):
        return public_stream(self.source)


async def read_result(source):
    body = bytearray()
    async for chunk in source.iter_chunked(65536):
        body.extend(chunk)
        if len(body) > 4 * 1024 * 1024:
            raise QuantumError("quantum_invalid_response")
    try:
        return public_result(json.loads(body))
    except (ValueError, UnicodeDecodeError):
        raise QuantumError("quantum_invalid_response") from None


def require_free_usage(value):
    """Live acceptance needs explicit metered zero, never an omitted estimate."""
    usage = value.get("usage") if isinstance(value, dict) else None
    if not isinstance(usage, dict) or usage.get("is_byok") is not False:
        raise QuantumError("quantum_free_guard_required", 503)
    cost = usage.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float, Decimal)) or cost != 0:
        raise QuantumError("quantum_free_guard_required", 503)
    total = usage.get("total_tokens")
    if type(total) is not int or total <= 0:
        raise QuantumError("quantum_invalid_response")


async def probe_free_core():
    """Two bounded synthetic calls using the exact service key; no fallback.

    Run explicitly for a model migration, not as a recurring availability gate.
    Pins Z.ai's published free model, or verifies Experiential's hard $0 key
    cap before each call. Records only safe structural evidence. Z.ai usage
    does not report a monetary cost, so never claim a metered zero for it.
    """
    from aiohttp import ClientSession, ClientTimeout, DummyCookieJar
    config = configuration()
    if (config is None or not (config.provider == "zai" or config.provider == "experiential"
            and config.model in {FREE_CORE_MODEL, FREE_CORE_ROUTE} and config.base_url == EXPERIENTIAL_URL)):
        raise QuantumError("quantum_unavailable", 503)
    tool = {"type": "function", "function": {"name": "report_sum",
        "description": "Report the sum requested by the user.",
        "parameters": {"type": "object", "properties": {"sum": {"type": "integer"}},
            "required": ["sum"], "additionalProperties": False}}}
    report = {"model": config.model, "ok": False, "calls": 0,
        "tool_call": False, "stream": False}
    if config.provider == "experiential":
        report["reported_cost_usd"] = 0
    else:
        report["pricing_basis"] = "zai_published_free_model"
    async with ClientSession(timeout=ClientTimeout(total=60, connect=5, sock_read=50),
            cookie_jar=DummyCookieJar(), trust_env=False) as client:
        for stream in (False, True):
            await verify_zero_spend_async(client, config)
            messages = [{"role": "system", "content": "You are Velia, a female multilingual assistant."},
                {"role": "user", "content": "Вызови report_sum для суммы 7 + 8." if not stream
                    else "Ответь кратко на русском, English and Türkçe: ты готова помочь?"}]
            body = payload(config, messages, stream=stream, max_tokens=1024,
                tools=None if stream else [tool], tool_choice=None if stream else "required")
            async with client.post(config.base_url + "/chat/completions", json=body,
                    headers={"Authorization": "Bearer " + config.api_key}, allow_redirects=False) as response:
                if response.status != 200:
                    code, reason = await read_provider_error(response)
                    print("VELIA_QUANTUM_FREE_PROBE_REJECTED " + json.dumps({
                        "status": response.status, "provider_code": code, "quota_reason": reason}), flush=True)
                    raise response_error(response.status, provider_code=code, quota_reason=reason)
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > 256 * 1024:
                        raise QuantumError("quantum_invalid_response")
                if stream:
                    if "text/event-stream" not in response.headers.get("Content-Type", ""):
                        raise QuantumError("quantum_invalid_response")
                    decoder = StreamDecoder()
                    events = decoder.feed(bytes(raw))
                    decoder.finish()
                    if decoder.finish_reason != "stop" or not any(
                            e["choices"][0]["delta"].get("content", "").strip() for e in events):
                        raise QuantumError("quantum_invalid_response")
                    usage_frames = []
                    for frame in re.split(br"\r?\n\r?\n", bytes(raw)):
                        data = b"\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith(b"data:"))
                        if not data or data == b"[DONE]":
                            continue
                        value = json.loads(data, parse_float=Decimal)
                        if isinstance(value, dict) and isinstance(value.get("usage"), dict):
                            if config.provider == "experiential":
                                require_free_usage(value)
                            usage_frames.append(value)
                    if config.provider == "experiential" and not usage_frames:
                        raise QuantumError("quantum_free_guard_required", 503)
                    report["stream"] = True
                else:
                    value = json.loads(raw, parse_float=Decimal)
                    if config.provider == "experiential":
                        require_free_usage(value)
                    choice = value["choices"][0]
                    calls = choice["message"]["tool_calls"]
                    valid_finish = {"tool_calls", "stop"} if config.provider == "zai" else {"tool_calls"}
                    if choice.get("finish_reason") not in valid_finish or len(calls) != 1:
                        raise QuantumError("quantum_invalid_response")
                    function = calls[0]["function"]
                    args = function["arguments"]
                    args = json.loads(args) if isinstance(args, str) else args
                    if function.get("name") != "report_sum" or args != {"sum": 15}:
                        raise QuantumError("quantum_invalid_response")
                    report["tool_call"] = True
                report["calls"] += 1
    report["ok"] = True
    print("VELIA_QUANTUM_FREE_ACCEPTED " + json.dumps(report), flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(probe_free_core())
    except Exception as exc:
        code = exc.code if isinstance(exc, QuantumError) else "quantum_invalid_response"
        print("VELIA_QUANTUM_FREE_PROBE_FAILED " + code, flush=True)
        raise SystemExit(1) from None
