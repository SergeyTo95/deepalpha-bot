"""Bounded Z.ai overload handling shared by sync and async Quantum chat.

Only an explicit HTTP 429/1305 before the first completion starts is retried.
No network, stream, quota or authentication failure is replayed. Cooldowns are
process-local, scoped to endpoint/model/key, and never contain user messages.
An explicitly configured Groq reserve can handle pre-response Z.ai capacity
failures; no stream or tool continuation can trigger a provider switch.
"""
import asyncio
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager, contextmanager
import hashlib
import logging
import os
import random
import threading
import time

from velia_quantum_provider import (QuantumError, provider_error_details,
    read_provider_error, response_error, verify_zero_spend, verify_zero_spend_async, payload)

LOG = logging.getLogger(__name__)
HEADERS = {"Accept": "text/event-stream", "Accept-Encoding": "identity",
    "User-Agent": "VELIA-Quantum/1.0"}
_LOCK = threading.Lock()
_CIRCUITS = {}
COOLDOWN_SECONDS = 30


def _identity(config):
    return (os.getpid(), config.provider, config.base_url, config.model,
        hashlib.sha256(config.api_key.encode()).digest())


def check_overload(config):
    if config.provider != "zai":
        return
    with _LOCK:
        state = _CIRCUITS.get(_identity(config))
        if state and state[1] > time.monotonic():
            LOG.info("VELIA_QUANTUM_OVERLOAD_COOLDOWN active=true")
            error = QuantumError("quantum_overloaded", 503)
            error.before_response = True
            raise error


def _rejected(config, status, code):
    if config.provider != "zai" or status != 429 or code != "1305":
        return False
    with _LOCK:
        key = _identity(config)
        count, until = _CIRCUITS.get(key, (0, 0))
        now = time.monotonic()
        if until and until <= now:
            count = 0
        count += 1
        # Two actual overload responses open the circuit for this worker/key.
        until = max(until, now + COOLDOWN_SECONDS) if count >= 2 else 0
        if key not in _CIRCUITS and len(_CIRCUITS) >= 256:
            _CIRCUITS.pop(next(iter(_CIRCUITS)))
        _CIRCUITS[key] = (count, until)
        return not until


def _healthy(config):
    with _LOCK:
        _CIRCUITS.pop(_identity(config), None)


def _delay(response_headers):
    delay = random.uniform(1, 2)
    retry_after = response_headers.get("Retry-After")
    if retry_after is not None:
        try:
            seconds = float(retry_after)
        except (ValueError, TypeError):
            return None  # Date-form/unknown advice: do not retry early.
        if not 0 <= seconds <= 2:
            return None
        delay = max(delay, seconds)
    return delay


def sync_error_details(response):
    raw = bytearray()
    deadline = time.monotonic() + 3
    try:
        for chunk in response.iter_content(1):
            raw.extend(chunk)
            if len(raw) > 8192:
                return "oversized_error", "none"
            if time.monotonic() >= deadline:
                return "unreadable_error", "none"
        return provider_error_details(raw)
    except (OSError, ValueError, TypeError):
        return "unreadable_error", "none"


@contextmanager
def _completion(client, config, body, *, timeout, retry_overload=False):
    for attempt in range(2):
        check_overload(config)
        verify_zero_spend(client, config)
        with client.post(config.base_url + "/chat/completions", json=body,
                headers={**HEADERS, "Authorization": "Bearer " + config.api_key},
                timeout=timeout, stream=True, allow_redirects=False) as response:
            if response.status_code == 200:
                _healthy(config)
                yield response
                return
            code, reason = sync_error_details(response)
            LOG.warning("VELIA_QUANTUM_HTTP_REJECTED phase=answer status=%d provider_code=%s quota_reason=%s",
                response.status_code, code, reason)
            can_retry = _rejected(config, response.status_code, code)
            delay = _delay(response.headers) if retry_overload and attempt == 0 and can_retry else None
            if delay is None:
                error = response_error(response.status_code, provider_code=code, quota_reason=reason)
                error.before_response = response.status_code == 429 and code in {"1302", "1305"}
                raise error
        LOG.info("VELIA_QUANTUM_OVERLOAD_RETRY attempt=2")
        time.sleep(delay)  # Response closed before waiting; one retry maximum.


@asynccontextmanager
async def _completion_async(client, config, body, *, retry_overload=False):
    for attempt in range(2):
        check_overload(config)
        await verify_zero_spend_async(client, config)
        async with client.post(config.base_url + "/chat/completions", json=body,
                headers={**HEADERS, "Authorization": "Bearer " + config.api_key},
                allow_redirects=False) as response:
            if response.status == 200:
                _healthy(config)
                yield response
                return
            code, reason = await read_provider_error(response)
            LOG.warning("VELIA_QUANTUM_HTTP_REJECTED phase=tool_turn status=%d provider_code=%s quota_reason=%s",
                response.status, code, reason)
            can_retry = _rejected(config, response.status, code)
            delay = _delay(response.headers) if retry_overload and attempt == 0 and can_retry else None
            if delay is None:
                error = response_error(response.status, provider_code=code, quota_reason=reason)
                error.before_response = response.status == 429 and code in {"1302", "1305"}
                raise error
        LOG.info("VELIA_QUANTUM_OVERLOAD_RETRY attempt=2")
        await asyncio.sleep(delay)


def _backup_body(config, backup, body, exc):
    if (config.provider != "zai" or backup is None or backup.provider != "groq"
            or not getattr(exc, "before_response", False)
            or exc.code not in {"quantum_overloaded", "quantum_rate_limited"}):
        raise exc
    LOG.info("VELIA_QUANTUM_BACKUP from=zai to=groq reason=%s", exc.code)
    return payload(backup, body["messages"], stream=True,
        max_tokens=body.get("max_tokens"), tools=body.get("tools"), tool_choice=body.get("tool_choice"))


@contextmanager
def completion(client, config, body, *, timeout, retry_overload=False, backup=None, on_route=None):
    # Catch only entry failures: exceptions while consuming a successful stream
    # propagate to the caller and can never restart a turn on another provider.
    with ExitStack() as stack:
        try:
            response = stack.enter_context(_completion(client, config, body,
                timeout=timeout, retry_overload=retry_overload))
        except QuantumError as exc:
            converted = _backup_body(config, backup, body, exc)
            if on_route:
                on_route(backup)
            response = stack.enter_context(_completion(client, backup, converted, timeout=timeout))
        yield response


@asynccontextmanager
async def completion_async(client, config, body, *, retry_overload=False, backup=None, on_route=None):
    async with AsyncExitStack() as stack:
        try:
            response = await stack.enter_async_context(_completion_async(client, config, body,
                retry_overload=retry_overload))
        except QuantumError as exc:
            converted = _backup_body(config, backup, body, exc)
            if on_route:
                on_route(backup)
            response = await stack.enter_async_context(_completion_async(client, backup, converted))
        yield response
