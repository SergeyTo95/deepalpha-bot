import logging
import re
from typing import Any, Dict, Iterable, List

logger = logging.getLogger(__name__)

_RUSSIAN_LOCATION_ALIASES = {
    "новополоцке": "Novopolotsk",
    "новополоцк": "Novopolotsk",
    "полоцке": "Polotsk",
    "полоцк": "Polotsk",
    "минске": "Minsk",
    "минск": "Minsk",
    "москве": "Moscow",
    "москва": "Moscow",
    "анталии": "Antalya",
    "анталье": "Antalya",
    "анталья": "Antalya",
    "стамбуле": "Istanbul",
    "стамбул": "Istanbul",
    "витебске": "Vitebsk",
    "витебск": "Vitebsk",
    "гомеле": "Gomel",
    "гомель": "Gomel",
    "гродно": "Grodno",
    "бресте": "Brest",
    "брест": "Brest",
    "могилеве": "Mogilev",
    "могилёве": "Mogilev",
    "могилев": "Mogilev",
    "могилёв": "Mogilev",
    "бобруйске": "Babruysk",
    "бобруйск": "Babruysk",
    "твери": "Tver",
    "тверь": "Tver",
    "перми": "Perm",
    "пермь": "Perm",
    "астане": "Astana",
    "астана": "Astana",
    "анкаре": "Ankara",
    "анкара": "Ankara",
    "одессе": "Odesa",
    "одесса": "Odesa",
    "праге": "Prague",
    "прага": "Prague",
}

_TRANSLITERATION = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
        "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E",
        "Ё": "Yo", "Ж": "Zh", "З": "Z", "И": "I", "Й": "Y", "К": "K",
        "Л": "L", "М": "M", "Н": "N", "О": "O", "П": "P", "Р": "R",
        "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "Kh", "Ц": "Ts",
        "Ч": "Ch", "Ш": "Sh", "Щ": "Shch", "Ъ": "", "Ы": "Y", "Ь": "",
        "Э": "E", "Ю": "Yu", "Я": "Ya",
    }
)


def _normalized_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower().replace("ё", "е")


def _looks_cyrillic(value: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", str(value or "")))


def _transliterate(value: str) -> str:
    return str(value or "").translate(_TRANSLITERATION).strip()


def _dedupe(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        cleaned = re.sub(r"\s+", " ", str(value or "").strip()).strip(" .,!?:;—–-")
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def location_fallback_candidates(location: str) -> List[str]:
    original = re.sub(r"\s+", " ", str(location or "").strip()).strip(" .,!?:;—–-")
    if not original:
        return []

    candidates: List[str] = []
    alias = _RUSSIAN_LOCATION_ALIASES.get(_normalized_key(original))
    if alias:
        candidates.append(alias)

    if _looks_cyrillic(original):
        base_forms: List[str] = []
        lower = original.lower()
        if lower.endswith("ии") and len(original) > 4:
            base_forms.append(original[:-2] + "ия")
        if lower.endswith("е") and len(original) > 4:
            base_forms.append(original[:-1])
        if lower.endswith("и") and len(original) > 4:
            # This catches a small set of common masculine city forms. Known
            # irregular forms such as Твери and Перми are handled by aliases.
            base_forms.append(original[:-1])

        candidates.extend(base_forms)
        candidates.extend(_transliterate(item) for item in base_forms)
        candidates.append(_transliterate(original))

    return [
        candidate
        for candidate in _dedupe(candidates)
        if candidate.casefold() != original.casefold()
    ][:4]


def install(velia_plugin_service_module: Any) -> None:
    if getattr(velia_plugin_service_module, "_velia_weather_location_patch_installed", False):
        return

    original_weather_context = velia_plugin_service_module._weather_context
    extract_location = velia_plugin_service_module._extract_location

    def weather_context_with_location_fallback(message: str) -> Dict[str, Any]:
        result = original_weather_context(message)
        if result.get("ok") or result.get("error") != "weather_location_not_found":
            return result

        requested_location = str(extract_location(message) or "").strip()
        for candidate in location_fallback_candidates(requested_location):
            retry = original_weather_context(f"weather in {candidate} now")
            if retry.get("ok"):
                retry["location_resolution"] = {
                    "requested": requested_location,
                    "resolved_query": candidate,
                }
                logger.info(
                    "VELIA_WEATHER_LOCATION_RESOLVED requested=%s resolved=%s",
                    requested_location,
                    candidate,
                )
                return retry
            if retry.get("error") not in {
                "weather_location_not_found",
                "weather_location_required",
            }:
                return retry

        return result

    velia_plugin_service_module._weather_context = weather_context_with_location_fallback
    velia_plugin_service_module._velia_weather_location_patch_installed = True
    logger.info("VELIA_WEATHER_LOCATION_PATCH_INSTALLED")
