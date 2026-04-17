from typing import Any, Dict, List, Optional

from db.database import get_connection, get_accuracy_stats


# ═══════════════════════════════════════════
# ОПРЕДЕЛЕНИЯ БЕЙДЖЕЙ
# ═══════════════════════════════════════════

BADGES = {
    "sniper": {
        "emoji": "🎯",
        "name": {"ru": "Снайпер", "en": "Sniper"},
        "description": {
            "ru": "5 угаданных прогнозов подряд",
            "en": "5 correct predictions in a row",
        },
    },
    "analyst": {
        "emoji": "📊",
        "name": {"ru": "Аналитик", "en": "Analyst"},
        "description": {
            "ru": "50 анализов всего",
            "en": "50 total analyses",
        },
    },
    "oracle": {
        "emoji": "👑",
        "name": {"ru": "Оракул", "en": "Oracle"},
        "description": {
            "ru": "Точность >70% при 30+ разрешённых прогнозах",
            "en": "Accuracy >70% with 30+ resolved predictions",
        },
    },
    "streaker": {
        "emoji": "🔥",
        "name": {"ru": "Стрикер", "en": "Streaker"},
        "description": {
            "ru": "10 угаданных прогнозов подряд",
            "en": "10 correct predictions in a row",
        },
    },
    "founder": {
        "emoji": "💎",
        "name": {"ru": "Основатель", "en": "Founder"},
        "description": {
            "ru": "Один из первых 100 пользователей",
            "en": "Among first 100 users",
        },
    },
    "author": {
        "emoji": "📢",
        "name": {"ru": "Автор", "en": "Author"},
        "description": {
            "ru": "Статус автора прогнозов",
            "en": "Prediction author status",
        },
    },
    "top_earner": {
        "emoji": "💰",
        "name": {"ru": "Топ-Заработок", "en": "Top Earner"},
        "description": {
            "ru": "Топ-10 авторов по донатам",
            "en": "Top-10 authors by donations",
        },
    },
    "wise": {
        "emoji": "🎓",
        "name": {"ru": "Мудрец", "en": "Wise"},
        "description": {
            "ru": "100 анализов всего",
            "en": "100 total analyses",
        },
    },
    "speed": {
        "emoji": "⚡",
        "name": {"ru": "Скорость", "en": "Speed"},
        "description": {
            "ru": "Использует inline query 20+ раз",
            "en": "Used inline query 20+ times",
        },
    },
    "connector": {
        "emoji": "👥",
        "name": {"ru": "Связной", "en": "Connector"},
        "description": {
            "ru": "5 или более приглашённых рефералов",
            "en": "5 or more invited referrals",
        },
    },
}


# ═══════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ
# ═══════════════════════════════════════════

def get_user_badges(user_id: int) -> List[str]:
    """
    Возвращает список ID бейджей которые заработал пользователь.
    Вычисляется на лету на основе данных из БД.
    """
    earned = []

    stats = _get_user_stats(user_id)
    if not stats:
        return earned

    # 🎯 Sniper — 5 подряд
    if stats["max_streak"] >= 5:
        earned.append("sniper")

    # 📊 Analyst — 50 анализов
    if stats["total_analyses"] >= 50:
        earned.append("analyst")

    # 👑 Oracle — точность >70% при 30+ разрешённых
    if stats["resolved_count"] >= 30 and stats["accuracy"] >= 70:
        earned.append("oracle")

    # 🔥 Streaker — 10 подряд
    if stats["max_streak"] >= 10:
        earned.append("streaker")

    # 💎 Founder — первые 100 юзеров
    if _is_founder(user_id):
        earned.append("founder")

    # 📢 Author — статус автора
    if stats.get("is_author"):
        earned.append("author")

    # 💰 Top Earner — топ-10 авторов
    if _is_top_earner(user_id):
        earned.append("top_earner")

    # 🎓 Wise — 100 анализов
    if stats["total_analyses"] >= 100:
        earned.append("wise")

    # ⚡ Speed — 20+ inline запросов
    if stats.get("inline_queries_count", 0) >= 20:
        earned.append("speed")

    # 👥 Connector — 5+ рефералов
    if stats.get("total_referrals", 0) >= 5:
        earned.append("connector")

    return earned


