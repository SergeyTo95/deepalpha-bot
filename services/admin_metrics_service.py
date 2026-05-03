from datetime import datetime, timedelta


def safe_date_prefix(value):
    return str(value or "")[:10]


def in_last_days(value, days):
    d = safe_date_prefix(value)
    if not d:
        return False
    try:
        return d >= (datetime.utcnow() - timedelta(days=days)).date().isoformat()
    except Exception:
        return False


def summarize_users(users):
    total = len(users)
    return {
        "total_users": total,
        "new_today": sum(1 for u in users if safe_date_prefix(u.get("created_at")) == datetime.utcnow().date().isoformat()),
        "new_7d": sum(1 for u in users if in_last_days(u.get("created_at"), 7)),
        "active_7d": sum(1 for u in users if in_last_days(u.get("updated_at"), 7)),
        "token_sum": sum(int(u.get("token_balance", 0) or 0) for u in users),
        "vip": sum(1 for u in users if u.get("is_vip")),
        "banned": sum(1 for u in users if u.get("is_banned")),
        "authors": sum(1 for u in users if u.get("is_author")),
        "opportunities": sum(int(u.get("total_opportunities", 0) or 0) for u in users),
    }
