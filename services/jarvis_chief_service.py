from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from services.jarvis_service import is_founder_user
from services.project_metrics_service import get_project_metrics_snapshot


DATA_NOT_ENOUGH = "данных недостаточно"


def _blocked(actor_id: int) -> Optional[str]:
    if not is_founder_user(actor_id):
        return "Команда доступна только основателю."
    return None


def _metric(snapshot: Dict[str, Any], key: str) -> Any:
    return (snapshot.get("metrics") or {}).get(key)


def _value(value: Any, suffix: str = "") -> str:
    if value is None:
        return DATA_NOT_ENOUGH
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
    else:
        text = str(value)
    return f"{text}{suffix}"


def _percent(value: Any) -> str:
    return _value(value, "%")


def _ratio_value(value: Any) -> str:
    return _value(value)


def _plural_days(days: int) -> str:
    days = int(days or 7)
    if days % 10 == 1 and days % 100 != 11:
        return f"{days} день"
    if days % 10 in (2, 3, 4) and days % 100 not in (12, 13, 14):
        return f"{days} дня"
    return f"{days} дней"


def _priority_label(priority: str) -> str:
    return {"High": "Высокий", "Medium": "Средний", "Low": "Низкий"}.get(priority, priority)


def _growth_quality_text(value: Any) -> str:
    labels = {
        "no_user_base": "Базы нет — рано оценивать качество роста.",
        "no_new_users": "Притока нет — рост остановился.",
        "low_quality_growth_activation_bottleneck": "Рост низкого качества: пользователи приходят, но не активируются.",
        "low_quality_growth_first_value_bottleneck": "Рост низкого качества: нет достаточного first value moment.",
        "organic_growth_signal": "Есть органический сигнал: рефералы заметны в новом притоке.",
        "early_growth_signal": "Есть ранний рост, следующий фокус — retention и монетизация.",
    }
    if value is None:
        return DATA_NOT_ENOUGH
    return labels.get(str(value), str(value))


