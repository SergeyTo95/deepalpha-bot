import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

PENDING_TASK_TTL_MINUTES = 30
_pending_jarvis_tasks: Dict[int, Dict[str, Any]] = {}


def _clean_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _short_title(text: str) -> str:
    clean = _clean_text(text)
    if not clean:
        return "Подготовить улучшение DeepAlpha"
    title = clean[:80].rstrip(".,;: ")
    return title[:1].upper() + title[1:]


def _infer_task_fields(text: str) -> Dict[str, str]:
    lower = _clean_text(text).lower()
    if any(word in lower for word in ("onboarding", "start", "demo", "activation", "онборд", "старт", "демо", "активац")):
        return {
            "metric": "Activation rate / first analysis rate",
            "role": "Bot developer / Product",
            "priority": "High",
        }
    if any(word in lower for word in ("help", "помощь", "помощ", "faq")):
        return {
            "metric": "Help click → analysis start rate",
            "role": "Bot developer / Content",
            "priority": "Medium",
        }
    if any(word in lower for word in ("payment", "касса", "tokens", "покупка", "платеж", "оплата", "токен")):
        return {
            "metric": "Payment conversion / successful purchase count",
            "role": "Backend developer",
            "priority": "High",
        }
    if any(word in lower for word in ("referral", "реферал")):
        return {
            "metric": "Referral activation rate",
            "role": "Backend/Product",
            "priority": "Medium",
        }
    return {
        "metric": "Completion of acceptance criteria",
        "role": "Product / Developer",
        "priority": "Medium",
    }


def build_team_task_from_text(text: str, creator_id: int) -> str:
    clean = _clean_text(text)
    if not clean:
        clean = "Подготовить и описать понятное улучшение DeepAlpha для команды."
    fields = _infer_task_fields(clean)
    deadline = (datetime.utcnow() + timedelta(days=3)).strftime("%Y-%m-%d")

    return (
        "🧠 Задача от Jarvis\n\n"
        "Название:\n"
        f"{_short_title(clean)}\n\n"
        "Цель:\n"
        "Улучшить продуктовый сценарий DeepAlpha и приблизить пользователя к первому полезному действию.\n\n"
        "Описание:\n"
        f"{clean}\n\n"
        "Критерии готовности:\n\n"
        "1. Решение реализовано и доступно в основном пользовательском сценарии.\n"
        "2. Сценарий можно проверить вручную от входа пользователя до целевого действия.\n"
        "3. Существующие платежи, рефералы, проверки, Jarvis /post и аналитика не сломаны.\n\n"
        "Метрика успеха:\n"
        f"{fields['metric']}\n\n"
        "Приоритет:\n"
        f"{fields['priority']}\n\n"
        "Срок:\n"
        f"{deadline}\n\n"
        "Кому:\n"
        f"{fields['role']}\n\n"
        "Контекст:\n"
        "Задача создана по запросу Сергея."
    )


def create_pending_task(creator_id: int, chat_id: int, raw_text: str) -> Dict[str, Any]:
    task_id = uuid.uuid4().hex[:8]
    task = {
        "id": task_id,
        "created_at": datetime.utcnow(),
        "creator_id": int(creator_id),
        "chat_id": int(chat_id),
        "raw_text": _clean_text(raw_text),
        "formatted_task": build_team_task_from_text(raw_text, creator_id),
    }
    _pending_jarvis_tasks[int(creator_id)] = task
    return task


def get_pending_task(creator_id: int, task_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    task = _pending_jarvis_tasks.get(int(creator_id))
    if not task:
        return None
    if task_id and task.get("id") != task_id:
        return None
    return task


def clear_pending_task(creator_id: int, task_id: Optional[str] = None) -> None:
    task = _pending_jarvis_tasks.get(int(creator_id))
    if not task:
        return
    if task_id and task.get("id") != task_id:
        return
    _pending_jarvis_tasks.pop(int(creator_id), None)


def is_pending_task_expired(task: Dict[str, Any]) -> bool:
    created_at = task.get("created_at")
    if not isinstance(created_at, datetime):
        return True
    return datetime.utcnow() - created_at > timedelta(minutes=PENDING_TASK_TTL_MINUTES)
