import hashlib
import logging
import os
import re
import threading
import time
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)

_CONTEXT = threading.local()
_CASUAL_MESSAGE = re.compile(
    r"^(?:"
    r"привет(?:ик)?|здравствуй(?:те)?|ку[\s-]*ку|доброе\s+утро|"
    r"добрый\s+(?:день|вечер)|спасибо|благодарю|ок(?:ей)?|понял(?:а)?|"
    r"ясно|хорошо|ладно|да|нет|пока|до\s+свидания|как\s+дела|"
    r"кто\s+ты|что\s+ты\s+умеешь|"
    r"hi|hello|hey|thanks|thank\s+you|ok(?:ay)?|got\s+it|yes|no|bye|"
    r"good\s+(?:morning|afternoon|evening)|how\s+are\s+you|who\s+are\s+you|"
    r"merhaba|selam|teşekkür(?:ler|\s+ederim)?|tamam|evet|hayır|görüşürüz|"
    r"nasılsın|sen\s+kimsin"
    r")[\s.!?…,:;\-—–🙂😊😉👍👌❤️❤]*$",
    re.IGNORECASE,
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 1000) -> int:
    try:
        parsed = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def _stable_prompt_cache_key(conversation_id: str) -> str:
    value = f"velia-chat:{str(conversation_id or '').strip()}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_casual_message(message: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(message or "")).strip()
    return bool(normalized and len(normalized) <= 160 and _CASUAL_MESSAGE.fullmatch(normalized))