def detect_primary_bottleneck(snapshot: dict) -> dict:
    m = snapshot.get("metrics") or {}
    total_users = m.get("total_users")
    new_users = m.get("new_users_days")
    active_users = m.get("active_users_days")
    activation_rate = m.get("activation_rate_days")
    analyses = m.get("analyses_days")
    revenue = m.get("revenue_ton_days")

    if total_users is None:
        return {
            "code": "no_data",
            "title": "Недостаточно данных",
            "explanation": "Jarvis пока не видит базовую пользовательскую аналитику.",
            "priority": "High",
            "recommended_actions": [
                "Проверить доступность таблицы users и read-only сбор метрик.",
                "Не принимать продуктовые решения, пока нет базовой пользовательской аналитики.",
            ],
        }

    if total_users == 0:
        return {
            "code": "no_users",
            "title": "Нет пользовательской базы",
            "explanation": "В продукте пока нет пользовательской базы. Сначала нужен рабочий входящий поток.",
            "priority": "High",
            "recommended_actions": [
                "Проверить acquisition и /start flow.",
                "Убедиться, что новый пользователь сохраняется после первого входа.",
            ],
        }

    activation_weak = active_users is not None and activation_rate is not None and activation_rate < 10
    first_value_weak = analyses is not None and analyses <= 1 and total_users >= 10
    if activation_weak:
        return {
            "code": "activation_weak",
            "title": "Слабая активация",
            "explanation": "Пользователи приходят, но почти не доходят до ключевого действия — анализа рынка. Сейчас проблема не в монетизации, а в first value moment.",
            "priority": "High",
            "recommended_actions": [
                "Добавить демо-анализ после /start.",
                "Сделать кнопку “Показать пример сигнала”.",
                "После демо сразу просить Polymarket ссылку.",
            ],
        }

    if first_value_weak:
        return {
            "code": "first_value_weak",
            "title": "Слабый first value moment",
            "explanation": "Люди не используют ключевую функцию — анализ рынка. Монетизацию рано оптимизировать, пока анализов почти нет.",
            "priority": "High",
            "recommended_actions": [
                "Упростить путь до первого анализа до одного CTA.",
                "Показать пример результата до запроса оплаты.",
                "После демо сразу вести пользователя к вводу Polymarket ссылки.",
            ],
        }

    if new_users == 0:
        return {
            "code": "acquisition_weak",
            "title": "Нет нового притока",
            "explanation": "За период не видно новых пользователей.",
            "priority": "Medium",
            "recommended_actions": [
                "Проверить источники трафика и /start flow.",
                "Не масштабировать платные каналы, пока не понятен источник последнего качественного пользователя.",
            ],
        }

    if activation_rate is not None and activation_rate < 10:
        return {
            "code": "monetization_too_early",
            "title": "Монетизацию рано оптимизировать",
            "explanation": "Сначала нужно поднять активацию и использование анализа, иначе новые пользователи не будут покупать.",
            "priority": "High",
            "recommended_actions": [
                "Снять фокус с оплаты до activation rate 10–15%.",
                "Усилить первый момент ценности в онбординге.",
            ],
        }

    if analyses is not None and analyses <= 1:
        return {
            "code": "monetization_too_early",
            "title": "Монетизацию рано оптимизировать",
            "explanation": "Сначала нужно поднять активацию и использование анализа, иначе новые пользователи не будут покупать.",
            "priority": "High",
            "recommended_actions": [
                "Довести пользователя до первого анализа до любого давления на покупку.",
                "Сравнить конверсию /start → первый анализ после изменения онбординга.",
            ],
        }

    if activation_rate is not None and activation_rate >= 10 and revenue == 0:
        return {
            "code": "revenue_weak",
            "title": "Слабая монетизация",
            "explanation": "Активность есть, но платёжный путь не конвертирует.",
            "priority": "Medium",
            "recommended_actions": [
                "Проверить paywall и понятность ценности до покупки.",
                "Найти точку, где активный пользователь должен увидеть платное действие.",
            ],
        }

    if new_users is not None and new_users > 0 and activation_rate is not None and activation_rate >= 10 and analyses is not None and analyses > 1:
        return {
            "code": "healthy_early_signal",
            "title": "Есть ранний product signal",
            "explanation": "Пользователи приходят и используют продукт. Следующий фокус — retention и монетизация.",
            "priority": "Low",
            "recommended_actions": [
                "Измерить повторное использование анализа.",
                "Аккуратно тестировать монетизацию без ухудшения first value moment.",
                "Масштабировать только каналы, которые приводят активных пользователей.",
            ],
        }

    return {
        "code": "needs_more_signal",
        "title": "Нужен более чёткий сигнал",
        "explanation": "Базовые данные есть, но главный bottleneck не определяется без фальшивых выводов.",
        "priority": "Medium",
        "recommended_actions": [
            "Проверить полноту событий: /start, первый анализ, покупка, referral.",
            "Смотреть на activation rate и first analysis rate как на главные метрики недели.",
        ],
    }


def _decision(snapshot: Dict[str, Any], bottleneck: Dict[str, Any]) -> str:
    code = bottleneck.get("code")
    activation_rate = _metric(snapshot, "activation_rate_days")
    analyses_per_user = _metric(snapshot, "analyses_per_user_days")
    if code in ("activation_weak", "first_value_weak", "monetization_too_early"):
        return "Не масштабировать трафик и не давить на покупки, пока activation rate ниже 10–15%. Главная проблема — активация, не монетизация."
    if code == "acquisition_weak":
        return "Сейчас нужен новый приток, но масштабировать стоит только после проверки first value moment. Пустой трафик не лечит продукт."
    if code == "revenue_weak":
        return "Можно оптимизировать оплату, потому что базовая активация уже есть. Но не ломать путь до первого анализа."
    if code == "healthy_early_signal":
        return "Есть ранний сигнал. Следующий шаг — retention и аккуратная монетизация, не агрессивный paywall."
    if activation_rate is not None and activation_rate < 10:
        return "Сейчас не надо оптимизировать оплату. Главная задача — поднять activation rate. Если привести ещё 100 пользователей при текущей активации, большинство просто уйдёт."
    if analyses_per_user is not None and analyses_per_user < 0.1:
        return "Сначала поднять first analysis rate. Без использования анализа покупка будет случайной, не системной."
    return "Не делать выводов за пределами данных. Следующий фокус — добрать события по активации и первому анализу."