def format_badges_line(badge_ids: List[str], lang: str = "ru") -> str:
    """Возвращает строку с эмодзи бейджей, например: 🎯🔥📊"""
    if not badge_ids:
        return ""
    emojis = [BADGES[bid]["emoji"] for bid in badge_ids if bid in BADGES]
    return "".join(emojis)


def format_badges_list(badge_ids: List[str], lang: str = "ru") -> str:
    """Возвращает подробный список бейджей с названиями и описаниями."""
    if not badge_ids:
        return ""
    lines = []
    for bid in badge_ids:
        if bid not in BADGES:
            continue
        b = BADGES[bid]
        name = b["name"].get(lang, b["name"]["en"])
        desc = b["description"].get(lang, b["description"]["en"])
        lines.append(f"{b['emoji']} {name} — {desc}")
    return "\n".join(lines)


def format_next_badge_hint(user_id: int, lang: str = "ru") -> str:
    """
    Показывает подсказку — какой следующий бейдж ближе всего.
    Мотивация пользователя добиваться следующей ачивки.
    """
    stats = _get_user_stats(user_id)
    if not stats:
        return ""

    earned = set(get_user_badges(user_id))
    hints = []

    # Аналитик (50 анализов)
    if "analyst" not in earned:
        left = 50 - stats["total_analyses"]
        if 0 < left <= 20:
            hint = (
                f"📊 До Аналитика: ещё {left} анализов"
                if lang == "ru"
                else f"📊 To Analyst: {left} more analyses"
            )
            hints.append(hint)

    # Мудрец (100 анализов)
    if "wise" not in earned and stats["total_analyses"] >= 50:
        left = 100 - stats["total_analyses"]
        if left > 0:
            hint = (
                f"🎓 До Мудреца: ещё {left} анализов"
                if lang == "ru"
                else f"🎓 To Wise: {left} more analyses"
            )
            hints.append(hint)

    # Снайпер / Стрикер
    if "sniper" not in earned:
        left = 5 - stats["current_streak"]
        if left > 0 and stats["current_streak"] >= 2:
            hint = (
                f"🎯 До Снайпера: ещё {left} угаданных подряд"
                if lang == "ru"
                else f"🎯 To Sniper: {left} more correct in a row"
            )
            hints.append(hint)
    elif "streaker" not in earned:
        left = 10 - stats["current_streak"]
        if left > 0 and stats["current_streak"] >= 5:
            hint = (
                f"🔥 До Стрикера: ещё {left} угаданных подряд"
                if lang == "ru"
                else f"🔥 To Streaker: {left} more correct in a row"
            )
            hints.append(hint)

    # Оракул (70% точность при 30+)
    if "oracle" not in earned and stats["resolved_count"] < 30:
        left = 30 - stats["resolved_count"]
        hint = (
            f"👑 До Оракула: ещё {left} завершённых прогнозов"
            if lang == "ru"
            else f"👑 To Oracle: {left} more resolved predictions"
        )
        hints.append(hint)

    # Связной (5 рефералов)
    if "connector" not in earned:
        left = 5 - stats.get("total_referrals", 0)
        if 0 < left <= 3:
            hint = (
                f"👥 До Связного: ещё {left} рефералов"
                if lang == "ru"
                else f"👥 To Connector: {left} more referrals"
            )
            hints.append(hint)

    if not hints:
        return ""

    header = "🎯 Следующие цели:" if lang == "ru" else "🎯 Next goals:"
    return header + "\n" + "\n".join(hints[:3])  # максимум 3 подсказки


def get_all_badges_info(lang: str = "ru") -> str:
    """
    Возвращает полный список всех возможных бейджей с описаниями.
    Для команды /badges или профиля.
    """
    header = "🏆 Все бейджи системы:" if lang == "ru" else "🏆 All badges:"
    lines = [header, ""]
    for bid, b in BADGES.items():
        name = b["name"].get(lang, b["name"]["en"])
        desc = b["description"].get(lang, b["description"]["en"])
        lines.append(f"{b['emoji']} {name} — {desc}")
    return "\n".join(lines)


