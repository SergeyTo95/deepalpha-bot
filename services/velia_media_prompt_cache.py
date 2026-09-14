"""Small tenant-scoped cache for successful prompt rewrites, never media or lyrics."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError
from typing import Callable

_LOCK = threading.Lock()
_CACHE: OrderedDict[str, tuple[float, str]] = OrderedDict()
_PENDING: dict[str, Future] = {}
_MAX_ENTRIES = 256
_TTL_SECONDS = 600


def reuse_media_text(
    *, user_id: int, instruction: str, producer: Callable[[], str], minimum_chars: int,
) -> str:
    if (
        int(user_id) <= 0
        or os.getenv("VELIA_MEDIA_PROMPT_CACHE_ENABLED", "true").strip().lower() in {"0", "false", "off"}
    ):
        return producer()
    # Include provider configuration in the hash so switching our own model or
    # credentials cannot reuse output produced under an earlier configuration.
    config = {key: value for key, value in os.environ.items() if key.startswith(("LLM_", "KIMI_", "GEMINI_"))}
    key = hashlib.sha256(json.dumps(
        [int(user_id), instruction, config], sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    now = time.monotonic()
    with _LOCK:
        for expired in [name for name, (deadline, _) in _CACHE.items() if deadline <= now]:
            _CACHE.pop(expired, None)
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached[1]
        pending = _PENDING.get(key)
        owner = pending is None
        if owner:
            if len(_PENDING) >= _MAX_ENTRIES:
                return ""
            pending = Future()
            _PENDING[key] = pending

    if not owner:
        try:
            return pending.result(timeout=45)
        except TimeoutError:
            # Caller can use the original request; do not launch a duplicate LLM call.
            return ""

    try:
        value = str(producer() or "")
        if minimum_chars <= len(value) <= 12000:
            with _LOCK:
                _CACHE[key] = (time.monotonic() + _TTL_SECONDS, value)
                _CACHE.move_to_end(key)
                while len(_CACHE) > _MAX_ENTRIES:
                    _CACHE.popitem(last=False)
        pending.set_result(value)
        return value
    except BaseException as exc:
        pending.set_exception(exc)
        raise
    finally:
        with _LOCK:
            _PENDING.pop(key, None)