def _format_missing(snapshot: Dict[str, Any]) -> str:
    missing = snapshot.get("missing_metrics") or []
    if not missing:
        return "нет"
    labels = {
        "active_users_days": "активные пользователи",
        "activation_rate_days": "activation rate",
        "new_user_growth_rate_days": "growth rate",
        "analysis_conversion_days": "analysis conversion",
        "analyses_per_user_days": "analyses per user",
        "analyses_per_active_user_days": "analyses per active user",
        "purchase_conversion_days": "purchase conversion",
        "revenue_per_user_days": "revenue per user",
        "revenue_per_active_user_days": "revenue per active user",
        "referral_ratio": "referral ratio",
        "growth_quality": "growth quality",
        "revenue_ton_total": "общая выручка",
        "revenue_ton_days": "выручка за период",
        "referrals_days": "рефералы за период",
        "top_user_actions": "топ действий",
        "analyses_days": "анализы за период",
        "token_purchases_days": "покупки за период",
    }
    return ", ".join(labels.get(item, item) for item in missing[:16])


def _purchase_line(snapshot: Dict[str, Any]) -> str:
    purchases = _metric(snapshot, "token_purchases_days")
    revenue = _metric(snapshot, "revenue_ton_days")
    if purchases is not None and revenue is not None:
        return f"{purchases} / {_value(revenue, ' Gram')}"
    if purchases is not None:
        return str(purchases)
    if _metric(snapshot, "purchase_intents_days") is not None:
        return f"{_metric(snapshot, 'purchase_intents_days')} payment intents, не выручка"
    return DATA_NOT_ENOUGH


