from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from services.jarvis_service import is_founder_user
from services.project_metrics_service import MISSING_TEXT, get_project_metrics_snapshot


def _blocked(actor_id: int) -> Optional[str]:
    if not is_founder_user(actor_id):
        return "Команда доступна только основателю."
    return None


def _metric(snapshot: Dict[str, Any], key: str) -> Any:
    return (snapshot.get("metrics") or {}).get(key)


def _value(value: Any, suffix: str = "") -> str:
    if value is None:
        return "данных недостаточно"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
    else:
        text = str(value)
    return f"{text}{suffix}"


def _plural_days(days: int) -> str:
    days = int(days or 7)
    if days % 10 == 1 and days % 100 != 11:
        return f"{days} день"
    if days % 10 in (2, 3, 4) and days % 100 not in (12, 13, 14):
        return f"{days} дня"
    return f"{days} дней"


def _has_any_real_value(snapshot: Dict[str, Any]) -> bool:
    return any(v is not None for v in (snapshot.get("metrics") or {}).values())


def _main_insight(snapshot: Dict[str, Any]) -> str:
    if not _has_any_real_value(snapshot):
        return MISSING_TEXT
    total_users = _metric(snapshot, "total_users")
    new_users = _metric(snapshot, "new_users_days")
    analyses = _metric(snapshot, "analyses_days")
    active = _metric(snapshot, "active_users_days")
    purchases = _metric(snapshot, "token_purchases_days")

    if total_users is not None and new_users is not None:
        if total_users == 0:
            return "Пользовательская база пока пустая. Сначала нужно проверить входящий поток и онбординг."
        if new_users == 0:
            return "По текущим данным база есть, но нового притока за период не видно."
        return f"По текущим данным база растёт: +{new_users} пользователей за период при общей базе {total_users}."
    if analyses is not None:
        return f"По продукту видно использование анализов: {analyses} за период."
    if active is not None:
        return f"За период видна активность {active} пользователей."
    if purchases is not None:
        return f"Платёжный сигнал есть: {purchases} подтверждённых покупок за период."
    return "Данных пока мало, но часть инфраструктурных метрик уже читается."


def _main_risk(snapshot: Dict[str, Any]) -> str:
    missing = set(snapshot.get("missing_metrics") or [])
    if not _has_any_real_value(snapshot):
        return MISSING_TEXT
    if "active_users_days" in missing:
        return "Нет полной событийной аналитики: сложно оценить реальное удержание и активность."
    if _metric(snapshot, "new_users_days") == 0:
        return "Нет видимого нового притока пользователей за период."
    if _metric(snapshot, "analyses_days") == 0:
        return "Пользователи есть, но анализы за период не видны — возможен bottleneck в активации."
    if _metric(snapshot, "revenue_ton_days") in (None, 0):
        return "Выручка за период не подтверждается данными: монетизация может быть слабым местом."
    return "Критичный риск по доступным данным не выделяется; нужен больший объём событийной аналитики."


def _actions(snapshot: Dict[str, Any]) -> List[str]:
    actions: List[str] = []
    if _metric(snapshot, "active_users_days") is None:
        actions.append("Подключить/уточнить событийную аналитику: запуск анализа, покупка, чек, referral, watchlist.")
    if _metric(snapshot, "new_users_days") in (None, 0):
        actions.append("Проверить верх воронки: откуда приходит новый пользователь и где он теряется.")
    if _metric(snapshot, "analyses_days") in (None, 0):
        actions.append("Упростить первый анализ: один понятный CTA и короткий пример рынка.")
    if _metric(snapshot, "revenue_ton_days") in (None, 0):
        actions.append("Проверить платёжный путь и показать пользователю момент ценности до покупки.")
    if _metric(snapshot, "referral_count") in (None, 0):
        actions.append("Не масштабировать referral-механику, пока нет стабильной активации и понятной ценности.")
    actions.append("Смотреть этот отчёт регулярно и принимать решения только по подтверждённым числам.")
    return actions[:3]


