import sys
import types

import pytest

sys.modules.setdefault(
    "psycopg2",
    types.SimpleNamespace(
        connect=lambda *args, **kwargs: None,
        extras=types.SimpleNamespace(RealDictCursor=object),
        errors=types.SimpleNamespace(),
    ),
)
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))
sys.modules.setdefault("psycopg2.errors", types.SimpleNamespace())

from services import airdrop_referral_service as svc


@pytest.fixture(autouse=True)
def reset_referral_memory(monkeypatch):
    svc._MEMORY_REFERRALS.clear()
    svc._MEMORY_MILESTONES.clear()
    monkeypatch.setattr(
        svc,
        "get_connection",
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    yield


def test_invited_count_uses_registered_referral_even_without_milestone():
    svc._MEMORY_REFERRALS[20] = {
        "referrer_user_id": 10,
        "referred_user_id": 20,
        "status": svc.PENDING,
    }

    summary = svc.get_referral_summary(10)

    assert summary["invited"] == 1
    assert summary["milestones"] == []


def test_invited_count_deduplicates_registration_and_milestone():
    svc._MEMORY_REFERRALS[20] = {
        "referrer_user_id": 10,
        "referred_user_id": 20,
        "status": svc.PENDING,
    }
    svc._MEMORY_MILESTONES.append(
        {
            "referrer_user_id": 10,
            "referred_user_id": 20,
            "milestone": svc.M1_STARTED_BOT,
            "points": 20,
            "status": svc.PENDING,
        }
    )

    summary = svc.get_referral_summary(10)

    assert summary["invited"] == 1
    assert summary["pending_points"] == 20


def test_db_source_unions_airdrop_and_legacy_referrals(monkeypatch):
    class Cursor:
        def __init__(self):
            self.sql = ""
            self.params = None

        def execute(self, sql, params):
            self.sql = sql
            self.params = params

        def fetchall(self):
            return [(20,), (21,), (20,)]

    class Connection:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    cursor = Cursor()
    connection = Connection()
    monkeypatch.setattr(svc, "_connect_ready", lambda: (connection, cursor))

    referred = svc._referred_user_ids_for_user(10)

    assert referred == {20, 21}
    assert "FROM airdrop_referrals" in cursor.sql
    assert "FROM users" in cursor.sql
    assert "referred_by=%s" in cursor.sql
    assert cursor.params == (10, 10)
    assert connection.closed is True


def test_invite_screen_displays_unified_count_and_stable_link(monkeypatch):
    monkeypatch.setattr(svc, "get_or_create_referral_code", lambda user_id: "daStableCode")
    monkeypatch.setattr(
        svc,
        "get_referral_summary",
        lambda user_id: {
            "invited": 3,
            "active_referrals": 1,
            "pending_points": 20,
            "confirmed_points": 250,
        },
    )

    text = svc.format_invite_friends(10, "DeepAlphaAI_bot", "ru")

    assert "https://t.me/DeepAlphaAI_bot?start=ref_daStableCode" in text
    assert "Приглашено: 3" in text
    assert "Активных рефералов: 1" in text
