import logging
import re
from typing import Any, Dict, List, Optional

from db.database import get_connection


logger = logging.getLogger(__name__)

_CONVERSATION_MARKER = "\n\nConversation:\n"
_MAX_ACK_NOTE_CHARS = 280
_ROLE_PRIORITY = {
    "user": 0,
    "assistant": 1,
    "system": 2,
}
_MEMORY_NOTE_PATTERNS = (
    (
        "ru",
        re.compile(
            r"^\s*(?:пожалуйста[,:]?\s*)?(?:запомни|запиши\s+в\s+память|"
            r"сохрани(?:\s+это)?\s+в\s+памяти)\s*[:;,—\-]?\s*(?P<note>.+?)\s*$",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "en",
        re.compile(
            r"^\s*(?:please\s+)?(?:remember|save(?:\s+this)?\s+to\s+memory)"
            r"\s*[:;,—\-]?\s*(?P<note>.+?)\s*$",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "tr",
        re.compile(
            r"^\s*(?:lütfen\s+)?(?:hatırla|aklında\s+tut|hafızana\s+kaydet)"
            r"\s*[:;,—\-]?\s*(?P<note>.+?)\s*$",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)
_RU_PERSPECTIVE_REPLACEMENTS = {
    "моими": "твоими",
    "моего": "твоего",
    "моему": "твоему",
    "моих": "твоих",
    "моей": "твоей",
    "моим": "твоим",
    "моё": "твоё",
    "мое": "твое",
    "мои": "твои",
    "моя": "твоя",
    "мой": "твой",
}
_EN_PERSPECTIVE_REPLACEMENTS = {
    "mine": "yours",
    "my": "your",
}


def _compact_note(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(normalized) <= _MAX_ACK_NOTE_CHARS:
        return normalized
    shortened = normalized[: _MAX_ACK_NOTE_CHARS - 1].rstrip()
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0].rstrip()
    return shortened + "…"


def _match_case(source: str, replacement: str) -> str:
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _replace_words(text: str, replacements: Dict[str, str]) -> str:
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(word) for word in replacements) + r")\b",
        re.IGNORECASE,
    )

    def replace(match: re.Match) -> str:
        source = match.group(0)
        replacement = replacements[source.lower()]
        return _match_case(source, replacement)

    return pattern.sub(replace, text)


def _shift_note_perspective(note: str, language: str) -> str:
    shifted = str(note or "")
    if language == "ru":
        shifted = re.sub(
            r"\bя\s+сам\b",
            lambda match: _match_case(match.group(0), "ты сам"),
            shifted,
            flags=re.IGNORECASE,
        )
        shifted = re.sub(
            r"\bя\s+сама\b",
            lambda match: _match_case(match.group(0), "ты сама"),
            shifted,
            flags=re.IGNORECASE,
        )
        return _replace_words(shifted, _RU_PERSPECTIVE_REPLACEMENTS)
    if language == "en":
        return _replace_words(shifted, _EN_PERSPECTIVE_REPLACEMENTS)
    return shifted


def memory_note_ack(message: str) -> Optional[str]:
    text = str(message or "")
    for language, pattern in _MEMORY_NOTE_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        note = _compact_note(match.group("note"))
        if not note:
            return None
        note = _shift_note_perspective(note, language).rstrip(" .!?;:")
        if language == "ru":
            return f"Приняла: {note}."
        if language == "tr":
            return f"Not aldım: {note}."
        return f"Noted: {note}."
    return None


def _latest_completed_user_message(user_id: int, conversation_id: str) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT content
            FROM velia_messages
            WHERE conversation_id=%s AND user_id=%s
              AND role='user' AND status='completed' AND deleted_at IS NULL
            ORDER BY created_at DESC, message_id DESC
            LIMIT 1
            """,
            (str(conversation_id), int(user_id)),
        )
        row = cursor.fetchone()
        if row is None:
            return ""
        if isinstance(row, dict):
            return str(row.get("content") or "").strip()
        return str(row[0] if row else "").strip()
    finally:
        cursor.close()
        conn.close()


def _deterministic_transcript(
    velia_chat_service_module: Any,
    user_id: int,
    conversation_id: str,
) -> str:
    max_messages = velia_chat_service_module._env_int(
        "VELIA_CHAT_CONTEXT_MESSAGES",
        24,
        2,
        100,
    )
    max_chars = velia_chat_service_module._env_int(
        "VELIA_CHAT_CONTEXT_CHARS",
        24000,
        2000,
        120000,
    )
    conn = get_connection()
    cursor = velia_chat_service_module._dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT message_id, role, content, created_at
            FROM velia_messages
            WHERE conversation_id=%s AND user_id=%s
              AND status='completed' AND deleted_at IS NULL
              AND role IN ('user', 'assistant')
            ORDER BY created_at DESC,
                     CASE role
                         WHEN 'assistant' THEN 0
                         WHEN 'user' THEN 1
                         ELSE 2
                     END ASC,
                     message_id DESC
            LIMIT %s
            """,
            (str(conversation_id), int(user_id), max_messages),
        )
        rows = list(reversed(cursor.fetchall() or []))
    finally:
        cursor.close()
        conn.close()

    transcript: List[str] = []
    used_chars = 0
    for row in reversed(rows):
        role = str(
            velia_chat_service_module._row_value(row, "role", 1, "user")
        )
        content = str(
            velia_chat_service_module._row_value(row, "content", 2, "")
        ).strip()
        if not content:
            continue
        label = "USER" if role == "user" else "ASSISTANT"
        chunk = f"{label}: {content}"
        if transcript and used_chars + len(chunk) > max_chars:
            break
        transcript.append(chunk)
        used_chars += len(chunk)
    transcript.reverse()
    return "\n\n".join(transcript)


def _chronological_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(messages) < 2:
        return messages
    if any(not str(message.get("created_at") or "").strip() for message in messages):
        return messages
    return sorted(
        messages,
        key=lambda message: (
            str(message.get("created_at") or ""),
            _ROLE_PRIORITY.get(str(message.get("role") or ""), 9),
            str(message.get("id") or ""),
        ),
    )


def install(
    velia_chat_service_module: Any,
    velia_mobile_routes_module: Any = None,
) -> None:
    already_installed = bool(
        getattr(
            velia_chat_service_module,
            "_velia_conversation_quality_patch_installed",
            False,
        )
    )

    if not already_installed:
        original_build_prompt = velia_chat_service_module._build_prompt
        original_list_messages = velia_chat_service_module.list_messages
        original_generate = velia_chat_service_module.generate_velia_chat_result

        def build_prompt_with_deterministic_turns(
            user_id: int,
            conversation_id: str,
        ) -> str:
            prompt = original_build_prompt(user_id, conversation_id)
            if _CONVERSATION_MARKER not in prompt:
                return prompt
            prefix = prompt.split(_CONVERSATION_MARKER, 1)[0]
            transcript = _deterministic_transcript(
                velia_chat_service_module,
                int(user_id),
                str(conversation_id),
            )
            return prefix + _CONVERSATION_MARKER + transcript

        def list_messages_chronologically(
            user_id: int,
            conversation_id: str,
            *,
            limit: int = 100,
        ):
            result = original_list_messages(
                user_id,
                conversation_id,
                limit=limit,
            )
            if result is None:
                return None
            return _chronological_messages(result)

        def generate_with_memory_note_ack(
            prompt: str,
            *,
            user_id: int,
            conversation_id: str,
            request_id: str = None,
        ) -> Dict[str, Any]:
            latest_message = _latest_completed_user_message(
                int(user_id),
                str(conversation_id),
            )
            acknowledgement = memory_note_ack(latest_message)
            if acknowledgement:
                return {
                    "ok": True,
                    "text": acknowledgement,
                    "request_id": str(request_id or ""),
                    "provider": "velyon_core",
                    "model": "memory_note",
                    "finish_reason": "memory_note_ack",
                    "fallback_used": False,
                    "estimated_cost_usd": 0.0,
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "cached_input_tokens": 0,
                        "reasoning_tokens": 0,
                    },
                }
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        velia_chat_service_module._build_prompt = build_prompt_with_deterministic_turns
        velia_chat_service_module.list_messages = list_messages_chronologically
        velia_chat_service_module.generate_velia_chat_result = generate_with_memory_note_ack
        velia_chat_service_module._velia_conversation_quality_patch_installed = True
        logger.info("VELIA_CONVERSATION_QUALITY_PATCH_INSTALLED")

    if velia_mobile_routes_module is not None:
        velia_mobile_routes_module.list_messages = velia_chat_service_module.list_messages