def _format_missing(snapshot: Dict[str, Any]) -> str:
    missing = snapshot.get("missing_metrics") or []
    if not missing:
        return "нет"
    labels = {
        "active_users_days": "активные пользователи",
        "revenue_ton_total": "общая выручка",
        "revenue_ton_days": "выручка за период",
        "referrals_days": "рефералы за период",
        "top_user_actions": "топ действий",
        "analyses_days": "анализы за период",
        "token_purchases_days": "покупки за период",
    }
    return ", ".join(labels.get(item, item) for item in missing[:12])


def build_chief_report(actor_id: int, days: int = 7) -> str:
    blocked = _blocked(actor_id)
    if blocked:
        return blocked
    snapshot = get_project_metrics_snapshot(days=days)
    period = _plural_days(snapshot.get("period_days") or days)
    actions = _actions(snapshot)
    action_lines = "\n".join(f"{i}. {action}" for i, action in enumerate(actions, 1))
    purchases = _metric(snapshot, "token_purchases_days")
    revenue = _metric(snapshot, "revenue_ton_days")
    purchase_line = "данных недостаточно"
    if purchases is not None and revenue is not None:
        purchase_line = f"{purchases} / {_value(revenue, ' TON')}"
    elif purchases is not None:
        purchase_line = str(purchases)
    elif _metric(snapshot, "purchase_intents_days") is not None:
        purchase_line = f"{_metric(snapshot, 'purchase_intents_days')} payment intents, не выручка"

    return (
        "🧠 Jarvis Chief\n\n"
        "Период:\n"
        f"последние {period}\n\n"
        "Метрики:\n\n"
        f"- Пользователи: {_value(_metric(snapshot, 'total_users'))}\n"
        f"- Новые за {snapshot.get('period_days') or days} дней: {_value(_metric(snapshot, 'new_users_days'))}\n"
        f"- Новые за 24 часа: {_value(_metric(snapshot, 'new_users_24h'))}\n"
        f"- Активность: {_value(_metric(snapshot, 'active_users_days'))}\n"
        f"- Анализы: {_value(_metric(snapshot, 'analyses_days'))}\n"
        f"- Покупки/выручка: {purchase_line}\n"
        f"- Рефералы: {_value(_metric(snapshot, 'referrals_days'))}\n\n"
        "Главный вывод:\n"
        f"{_main_insight(snapshot)}\n\n"
        "Главный риск:\n"
        f"{_main_risk(snapshot)}\n\n"
        "Что делать сейчас:\n\n"
        f"{action_lines}"
    )


def build_metrics_report(actor_id: int, days: int = 7) -> str:
    blocked = _blocked(actor_id)
    if blocked:
        return blocked
    snapshot = get_project_metrics_snapshot(days=days)
    m = snapshot.get("metrics") or {}
    lines = [
        "📈 DeepAlpha Metrics",
        "",
        f"Период: последние {_plural_days(snapshot.get('period_days') or days)}",
        f"Собрано: {str(snapshot.get('generated_at') or '')[:19].replace('T', ' ')} UTC",
        "",
        "Метрика | Значение",
        "--- | ---",
        f"Пользователи всего | {_value(m.get('total_users'))}",
        f"Новые 24ч | {_value(m.get('new_users_24h'))}",
        f"Новые за период | {_value(m.get('new_users_days'))}",
        f"Активные за период | {_value(m.get('active_users_days'))}",
        f"Анализы всего | {_value(m.get('total_analyses'))}",
        f"Анализы 24ч | {_value(m.get('analyses_24h'))}",
        f"Анализы за период | {_value(m.get('analyses_days'))}",
        f"Подтверждённые token purchases всего | {_value(m.get('total_token_purchases'))}",
        f"Подтверждённые token purchases за период | {_value(m.get('token_purchases_days'))}",
        f"Выручка всего | {_value(m.get('revenue_ton_total'), ' TON')}",
        f"Выручка за период | {_value(m.get('revenue_ton_days'), ' TON')}",
        f"Payment intents всего | {_value(m.get('purchase_intents_total'))}",
        f"Payment intents за период | {_value(m.get('purchase_intents_days'))}",
        f"Рефералы всего | {_value(m.get('referral_count'))}",
        f"Рефералы за период | {_value(m.get('referrals_days'))}",
        f"Созданные чеки всего | {_value(m.get('created_checks_total'))}",
        f"Созданные чеки за период | {_value(m.get('created_checks_days'))}",
        f"Активированные чеки всего | {_value(m.get('claimed_checks_total'))}",
        f"Активированные чеки за период | {_value(m.get('claimed_checks_days'))}",
        f"Watchlist всего | {_value(m.get('watchlist_count'))}",
        f"Watchlist active | {_value(m.get('watchlist_active_count'))}",
        "",
        f"Недостающие метрики: {_format_missing(snapshot)}",
    ]
    if snapshot.get("notes"):
        lines.extend(["", "Заметки:"] + [f"- {note}" for note in snapshot.get("notes") or []])
    if m.get("top_user_actions"):
        lines.extend(["", "Топ действий:"])
        for item in m.get("top_user_actions") or []:
            lines.append(f"- {item.get('name')}: всего {_value(item.get('total'))}, за период {_value(item.get('in_period'))}")
    return "\n".join(lines)