# ═══════════════════════════════════════════
# ВНУТРЕННИЕ ХЕЛПЕРЫ
# ═══════════════════════════════════════════

def _get_user_stats(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Собирает все нужные данные для вычисления бейджей в одном запросе.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Базовые поля юзера
        cursor.execute("""
        SELECT total_analyses, total_opportunities, total_referrals
        FROM users WHERE user_id = %s
        """, (user_id,))
        row = cursor.fetchone()
        if not row:
            return None

        total_analyses = row[0] or 0
        total_opportunities = row[1] or 0
        total_referrals = row[2] or 0

        # Статус автора (поле может ещё не существовать)
        is_author = False
        try:
            cursor.execute("SELECT is_author FROM users WHERE user_id = %s", (user_id,))
            r = cursor.fetchone()
            if r and r[0]:
                is_author = bool(r[0])
        except Exception:
            pass

        # Счётчик inline запросов
        inline_queries_count = 0
        try:
            cursor.execute(
                "SELECT inline_queries_count FROM users WHERE user_id = %s",
                (user_id,),
            )
            r = cursor.fetchone()
            if r and r[0]:
                inline_queries_count = r[0]
        except Exception:
            pass

        # Данные по точности — только разрешённые предсказания
        cursor.execute("""
        SELECT COUNT(*), SUM(is_correct)
        FROM predictions_tracking
        WHERE user_id = %s AND resolved_at IS NOT NULL
        """, (user_id,))
        r = cursor.fetchone()
        resolved_count = r[0] or 0
        correct_count = r[1] or 0
        accuracy = (correct_count / resolved_count * 100) if resolved_count > 0 else 0

        # Серии угаданных — берём последние 50 прогнозов в хронологическом порядке
        cursor.execute("""
        SELECT is_correct FROM predictions_tracking
        WHERE user_id = %s AND resolved_at IS NOT NULL
        ORDER BY resolved_at DESC LIMIT 50
        """, (user_id,))
        rows = cursor.fetchall()
        # rows[0] — самый свежий
        results = [bool(r[0]) for r in rows if r[0] is not None]

        current_streak = 0
        for is_correct in results:
            if is_correct:
                current_streak += 1
            else:
                break

        max_streak = 0
        streak = 0
        for is_correct in reversed(results):  # от старых к новым для max streak
            if is_correct:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0

        return {
            "total_analyses": total_analyses,
            "total_opportunities": total_opportunities,
            "total_referrals": total_referrals,
            "resolved_count": resolved_count,
            "correct_count": correct_count,
            "accuracy": accuracy,
            "current_streak": current_streak,
            "max_streak": max_streak,
            "is_author": is_author,
            "inline_queries_count": inline_queries_count,
        }

    except Exception as e:
        print(f"_get_user_stats error: {e}")
        return None
    finally:
        conn.close()


def _is_founder(user_id: int) -> bool:
    """Проверяет входит ли юзер в первые 100 зарегистрированных."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Считаем сколько юзеров зарегистрировалось ДО данного включительно
        cursor.execute("""
        SELECT COUNT(*) FROM users
        WHERE created_at <= (SELECT created_at FROM users WHERE user_id = %s)
        """, (user_id,))
        r = cursor.fetchone()
        rank = r[0] or 0
        return rank > 0 and rank <= 100
    except Exception:
        return False
    finally:
        conn.close()


def _is_top_earner(user_id: int) -> bool:
    """Проверяет входит ли юзер в топ-10 авторов по донатам."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Таблица author_donations может ещё не существовать
        cursor.execute("""
        SELECT author_id FROM author_donations
        WHERE author_id IS NOT NULL
        GROUP BY author_id
        ORDER BY SUM(author_received_ton) DESC
        LIMIT 10
        """)
        rows = cursor.fetchall()
        top_ids = {r[0] for r in rows}
        return user_id in top_ids
    except Exception:
        return False
    finally:
        conn.close()