def _latest_request_user_message(request_id: str, user_id: Optional[int]) -> str:
    if not request_id or user_id is None:
        return ""
    try:
        from db.database import get_connection

        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT content
                FROM velia_messages
                WHERE request_id=%s AND user_id=%s AND role='user'
                  AND status='completed' AND deleted_at IS NULL
                ORDER BY created_at DESC, message_id DESC
                LIMIT 1
                """,
                (str(request_id), int(user_id)),
            )
            row = cursor.fetchone()
            if not row:
                return ""
            if isinstance(row, dict):
                return str(row.get("content") or "").strip()
            return str(row[0] or "").strip()
        finally:
            cursor.close()
            conn.close()
    except Exception as exc:
        logger.warning(
            "VELIA_REASONING_ROUTE_SKIPPED request_id=%s error=%s",
            str(request_id),
            exc.__class__.__name__,
        )
        return ""


def _selected_reasoning_effort(
    *,
    feature: str,
    request_id: str,
    user_id: Optional[int],
    default_effort: str,
) -> str:
    if feature != "velia_chat":
        return default_effort
    if not _env_bool("VELIA_CHAT_ADAPTIVE_REASONING_ENABLED", True):
        return default_effort
    message = _latest_request_user_message(request_id, user_id)
    return "low" if _is_casual_message(message) else default_effort


def _prepare_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    prepared = dict(payload)
    cache_key = str(getattr(_CONTEXT, "prompt_cache_key", "") or "")
    reasoning_effort = str(getattr(_CONTEXT, "reasoning_effort", "") or "")
    if cache_key and not prepared.get("prompt_cache_key"):
        prepared["prompt_cache_key"] = cache_key
    if reasoning_effort in {"low", "high", "max"}:
        prepared["reasoning_effort"] = reasoning_effort
    return prepared


class _ThreadLocalPooledRequests:
    def __init__(self, base_requests: Any):
        self._base_requests = base_requests
        self._sessions = threading.local()

    def _session(self):
        session = getattr(self._sessions, "session", None)
        if session is not None:
            return session
        session_factory = getattr(self._base_requests, "Session", None)
        if not callable(session_factory):
            return None
        session = session_factory()
        try:
            from requests.adapters import HTTPAdapter

            pool_size = _env_int("VELIA_CORE_HTTP_POOL_SIZE", 8, 2, 64)
            adapter = HTTPAdapter(
                pool_connections=pool_size,
                pool_maxsize=pool_size,
                max_retries=0,
                pool_block=False,
            )
            session.mount("https://", adapter)
            session.mount("http://", adapter)
        except Exception:
            pass
        self._sessions.session = session
        return session

    def post(self, *args, **kwargs):
        if "json" in kwargs:
            kwargs["json"] = _prepare_payload(kwargs.get("json"))
        session = self._session()
        if session is not None:
            return session.post(*args, **kwargs)
        return self._base_requests.post(*args, **kwargs)


def _install_kimi_transport_patch() -> None:
    from services import kimi_gateway

    if getattr(kimi_gateway, "_velia_latency_transport_patch_installed", False):
        return

    original_call_kimi = kimi_gateway.call_kimi
    original_requests = kimi_gateway.requests
    kimi_gateway.requests = _ThreadLocalPooledRequests(original_requests)

    def call_kimi_with_velia_latency_features(**kwargs):
        feature = str(kwargs.get("feature") or "")
        cycle_id = str(kwargs.get("cycle_id") or "")
        request_id = str(kwargs.get("request_id") or "")
        user_id = kwargs.get("user_id")
        previous_cache_key = getattr(_CONTEXT, "prompt_cache_key", None)
        previous_reasoning = getattr(_CONTEXT, "reasoning_effort", None)

        if feature == "velia_chat" and cycle_id and _env_bool(
            "VELIA_CHAT_PROMPT_CACHE_KEY_ENABLED",
            True,
        ):
            _CONTEXT.prompt_cache_key = _stable_prompt_cache_key(cycle_id)
        else:
            _CONTEXT.prompt_cache_key = ""

        default_effort = kimi_gateway.kimi_reasoning_effort()
        selected_effort = _selected_reasoning_effort(
            feature=feature,
            request_id=request_id,
            user_id=user_id,
            default_effort=default_effort,
        )
        _CONTEXT.reasoning_effort = selected_effort

        started = time.monotonic()
        try:
            result = original_call_kimi(**kwargs)
            duration_ms = int((time.monotonic() - started) * 1000)
            if isinstance(result, dict):
                result.setdefault("core_duration_ms", duration_ms)
                result.setdefault("reasoning_effort", selected_effort)
            logger.info(
                "VELIA_CORE_TIMING request_id=%s feature=%s duration_ms=%s reasoning_effort=%s ok=%s reason=%s",
                request_id,
                feature,
                duration_ms,
                selected_effort,
                bool(isinstance(result, dict) and result.get("ok")),
                str(result.get("reason") or "") if isinstance(result, dict) else "invalid_result",
            )
            return result
        finally:
            if previous_cache_key is None:
                try:
                    delattr(_CONTEXT, "prompt_cache_key")
                except AttributeError:
                    pass
            else:
                _CONTEXT.prompt_cache_key = previous_cache_key
            if previous_reasoning is None:
                try:
                    delattr(_CONTEXT, "reasoning_effort")
                except AttributeError:
                    pass
            else:
                _CONTEXT.reasoning_effort = previous_reasoning

    kimi_gateway.call_kimi = call_kimi_with_velia_latency_features
    kimi_gateway._velia_latency_transport_patch_installed = True
    logger.info("VELIA_CORE_LATENCY_TRANSPORT_PATCH_INSTALLED")


def install(chat_module: Any, routes_module: Any = None) -> None:
    if getattr(chat_module, "_velia_chat_latency_patch_installed", False):
        if routes_module is not None:
            routes_module.send_message = chat_module.send_message
        return

    _install_kimi_transport_patch()

    original_build_prompt = chat_module._build_prompt
    original_generate = chat_module.generate_velia_chat_result
    original_send_message = chat_module.send_message

    def build_prompt_with_timing(user_id: int, conversation_id: str) -> str:
        started = time.monotonic()
        prompt = original_build_prompt(user_id, conversation_id)
        logger.info(
            "VELIA_PROMPT_TIMING user_id=%s conversation_id=%s duration_ms=%s prompt_chars=%s",
            int(user_id),
            str(conversation_id),
            int((time.monotonic() - started) * 1000),
            len(str(prompt or "")),
        )
        return prompt

    def generate_with_timing(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: str = None,
    ) -> Dict[str, Any]:
        started = time.monotonic()
        result = original_generate(
            prompt,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        if isinstance(result, dict):
            result.setdefault("generation_duration_ms", duration_ms)
        logger.info(
            "VELIA_GENERATION_TIMING request_id=%s user_id=%s conversation_id=%s duration_ms=%s ok=%s reason=%s",
            str(request_id or ""),
            int(user_id),
            str(conversation_id),
            duration_ms,
            bool(isinstance(result, dict) and result.get("ok")),
            str(result.get("reason") or "") if isinstance(result, dict) else "invalid_result",
        )
        return result

    def send_message_with_timing(*args, **kwargs):
        started = time.monotonic()
        result = original_send_message(*args, **kwargs)
        duration_ms = int((time.monotonic() - started) * 1000)
        user_id = args[0] if args else kwargs.get("user_id")
        conversation_id = args[1] if len(args) > 1 else kwargs.get("conversation_id")
        logger.info(
            "VELIA_CHAT_TOTAL_TIMING user_id=%s conversation_id=%s duration_ms=%s ok=%s error=%s",
            user_id,
            conversation_id,
            duration_ms,
            bool(isinstance(result, dict) and result.get("ok")),
            str(result.get("error") or "") if isinstance(result, dict) else "invalid_result",
        )
        return result

    chat_module._build_prompt = build_prompt_with_timing
    chat_module.generate_velia_chat_result = generate_with_timing
    chat_module.send_message = send_message_with_timing
    if routes_module is not None:
        routes_module.send_message = send_message_with_timing
    chat_module._velia_chat_latency_patch_installed = True
    logger.info("VELIA_CHAT_LATENCY_PATCH_INSTALLED")
