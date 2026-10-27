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
EXPERIENTIAL_URL = "https://api.experientiallabs.ai/v1"
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
        return "GLM-5.3 через Experiential" if self.provider == "experiential" else "Собственная модель VELIA"


def configuration():
    if os.getenv("VELIA_QUANTUM_ENABLED", "").strip().lower() not in {"true", "1", "yes", "on"}:
        return None
    provider = os.getenv("VELIA_QUANTUM_PROVIDER", "experiential").strip().lower()
    if provider not in {"experiential", "self_hosted"}:
        return None
    external = provider == "experiential"
    base = os.getenv("VELIA_QUANTUM_BASE_URL", EXPERIENTIAL_URL if external else "").strip().rstrip("/")
    model = os.getenv("VELIA_QUANTUM_MODEL", FREE_MODEL if external else "").strip()
    key = os.getenv("VELIA_QUANTUM_API_KEY", "").strip()
    if external and not key:
        key = os.getenv("EXPLABS_API_KEY", "").strip()
    try:
        url = urlsplit(base)
        private = (url.hostname or "").endswith(".railway.internal") or url.hostname in {"localhost", "127.0.0.1", "::1"}
        valid = (url.scheme == "https" or url.scheme == "http" and private)
        valid = (valid and bool(url.hostname) and not url.username and not url.password
            and not url.query and not url.fragment and url.path in {"", "/v1"}
            and (url.port is None or 1 <= url.port <= 65535))
    except ValueError:
        return None
    if (not valid or not key or any(c in key for c in "\r\n")
            or not re.fullmatch(r"[A-Za-z0-9_./:+-]{1,200}", model)):
        return None
    # Pin the documented canonical ID. Every Experiential transport must verify
    # the presented key's hard $0 spending cap before any completion request.
    if external and (base != EXPERIENTIAL_URL or model != FREE_MODEL):
        return None
    if not external and url.hostname == "api.experientiallabs.ai":
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
