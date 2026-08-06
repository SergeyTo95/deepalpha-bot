import re
import unicodedata
from dataclasses import dataclass
from typing import Optional


_MAX_PROMPT_CHARS = 4000
_USER_TURN_RE = re.compile(
    r"^USER:\s*(.*?)(?=\n\n(?:USER|ASSISTANT):|\Z)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)

_RU_PREFIX = (
    r"(?:(?:привет(?:ик)?|здравствуй(?:те)?|доброе\s+утро|"
    r"добрый\s+(?:день|вечер)|велия|слушай)\s*[,.!?:—–-]*\s*){0,2}"
)
_EN_PREFIX = (
    r"(?:(?:hi|hello|hey|good\s+(?:morning|afternoon|evening)|velia)"
    r"\s*[,.!?:—–-]*\s*){0,2}"
)
_TR_PREFIX = (
    r"(?:(?:merhaba|selam|günaydın|iyi\s+akşamlar|velia)"
    r"\s*[,.!?:—–-]*\s*){0,2}"
)

_REJECT = re.compile(
    r"(?:\b(?:промпт\w*|инструкци\w*|объясн\w*|расскаж\w*|"
    r"prompt|instructions?|explain|how\s+to|"
    r"açıklama|talimat|nasıl)\b)",
    re.IGNORECASE,
)

_RU_TEXT_TO_VIDEO = re.compile(
    rf"^\s*{_RU_PREFIX}(?:пожалуйста[,.]?\s*)?"
    r"(?:(?:можешь(?:\s+ли)?|сможешь|давай|прошу)\s+)?(?:мне\s+)?"
    r"(?:сгенерируй|создай|сделай|сними|сгенерировать|создать)\s+"
    r"(?:мне\s+)?(?:видео|ролик|клип)\b\s*(?:[:—–-]\s*)?(?P<prompt>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_RU_IMAGE_TO_VIDEO = re.compile(
    rf"^\s*{_RU_PREFIX}(?:пожалуйста[,.]?\s*)?"
    r"(?:(?:можешь(?:\s+ли)?|сможешь|давай|прошу)\s+)?(?:мне\s+)?"
    r"(?:оживи|анимируй|оживить|анимировать)\s+"
    r"(?:это\s+|эту\s+|данное\s+|данную\s+)?"
    r"(?:фото(?:графию)?|картинк\w*|изображени\w*)\b"
    r"\s*(?:[:—–-]\s*)?(?P<prompt>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_EN_TEXT_TO_VIDEO = re.compile(
    rf"^\s*{_EN_PREFIX}(?:please[,.]?\s*)?"
    r"(?:(?:can|could|would)\s+you\s+)?"
    r"(?:generate|create|make|render)\s+(?:me\s+)?"
    r"(?:a\s+)?(?:video|clip)\b\s*(?:[:—–-]\s*)?(?P<prompt>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_EN_IMAGE_TO_VIDEO = re.compile(
    rf"^\s*{_EN_PREFIX}(?:please[,.]?\s*)?"
    r"(?:(?:can|could|would)\s+you\s+)?"
    r"(?:animate|bring\s+to\s+life)\s+"
    r"(?:this\s+|the\s+)?(?:photo|picture|image)\b"
    r"\s*(?:[:—–-]\s*)?(?P<prompt>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_TR_TEXT_TO_VIDEO = re.compile(
    rf"^\s*{_TR_PREFIX}(?:lütfen[,.]?\s*)?"
    r"(?:(?:yapabilir\s+misin|oluşturabilir\s+misin|hadi)\s+)?"
    r"(?:bana\s+)?(?:video|klip)\s+(?:oluştur|üret|hazırla)\b"
    r"\s*(?:[:—–-]\s*)?(?P<prompt>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_TR_IMAGE_TO_VIDEO = re.compile(
    rf"^\s*{_TR_PREFIX}(?:lütfen[,.]?\s*)?"
    r"(?:(?:yapabilir\s+misin|hadi)\s+)?(?:bu\s+)?"
    r"(?:fotoğrafı|resmi|görseli)\s+(?:canlandır|hareketlendir|animasyonlaştır)\b"
    r"\s*(?:[:—–-]\s*)?(?P<prompt>.*)$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class VideoIntent:
    requested: bool
    mode: str = ""
    prompt: str = ""


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", normalized).strip()


def last_user_message_from_chat_prompt(chat_prompt: str) -> str:
    matches = _USER_TURN_RE.findall(str(chat_prompt or ""))
    return str(matches[-1] if matches else "").strip()


def _matched_intent(match: Optional[re.Match[str]], mode: str) -> Optional[VideoIntent]:
    if not match:
        return None
    message = str(match.group(0) or "")
    if _REJECT.search(message):
        return VideoIntent(False, "", "")
    prompt = _normalize(str(match.group("prompt") or ""))
    return VideoIntent(True, mode, prompt[:_MAX_PROMPT_CHARS])


def detect_video_intent(message: str) -> VideoIntent:
    normalized = _normalize(message)
    if not normalized:
        return VideoIntent(False, "", "")

    for pattern, mode in (
        (_RU_IMAGE_TO_VIDEO, "i2v"),
        (_EN_IMAGE_TO_VIDEO, "i2v"),
        (_TR_IMAGE_TO_VIDEO, "i2v"),
        (_RU_TEXT_TO_VIDEO, "t2v"),
        (_EN_TEXT_TO_VIDEO, "t2v"),
        (_TR_TEXT_TO_VIDEO, "t2v"),
    ):
        intent = _matched_intent(pattern.match(normalized), mode)
        if intent is not None:
            return intent
    return VideoIntent(False, "", "")


def video_intent_from_chat_prompt(chat_prompt: str) -> VideoIntent:
    return detect_video_intent(last_user_message_from_chat_prompt(chat_prompt))
