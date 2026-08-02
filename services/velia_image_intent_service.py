import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Pattern


_MAX_TARGET_OFFSET = 120
_MAX_PROMPT_CHARS = 4000


@dataclass(frozen=True)
class ImageIntent:
    requested: bool
    prompt: str = ""


@dataclass(frozen=True)
class _CommandSpec:
    command: Pattern[str]
    target: Pattern[str]
    reject_before_target: Optional[Pattern[str]] = None


_RUSSIAN_SPEC = _CommandSpec(
    command=re.compile(
        r"^\s*(?:пожалуйста[,.]?\s+)?"
        r"(?:сгенерируй|создай|нарисуй|сделай)\s+"
        r"(?:мне\s+)?(?P<body>.+)$",
        re.IGNORECASE | re.DOTALL,
    ),
    target=re.compile(
        r"\b(?:картинк\w*|изображени\w*|фото(?:графи\w*)?|"
        r"постер\w*|обложк\w*)\b",
        re.IGNORECASE,
    ),
    reject_before_target=re.compile(
        r"\b(?:промпт|описание|инструкцию|инструкции|сценарий)\b",
        re.IGNORECASE,
    ),
)

_ENGLISH_SPEC = _CommandSpec(
    command=re.compile(
        r"^\s*(?:please\s+)?(?:generate|create|draw|make|render)\s+"
        r"(?:me\s+)?(?P<body>.+)$",
        re.IGNORECASE | re.DOTALL,
    ),
    target=re.compile(
        r"\b(?:image|picture|photo|poster|cover|artwork|illustration)s?\b",
        re.IGNORECASE,
    ),
    reject_before_target=re.compile(
        r"\b(?:prompt|description|instructions|caption|scenario)\b",
        re.IGNORECASE,
    ),
)

_TURKISH_COMMAND_FIRST_SPEC = _CommandSpec(
    command=re.compile(
        r"^\s*(?:lütfen\s+)?(?:oluştur|üret|çiz|hazırla)\s+"
        r"(?P<body>.+)$",
        re.IGNORECASE | re.DOTALL,
    ),
    target=re.compile(
        r"\b(?:görsel|resim|fotoğraf|poster|kapak|illüstrasyon)\w*\b",
        re.IGNORECASE,
    ),
    reject_before_target=re.compile(
        r"\b(?:prompt|açıklama|talimat|senaryo)\b",
        re.IGNORECASE,
    ),
)

_TURKISH_TARGET_FIRST = re.compile(
    r"^\s*(?:lütfen\s+)?(?P<body>.+?)\s+"
    r"(?:oluştur|üret|çiz|hazırla)\s*"
    r"(?:[:—–-]\s*)?(?P<tail>.*)$",
    re.IGNORECASE | re.DOTALL,
)

_TURKISH_TARGET = re.compile(
    r"\b(?:görsel|resim|fotoğraf|poster|kapak|illüstrasyon)\w*\b",
    re.IGNORECASE,
)

_TURKISH_REJECT = re.compile(
    r"\b(?:prompt|açıklama|talimat|senaryo)\b",
    re.IGNORECASE,
)

_COMMAND_SPECS = (
    _RUSSIAN_SPEC,
    _ENGLISH_SPEC,
    _TURKISH_COMMAND_FIRST_SPEC,
)


def _normalize(message: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(message or ""))
    return re.sub(r"\s+", " ", normalized).strip()


def _last_user_message(chat_prompt: str) -> str:
    matches = re.findall(
        r"(?:^|\n\n)USER:\s*(.*?)(?=\n\n(?:USER|ASSISTANT):|\Z)",
        str(chat_prompt or ""),
        flags=re.DOTALL,
    )
    return str(matches[-1] if matches else "").strip()


def _intent_from_body(body: str, spec: _CommandSpec) -> ImageIntent:
    target = spec.target.search(body)
    if not target or target.start() > _MAX_TARGET_OFFSET:
        return ImageIntent(False, "")

    before_target = body[: target.start()]
    if spec.reject_before_target and spec.reject_before_target.search(before_target):
        return ImageIntent(False, "")

    description_after_target = body[target.end() :].strip(" \t\r\n:—–-,.;")
    if not description_after_target:
        return ImageIntent(True, "")

    return ImageIntent(True, body[:_MAX_PROMPT_CHARS].strip())


def detect_image_intent(message: str) -> ImageIntent:
    normalized = _normalize(message)
    if not normalized:
        return ImageIntent(False, "")

    for spec in _COMMAND_SPECS:
        command_match = spec.command.match(normalized)
        if command_match:
            return _intent_from_body(command_match.group("body").strip(), spec)

    target_first = _TURKISH_TARGET_FIRST.match(normalized)
    if target_first:
        body = target_first.group("body").strip()
        tail = target_first.group("tail").strip()
        target = _TURKISH_TARGET.search(body)
        if target and target.start() <= _MAX_TARGET_OFFSET:
            if _TURKISH_REJECT.search(body[: target.start()]):
                return ImageIntent(False, "")
            if not tail:
                return ImageIntent(True, "")
            combined = f"{body}: {tail}".strip()
            return ImageIntent(True, combined[:_MAX_PROMPT_CHARS])

    return ImageIntent(False, "")


def image_intent_from_chat_prompt(chat_prompt: str) -> ImageIntent:
    return detect_image_intent(_last_user_message(chat_prompt))
