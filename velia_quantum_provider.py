"""Quantum's provider boundary, shared by account chat and the desktop gateway.

Clients use velia-quantum / chat_mode=quantum. Only operator configuration knows
the upstream model. Experiential is deliberately free-only; there is no retry,
provider fallback, or use of another model's credentials.
"""
from dataclasses import dataclass, field
import json
import os
import re
from urllib.parse import urlsplit

MODEL_ID = "velia-quantum"
PROVIDER_ID = "velia-quantum"
FREE_MODEL = "glm-5.3-uncensored:free"
EXPERIENTIAL_URL = "https://api.experientiallabs.ai/v1"
ERRORS = frozenset({"quantum_unavailable", "quantum_quota_exhausted", "quantum_access_required",
    "quantum_busy", "quantum_timeout", "quantum_provider_error",
    "quantum_invalid_response", "quantum_context_too_long",
    "quantum_attachments_unsupported", "quantum_daily_user_limit_exceeded",
    "quantum_daily_global_limit_exceeded"})


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
    # A typo must never turn the free promotion into a paid request, or send
    # the Experiential key to a different endpoint.
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


def response_error(status):
    if status == 402:
        return QuantumError("quantum_access_required", 503)
    if status == 429:
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
