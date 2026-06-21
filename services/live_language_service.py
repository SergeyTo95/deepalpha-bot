import re
from typing import Optional


def detect_live_ui_language(text: str, stored_language: Optional[str] = None, telegram_language_code: Optional[str] = None) -> str:
    if stored_language in {"ru", "en"}:
        return stored_language
    if (telegram_language_code or "").lower().startswith("ru"):
        return "ru"
    if re.search(r"[А-Яа-яЁё]", text or ""):
        return "ru"
    return "en"


def get_live_thinking_message(ui_language: Optional[str]) -> str:
    if ui_language == "ru":
        return "🧠 Думаю… проверяю свежий контекст, риск и возможные сценарии."
    return "🧠 Thinking… checking fresh context, risk, and possible scenarios."
