import re
import unicodedata
from dataclasses import dataclass
from typing import Pattern


_MAX_TARGET_OFFSET = 160
_MAX_PROMPT_CHARS = 4000
_USER_TURN_RE = re.compile(
    r"^USER:\s*(.*?)(?=\n\n(?:USER|ASSISTANT):|\Z)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)

_RUSSIAN_SOCIAL_PREFIX = (
    r"(?:(?:привет(?:ик)?|здравствуй(?:те)?|доброе\s+утро|"
    r"добрый\s+(?:день|вечер)|велия|слушай)\s*[,.!?:—–-]*\s*){0,2}"
)
_ENGLISH_SOCIAL_PREFIX = (
    r"(?:(?:hi|hello|hey|good\s+(?:morning|afternoon|evening)|velia)"
    r"\s*[,.!?:—–-]*\s*){0,2}"
)
_TURKISH_SOCIAL_PREFIX = (
    r"(?:(?:merhaba|selam|günaydın|iyi\s+akşamlar|velia)"
    r"\s*[,.!?:—–-]*\s*){0,2}"
)


@dataclass(frozen=True)
class ImageIntent:
    requested: bool
    prompt: str = ""


@dataclass(frozen=True)
class _CommandSpec:
    command: Pattern[str]
    target: Pattern[str]
    reject: Pattern[str]
    strong_verbs: Pattern[str]
    visual_cues: Pattern[str]


_RUSSIAN_SPEC = _CommandSpec(
    command=re.compile(
        rf"^\s*{_RUSSIAN_SOCIAL_PREFIX}(?:пожалуйста[,.]?\s*)?"
        r"(?:(?:можешь(?:\s+ли)?|сможешь|давай|прошу|хочу(?:,?\s+чтобы\s+ты)?|нужно)\s+)?"
        r"(?:мне\s+)?"
        r"(?P<verb>сгенерируй|сгенерировать|сгенерируем|сгенерировал|"
        r"создай|создать|создадим|создал|"
        r"нарисуй|нарисовать|нарисуем|нарисовал|"
        r"сделай|сделать|сделаем|сделал)\s+"
        r"(?:мне\s+)?(?P<body>.+)$",
        re.IGNORECASE | re.DOTALL,
    ),
    target=re.compile(
        r"\b(?:картинк\w*|изображени\w*|рисунк\w*|фото(?:графи\w*)?|"
        r"постер\w*|обложк\w*|иллюстраци\w*|арт\w*)\b",
        re.IGNORECASE,
    ),
    reject=re.compile(
        r"\b(?:промпт\w*|описани\w*|инструкци\w*|сценари\w*|"
        r"план\w*|анализ\w*|код\w*)\b",
        re.IGNORECASE,
    ),
    strong_verbs=re.compile(
        r"^(?:нарисуй|нарисовать|нарисуем|нарисовал)$",
        re.IGNORECASE,
    ),
    visual_cues=re.compile(
        r"(?:\b(?:1\s*:\s*1|4k|8k|квадратн\w*|вертикальн\w*|горизонтальн\w*|"
        r"реалистичн\w*|фотореалистичн\w*|мультяшн\w*|акварельн\w*|"
        r"кинематографичн\w*|портрет\w*|пейзаж\w*|фон\w*|без\s+текста)\b|"
        r"\bв\s+стиле\b)",
        re.IGNORECASE,
    ),
)

_ENGLISH_SPEC = _CommandSpec(
    command=re.compile(
        rf"^\s*{_ENGLISH_SOCIAL_PREFIX}(?:please[,.]?\s*)?"
        r"(?:(?:can|could|would)\s+you\s+|let(?:'s| us)\s+|i\s+want\s+you\s+to\s+)?"
        r"(?P<verb>generate|create|draw|make|render)\s+"
        r"(?:me\s+)?(?P<body>.+)$",
        re.IGNORECASE | re.DOTALL,
    ),
    target=re.compile(
        r"\b(?:image|picture|photo|poster|cover|artwork|illustration|drawing)s?\b",
        re.IGNORECASE,
    ),
    reject=re.compile(
        r"\b(?:prompt|description|instructions?|caption|scenario|plan|analysis|code)\b",
        re.IGNORECASE,
    ),
    strong_verbs=re.compile(r"^(?:draw|render)$", re.IGNORECASE),
    visual_cues=re.compile(
        r"(?:\b(?:1\s*:\s*1|4k|8k|square|vertical|horizontal|realistic|"
        r"photorealistic|cartoon|watercolor|cinematic|portrait|landscape|"
        r"background|without\s+text)\b|\bin\s+the\s+style\s+of\b)",
        re.IGNORECASE,
    ),
)

_TURKISH_COMMAND_FIRST_SPEC = _CommandSpec(
    command=re.compile(
        rf"^\s*{_TURKISH_SOCIAL_PREFIX}(?:lütfen[,.]?\s*)?"
        r"(?:(?:yapabilir\s+misin|oluşturabilir\s+misin|çizebilir\s+misin|hadi)\s+)?"
        r"(?:bana\s+)?(?P<verb>oluştur|üret|çiz|hazırla)\s+"
        r"(?:bana\s+)?(?P<body>.+)$",
        re.IGNORECASE | re.DOTALL,
    ),
    target=re.compile(
        r"\b(?:görsel|resim|fotoğraf|poster|kapak|illüstrasyon|çizim)\w*\b",
        re.IGNORECASE,
    ),
    reject=re.compile(
        r"\b(?:prompt|açıklama|talimat|senaryo|plan|analiz|kod)\b",
        re.IGNORECASE,
    ),
    strong_verbs=re.compile(r"^(?:çiz)$", re.IGNORECASE),
    visual_cues=re.compile(
        r"(?:\b(?:1\s*:\s*1|4k|8k|kare|dikey|yatay|gerçekçi|fotogerçekçi|"
        r"karikatür|suluboya|sinematik|portre|manzara|arka\s+plan|metinsiz)\b|"
        r"\bstilinde\b)",
        re.IGNORECASE,
    ),
)

_TURKISH_TARGET_FIRST = re.compile(
    rf"^\s*{_TURKISH_SOCIAL_PREFIX}(?:lütfen\s+)?(?P<body>.+?)\s+"
    r"(?:oluştur|üret|çiz|hazırla)(?=\s|:|—|–|-|$)\s*"
    r"(?:[:—–-]\s*)?(?P<tail>.*)$",
    re.IGNORECASE | re.DOTALL,
)

_TURKISH_TARGET = re.compile(
    r"\b(?:görsel|resim|fotoğraf|poster|kapak|illüstrasyon|çizim)\w*\b",
    re.IGNORECASE,
)

_TURKISH_REJECT = re.compile(
    r"\b(?:prompt|açıklama|talimat|senaryo|plan|analiz|kod)\b",
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


def last_user_message_from_chat_prompt(chat_prompt: str) -> str:
    matches = _USER_TURN_RE.findall(str(chat_prompt or ""))
    return str(matches[-1] if matches else "").strip()


def _intent_from_command(match: re.Match[str], spec: _CommandSpec) -> ImageIntent:
    body = match.group("body").strip()
    verb = str(match.group("verb") or "").strip()
    target = spec.target.search(body)

    if target and target.start() <= _MAX_TARGET_OFFSET:
        before_target = body[: target.start()]
        if spec.reject.search(before_target):
            return ImageIntent(False, "")
        description_after_target = body[target.end() :].strip(" \t\r\n:—–-,.;")
        if not description_after_target:
            return ImageIntent(True, "")
        return ImageIntent(True, body[:_MAX_PROMPT_CHARS])

    if spec.reject.search(body):
        return ImageIntent(False, "")
    if spec.strong_verbs.match(verb) or spec.visual_cues.search(body):
        return ImageIntent(True, body[:_MAX_PROMPT_CHARS])
    return ImageIntent(False, "")


def detect_image_intent(message: str) -> ImageIntent:
    normalized = _normalize(message)
    if not normalized:
        return ImageIntent(False, "")

    for spec in _COMMAND_SPECS:
        command_match = spec.command.match(normalized)
        if command_match:
            return _intent_from_command(command_match, spec)

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
    return detect_image_intent(last_user_message_from_chat_prompt(chat_prompt))
