import pytest

from services.broadcast_service import (
    DEFAULT_BROADCAST_TEXT,
    FORBIDDEN_BROADCAST_PHRASES,
    BroadcastState,
    filter_broadcast_recipients,
)


def test_recipient_filtering_excludes_banned_users():
    users = [
        {"user_id": 1, "is_banned": 0},
        {"user_id": 2, "is_banned": 1},
        {"user_id": 3, "is_banned": False},
    ]
    assert filter_broadcast_recipients(users) == [1, 3]


def test_recipient_filtering_removes_duplicate_user_ids():
    users = [
        {"user_id": "10", "is_banned": 0},
        {"user_id": 10, "is_banned": 0},
        {"user_id": 11, "is_banned": 0},
    ]
    assert filter_broadcast_recipients(users) == [10, 11]


def test_broadcast_state_counters_update():
    state = BroadcastState()
    state.start(4)
    state.mark_sent()
    state.mark_failed("network")
    state.mark_blocked("blocked")
    state.mark_skipped()
    state.finish()
    assert state.status == "finished"
    assert state.total == 4
    assert state.sent == 1
    assert state.failed == 1
    assert state.blocked == 1
    assert state.skipped == 1
    assert state.last_error == "blocked"
    assert state.finished_at


@pytest.mark.parametrize("phrase", [
    "DeepAlpha",
    "AI-советник",
    "крипту",
    "спорт",
    "киберспорт",
    "Политика",
    "Airdrop Points",
    "Live режим",
])
def test_default_broadcast_text_contains_required_terms(phrase):
    assert phrase in DEFAULT_BROADCAST_TEXT


def test_default_broadcast_text_does_not_contain_forbidden_phrases():
    lower_text = DEFAULT_BROADCAST_TEXT.lower()
    for phrase in FORBIDDEN_BROADCAST_PHRASES:
        assert phrase.lower() not in lower_text
