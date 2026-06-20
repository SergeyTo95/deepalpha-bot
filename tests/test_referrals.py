import sqlite3
import sys
import types

psycopg2_stub = types.ModuleType("psycopg2")
psycopg2_stub.extras = types.SimpleNamespace(RealDictCursor=object)
psycopg2_stub.errors = types.SimpleNamespace()
psycopg2_stub.connect = lambda *args, **kwargs: None
sys.modules.setdefault("psycopg2", psycopg2_stub)
sys.modules.setdefault("psycopg2.extras", psycopg2_stub.extras)
sys.modules.setdefault("psycopg2.errors", psycopg2_stub.errors)

import db.database as database


class CursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, sql, params=()):
        translated = sql.replace("%s", "?").replace("COUNT(r.user_id)::INTEGER", "COUNT(r.user_id)")
        self.cursor.execute(translated, params)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


class ConnWrapper:
    def __init__(self, conn):
        self.conn = conn

    def cursor(self, *args, **kwargs):
        return CursorWrapper(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def close(self):
        pass


class ReferralDb:
    def __init__(self):
        self.raw = sqlite3.connect(":memory:")
        self.raw.row_factory = sqlite3.Row
        self.raw.execute(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                referred_by INTEGER DEFAULT NULL,
                referral_earnings_ton REAL DEFAULT 0,
                total_referrals INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        self.raw.commit()

    def connect(self):
        return ConnWrapper(self.raw)

    def referred_by(self, user_id):
        row = self.raw.execute("SELECT referred_by FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row[0] if row else None


import pytest


@pytest.fixture()
def referral_db(monkeypatch):
    db = ReferralDb()
    monkeypatch.setattr(database, "get_connection", db.connect)
    return db


def test_new_user_with_referral_sets_relationship_and_count(referral_db):
    database.ensure_user(user_id=100, username="referrer")
    database.ensure_user(user_id=200, username="worker", referred_by=100)

    assert referral_db.referred_by(200) == 100
    assert database.get_referral_count(100) == 1


def test_existing_referred_user_start_without_referral_preserves_referrer(referral_db):
    database.ensure_user(user_id=100)
    database.ensure_user(user_id=200, referred_by=100)

    database.ensure_user(user_id=200, username="worker2", source="start_without_payload")

    assert referral_db.referred_by(200) == 100
    assert database.get_referral_count(100) == 1


def test_existing_referred_user_start_with_another_referrer_keeps_original(referral_db):
    database.ensure_user(user_id=100)
    database.ensure_user(user_id=101)
    database.ensure_user(user_id=200, referred_by=100)

    database.ensure_user(user_id=200, referred_by=101)

    assert referral_db.referred_by(200) == 100
    assert database.get_referral_count(100) == 1
    assert database.get_referral_count(101) == 0


def test_self_referral_ignored(referral_db):
    database.ensure_user(user_id=100, referred_by=100)

    assert referral_db.referred_by(100) is None
    assert database.get_referral_count(100) == 0


def test_referral_count_returns_real_users_count(referral_db):
    database.ensure_user(user_id=100)
    for uid in (201, 202, 203):
        database.ensure_user(user_id=uid, referred_by=100)

    assert database.get_referral_count(100) == 3


def test_top_referrers_helper_uses_real_relationship_count(referral_db):
    database.ensure_user(user_id=100, username="referrer")
    for uid in (201, 202, 203):
        database.ensure_user(user_id=uid, referred_by=100)
    referral_db.raw.execute("UPDATE users SET total_referrals = 0 WHERE user_id = 100")
    referral_db.raw.commit()

    rows = database.get_top_referrers(limit=10)

    assert rows[0]["user_id"] == 100 if isinstance(rows[0], dict) else rows[0][0] == 100
    total_referrals = rows[0]["total_referrals"] if isinstance(rows[0], dict) else rows[0][4]
    assert total_referrals == 3


def test_source_update_does_not_change_referral_state(referral_db):
    database.ensure_user(user_id=100)
    database.ensure_user(user_id=200, referred_by=100)

    database.ensure_user(user_id=200, source="inline_share")

    assert referral_db.referred_by(200) == 100


def test_admin_users_count_uses_total_users_table_not_small_sample(referral_db):
    for uid in range(1, 51):
        database.ensure_user(user_id=uid, username=f"u{uid}")

    assert database.count_users() == 50


def test_admin_users_page_sample_does_not_change_total(referral_db):
    for uid in range(1, 51):
        database.ensure_user(user_id=uid, username=f"u{uid}")

    page = database.get_users_page(limit=2, offset=0)

    assert len(page) == 2
    assert database.count_users() == 50


def test_legacy_mismatch_diagnostics_reports_legacy_source(referral_db):
    database.ensure_user(user_id=100, username="legacy")
    referral_db.raw.execute("UPDATE users SET total_referrals = 3 WHERE user_id = 100")
    referral_db.raw.commit()

    d = database.get_referral_diagnostics(100)

    assert d["users_referred_by_count"] == 0
    assert d["legacy_total_referrals"] == 3
    assert d["mismatch"] is True
    assert d["final_referral_count"] == 3
    assert d["source_used"] == "legacy_total_referrals"


def test_referral_table_source_preferred_when_exists(referral_db):
    referral_db.raw.execute(
        "CREATE TABLE referral_relationships (referrer_id INTEGER, user_id INTEGER, created_at TEXT)"
    )
    database.ensure_user(user_id=100, username="referrer")
    for uid in (201, 202, 203):
        database.ensure_user(user_id=uid, username=f"u{uid}")
        referral_db.raw.execute(
            "INSERT INTO referral_relationships (referrer_id, user_id, created_at) VALUES (?, ?, 'now')",
            (100, uid),
        )
    referral_db.raw.commit()

    d = database.get_referral_diagnostics(100)

    assert database.get_referral_count(100) == 3
    assert d["users_referred_by_count"] == 0
    assert d["referral_table_count"] == 3
    assert d["final_referral_count"] == 3
    assert d["source_used"] == "referral_table"


def test_top_referrers_uses_final_count_not_stale_zero(referral_db):
    database.ensure_user(user_id=100, username="referrer")
    for uid in (201, 202, 203):
        database.ensure_user(user_id=uid, referred_by=100)
    referral_db.raw.execute("UPDATE users SET total_referrals = 0 WHERE user_id = 100")
    referral_db.raw.commit()

    rows = database.get_top_referrers(limit=10)

    assert rows[0]["user_id"] == 100
    assert rows[0]["total_referrals"] == 3
    assert rows[0]["source_used"] == "users.referred_by"