def build_chief_report(actor_id: int, days: int = 7) -> str:
    blocked = _blocked(actor_id)
    if blocked:
        return blocked
    snapshot = get_project_metrics_snapshot(days=days)
    period = _plural_days(snapshot.get("period_days") or days)
    bottleneck = detect_primary_bottleneck(snapshot)
    actions = bottleneck.get("recommended_actions") or []
    action_lines = "\n".join(f"{i}. {action}" for i, action in enumerate(actions[:3], 1))

    return (
        "🧠 Jarvis Chief\n\n"
        "Период:\n"
        f"последние {period}\n\n"
        "Ключевые метрики:\n\n"
        f"- Пользователи: {_value(_metric(snapshot, 'total_users'))}\n"
        f"- Новые: {_value(_metric(snapshot, 'new_users_days'))}\n"
        f"- Активные: {_value(_metric(snapshot, 'active_users_days'))}\n"
        f"- Активация: {_percent(_metric(snapshot, 'activation_rate_days'))}\n"
        f"- Анализы: {_value(_metric(snapshot, 'analyses_days'))}\n"
        f"- Анализов на пользователя: {_ratio_value(_metric(snapshot, 'analyses_per_user_days'))}\n"
        f"- Покупки/выручка: {_purchase_line(snapshot)}\n"
        f"- Рефералы: {_value(_metric(snapshot, 'referrals_days'))}\n\n"
        "Главный bottleneck:\n"
        f"{bottleneck.get('title')}\n\n"
        "Что это значит:\n"
        f"{bottleneck.get('explanation')}\n\n"
        "Что делать сейчас:\n\n"
        f"{action_lines}\n\n"
        "Решение по приоритетам:\n"
        f"{_decision(snapshot, bottleneck)}"
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
        f"Activation rate | {_percent(m.get('activation_rate_days'))}",
        f"Growth rate | {_percent(m.get('new_user_growth_rate_days'))}",
        f"Analysis conversion | {_percent(m.get('analysis_conversion_days'))}",
        f"Analyses per user | {_ratio_value(m.get('analyses_per_user_days'))}",
        f"Analyses per active user | {_ratio_value(m.get('analyses_per_active_user_days'))}",
        f"Purchase conversion | {_percent(m.get('purchase_conversion_days'))}",
        f"Revenue per user | {_value(m.get('revenue_per_user_days'), ' Gram')}",
        f"Revenue per active user | {_value(m.get('revenue_per_active_user_days'), ' Gram')}",
        f"Referral ratio | {_ratio_value(m.get('referral_ratio'))}",
        f"Growth quality | {_growth_quality_text(m.get('growth_quality'))}",
        f"Анализы всего | {_value(m.get('total_analyses'))}",
        f"Анализы 24ч | {_value(m.get('analyses_24h'))}",
        f"Анализы за период | {_value(m.get('analyses_days'))}",
        f"Подтверждённые token purchases всего | {_value(m.get('total_token_purchases'))}",
        f"Подтверждённые token purchases за период | {_value(m.get('token_purchases_days'))}",
        f"Выручка всего | {_value(m.get('revenue_ton_total'), ' Gram')}",
        f"Выручка за период | {_value(m.get('revenue_ton_days'), ' Gram')}",
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
    bottleneck = detect_primary_bottleneck(snapshot)
    actions = bottleneck.get("recommended_actions") or []
    next_tasks = "\n".join(f"{i}. {action}" for i, action in enumerate(actions[:3], 1))

    return (
        "📊 DeepAlpha Report\n\n"
        "1. Growth\n"
        f"Новые пользователи: {_value(m.get('new_users_days'))}. Growth rate: {_percent(m.get('new_user_growth_rate_days'))}. "
        f"Качество роста: {_growth_quality_text(m.get('growth_quality'))}\n\n"
        "2. Activation\n"
        f"Активные пользователи: {_value(m.get('active_users_days'))}. Activation rate: {_percent(m.get('activation_rate_days'))}.\n\n"
        "3. Product usage\n"
        f"Анализы: {_value(m.get('analyses_days'))}. Analysis conversion: {_percent(m.get('analysis_conversion_days'))}. "
        f"Анализов на пользователя: {_ratio_value(m.get('analyses_per_user_days'))}. "
        f"Анализов на активного: {_ratio_value(m.get('analyses_per_active_user_days'))}.\n\n"
        "4. Revenue\n"
        f"Покупки/выручка: {_purchase_line(snapshot)}. Purchase conversion: {_percent(m.get('purchase_conversion_days'))}. "
        f"Revenue per user: {_value(m.get('revenue_per_user_days'), ' Gram')}. "
        f"Revenue per active user: {_value(m.get('revenue_per_active_user_days'), ' Gram')}.\n\n"
        "5. Referral signal\n"
        f"Рефералы: {_value(m.get('referrals_days'))}. Referral ratio: {_ratio_value(m.get('referral_ratio'))}.\n\n"
        "6. Bottleneck\n"
        f"{bottleneck.get('title')} — {_priority_label(str(bottleneck.get('priority')))} priority. {bottleneck.get('explanation')}\n\n"
        "7. Decision\n"
        f"{_decision(snapshot, bottleneck)}\n\n"
        "8. Next 3 tasks\n"
        f"{next_tasks}"
    )


def build_team_task_draft(text: str, actor_id: int) -> str:
    blocked = _blocked(actor_id)
    if blocked:
        return blocked
    clean = " ".join((text or "").strip().split())
    if not clean:
        clean = "Улучшить активацию и первый анализ DeepAlpha"
    name = clean[:80].rstrip(".,;: ")
    lower = clean.lower()
    deadline = (datetime.utcnow() + timedelta(days=3)).strftime("%Y-%m-%d")
    priority = "High" if any(word in lower for word in ("onboarding", "онборд", "activation", "активац", "first", "перв")) else "Medium"
    role = "product + engineering" if any(word in lower for word in ("onboarding", "онборд", "activation", "активац")) else "ответственный product/engineering"

    return (
        "Черновик задачи\n\n"
        "Задача:\n"
        f"{name}\n\n"
        "Бизнес-цель:\n"
        "Поднять активацию: больше новых пользователей должны дойти до первого анализа и увидеть ценность продукта до покупки.\n\n"
        "Описание:\n"
        f"{clean}\n\n"
        "Критерии готовности:\n"
        "1. Новый пользователь после /start видит понятный путь к первому анализу.\n"
        "2. Есть демо/пример сигнала или короткий CTA, который ведёт к Polymarket ссылке.\n"
        "3. Можно вручную проверить сценарий /start → первый анализ без платежа.\n"
        "4. Существующие платежи, рефералы, проверки и Jarvis /post не затронуты.\n\n"
        "Метрики, которые должны улучшиться:\n"
        "- Activation rate.\n"
        "- First analysis rate.\n"
        "- Analyses per user.\n\n"
        "Метрика успеха:\n"
        "Activation rate / first analysis rate.\n\n"
        "Приоритет:\n"
        f"{priority}\n\n"
        "Срок:\n"
        f"{deadline}\n\n"
        "Suggested role:\n"
        f"{role}\n\n"
        "Автопостинг в командный чат отключён. Это только черновик."
    )
