import re
from typing import Any, Dict, List, Optional

from db.database import (
    append_live_analyst_message,
    close_live_analyst_session,
    create_live_analyst_session,
    get_live_analyst_active_session,
    get_live_analyst_recent_messages,
    update_live_analyst_session,
)


POLYMARKET_URL_RE = re.compile(r"https?://[^\s]*polymarket\.com/[^\s]+", re.IGNORECASE)


def extract_polymarket_url(text: str) -> str:
    match = POLYMARKET_URL_RE.search(text or "")
    return match.group(0).rstrip(").,;!?") if match else ""


def extract_market_title(text: str) -> str:
    cleaned = re.sub(POLYMARKET_URL_RE, "", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:240]


def get_or_create_active_session(user_id: int) -> Dict[str, Any]:
    session = get_live_analyst_active_session(user_id)
    if session:
        return session
    return create_live_analyst_session(user_id)


def start_session(
    user_id: int,
    market_url: str = "",
    market_title: str = "",
    analysis_summary: str = "",
) -> Dict[str, Any]:
    session = get_or_create_active_session(user_id)
    updates: Dict[str, Any] = {}
    if market_url:
        updates["current_market_url"] = market_url
    if market_title:
        updates["current_market_title"] = market_title[:500]
    if analysis_summary:
        updates["last_analysis_summary"] = analysis_summary[:4000]
    if updates:
        update_live_analyst_session(int(session["id"]), **updates)
        session.update(updates)
    return session


def reset_session(user_id: int) -> Dict[str, Any]:
    session = get_live_analyst_active_session(user_id)
    if session:
        close_live_analyst_session(int(session["id"]))
    return create_live_analyst_session(user_id)


def exit_session(user_id: int) -> bool:
    session = get_live_analyst_active_session(user_id)
    if not session:
        return False
    return close_live_analyst_session(int(session["id"]))


def is_active(user_id: int) -> bool:
    return bool(get_live_analyst_active_session(user_id))


def save_message(
    session_id: int,
    user_id: int,
    role: str,
    message_type: str,
    content: str,
    image_file_id: Optional[str] = None,
    tokens_charged: int = 0,
) -> Optional[Dict[str, Any]]:
    return append_live_analyst_message(
        session_id=session_id,
        user_id=user_id,
        role=role,
        message_type=message_type,
        content=content,
        image_file_id=image_file_id,
        tokens_charged=tokens_charged,
    )


def get_recent_context(session_id: int, limit: int) -> List[Dict[str, Any]]:
    return get_live_analyst_recent_messages(session_id, limit=limit)


def update_context_from_user_text(session: Dict[str, Any], text: str) -> Dict[str, Any]:
    url = extract_polymarket_url(text)
    updates: Dict[str, Any] = {}
    if url and url != session.get("current_market_url"):
        updates["current_market_url"] = url
        title = extract_market_title(text)
        if title:
            updates["current_market_title"] = title
    if updates:
        update_live_analyst_session(int(session["id"]), **updates)
        session.update(updates)
    return session


def update_last_image_summary(session_id: int, summary: str) -> None:
    update_live_analyst_session(session_id, last_image_summary=(summary or "")[:4000])


def update_last_analysis_summary(session_id: int, summary: str) -> None:
    update_live_analyst_session(session_id, last_analysis_summary=(summary or "")[:4000])