def build_report(actor_id: int, days: int = 7) -> str:
    blocked = _blocked(actor_id)
    if blocked:
        return blocked
    snapshot = get_project_metrics_snapshot(days=days)
    m = snapshot.get("metrics") or {}
    product = "Данных пока недостаточно."
    if m.get("analyses_days") is not None or m.get("watchlist_active_count") is not None:
        product = (
            f"Анализы за период: {_value(m.get('analyses_days'))}. "
            f"Watchlist active: {_value(m.get('watchlist_active_count'))}."
        )
    growth = "Данных пока недостаточно."
    if m.get("new_users_days") is not None or m.get("referrals_days") is not None:
        growth = f"Новые пользователи: {_value(m.get('new_users_days'))}. Рефералы: {_value(m.get('referrals_days'))}."
    revenue = "Данных пока недостаточно."
    if m.get("revenue_ton_days") is not None:
        revenue = f"Подтверждённая выручка за период: {_value(m.get('revenue_ton_days'), ' TON')}."
    elif m.get("purchase_intents_days") is not None:
        revenue = f"Payment intents за период: {_value(m.get('purchase_intents_days'))}. Это не выручка."

    return (
        "📊 DeepAlpha Report\n\n"
        "Пользователи:\n"
        f"Всего: {_value(m.get('total_users'))}. Новые за период: {_value(m.get('new_users_days'))}. "
        f"Активные: {_value(m.get('active_users_days'))}.\n\n"
        "Продукт:\n"
        f"{product}\n\n"
        "Рост:\n"
        f"{growth}\n\n"
        "Доход:\n"
        f"{revenue}\n\n"
        "Команда:\n"
        "Пока нет подключённой task-системы / данных недостаточно.\n\n"
        "Главный bottleneck:\n"
        f"{_main_risk(snapshot)}\n\n"
        "Решение:\n"
        f"{_actions(snapshot)[0]}"
    )


def build_team_task_draft(text: str, actor_id: int) -> str:
    blocked = _blocked(actor_id)
    if blocked:
        return blocked
    clean = " ".join((text or "").strip().split())
    if not clean:
        clean = "Уточнить задачу по продуктовым метрикам DeepAlpha"
    name = clean[:80].rstrip(".,;: ")
    deadline = (datetime.utcnow() + timedelta(days=3)).strftime("%Y-%m-%d")
    priority = "High" if any(word in clean.lower() for word in ("срочно", "critical", "критично", "выруч", "payment", "плат")) else "Medium"
    role = "product/engineering" if any(word in clean.lower() for word in ("метрик", "analytics", "данн", "event")) else "ответственный по направлению"
    return (
        "Задача:\n"
        f"{name}\n\n"
        "Цель:\n"
        "Убрать конкретный bottleneck и дать Сергею проверяемый результат по данным.\n\n"
        "Описание:\n\n"
        f"{clean}\n\n"
        "Критерии готовности:\n"
        "1. Есть понятный результат, который можно проверить вручную.\n"
        "2. Нет влияния на платежи, балансы, рефералы и существующий Jarvis без отдельного approval.\n"
        "3. Коротко описаны данные/скрин/лог, подтверждающие выполнение.\n\n"
        "Приоритет:\n"
        f"{priority}\n\n"
        "Срок:\n"
        f"{deadline}\n\n"
        "Кому:\n"
        f"{role}\n\n"
        "Автопостинг в командный чат отключён. Это только черновик."
    )
