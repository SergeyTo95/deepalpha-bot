from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from db.database import (
    create_referral_reward,
    get_referral_reward_settings,
    get_user,
    get_user_referral_earnings_summary,
    list_user_referral_rewards,
    unlock_due_referral_rewards,
    withdraw_available_referral_rewards_to_internal_wallet,
)


def process_token_purchase_referral_reward(*, buyer_user_id: int, purchase_amount_nano: int, purchase_ref: str = "") -> Optional[Dict[str, Any]]:
    settings = get_referral_reward_settings()
    if not settings.get("enabled"):
        return None
    if int(purchase_amount_nano or 0) <= 0:
        return None
    buyer = get_user(int(buyer_user_id)) or {}
    referrer_id = int(buyer.get("referred_by") or 0)
    if not referrer_id or referrer_id == int(buyer_user_id):
        return None
    percent = float(settings.get("reward_percent") or 10.0)
    unlock_hours = int(settings.get("unlock_hours") or 48)
    reward_nano = int((int(purchase_amount_nano) * percent) / 100.0)
    if reward_nano <= 0:
        return None
    unlock_at = (datetime.utcnow() + timedelta(hours=unlock_hours)).isoformat()
    return create_referral_reward(
        user_id=referrer_id,
        source_user_id=int(buyer_user_id),
        purchase_type="token_purchase",
        purchase_ref=(purchase_ref or "").strip() or None,
        purchase_amount_nano=int(purchase_amount_nano),
        reward_percent=percent,
        reward_nano=reward_nano,
        status="pending",
        unlock_at=unlock_at,
    )


def get_earnings_screen_data(user_id: int) -> Dict[str, Any]:
    unlock_due_referral_rewards()
    settings = get_referral_reward_settings()
    summary = get_user_referral_earnings_summary(user_id)
    recent = list_user_referral_rewards(user_id=user_id, limit=10, offset=0)
    return {"settings": settings, "summary": summary, "recent": recent}


def withdraw_referral_rewards(user_id: int) -> Dict[str, Any]:
    unlock_due_referral_rewards()
    return withdraw_available_referral_rewards_to_internal_wallet(user_id)
