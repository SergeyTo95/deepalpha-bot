import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from db.database import migrate_watchlist_slot_purchases_idempotency_index


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="requires real PostgreSQL TEST_DATABASE_URL",
)


@pytest.fixture
def pg_cursor():
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = False
    schema = f"watchlist_migration_{uuid.uuid4().hex}"
    cur = conn.cursor()
    try:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET search_path TO "{schema}"')
        yield conn, cur
    finally:
        conn.rollback()
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()
        cur.close()
        conn.close()


def _create_table(cur):
    cur.execute(
        """
        CREATE TABLE watchlist_slot_purchases (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            idempotency_key TEXT NOT NULL,
            other_key TEXT,
            created_at TIMESTAMP DEFAULT now()
        )
        """
    )


def _unique_indexes(cur):
    cur.execute(
        """
        SELECT i.relname, array_agg(a.attname::text ORDER BY k.ordinality) AS columns
        FROM pg_index ix
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ordinality)
          ON k.ordinality <= ix.indnkeyatts
        JOIN pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = k.attnum
        WHERE ix.indrelid = 'watchlist_slot_purchases'::regclass
          AND ix.indisunique
        GROUP BY i.relname
        ORDER BY i.relname
        """
    )
    return {name: columns for name, columns in cur.fetchall()}


def _constraints(cur):
    cur.execute(
        """
        SELECT c.conname, c.contype, array_agg(a.attname::text ORDER BY k.ordinality) AS columns
        FROM pg_constraint c
        JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) ON TRUE
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
        WHERE c.conrelid = 'watchlist_slot_purchases'::regclass
        GROUP BY c.conname, c.contype
        ORDER BY c.conname
        """
    )
    return {name: (contype, columns) for name, contype, columns in cur.fetchall()}


def _assert_composite_uniqueness(conn, cur):
    cur.execute(
        "INSERT INTO watchlist_slot_purchases(user_id, idempotency_key) VALUES (1, 'abc')"
    )
    cur.execute(
        "INSERT INTO watchlist_slot_purchases(user_id, idempotency_key) VALUES (2, 'abc')"
    )
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute(
            "INSERT INTO watchlist_slot_purchases(user_id, idempotency_key) VALUES (1, 'abc')"
        )
    conn.rollback()


def test_migrates_legacy_table_constraint_without_name_array_type_crash(pg_cursor):
    conn, cur = pg_cursor
    _create_table(cur)
    cur.execute(
        "ALTER TABLE watchlist_slot_purchases ADD CONSTRAINT legacy_global_idempotency UNIQUE (idempotency_key)"
    )
    cur.execute(
        "INSERT INTO watchlist_slot_purchases(user_id, idempotency_key) VALUES (1, 'existing')"
    )

    migrate_watchlist_slot_purchases_idempotency_index(cur)

    cur.execute("SELECT user_id, idempotency_key FROM watchlist_slot_purchases")
    assert cur.fetchall() == [(1, "existing")]
    constraints = _constraints(cur)
    assert "legacy_global_idempotency" not in constraints
    assert _unique_indexes(cur)["ux_watchlist_slot_purchases_user_idempotency"] == [
        "user_id",
        "idempotency_key",
    ]
    _assert_composite_uniqueness(conn, cur)


def test_migrates_legacy_standalone_unique_index_without_name_array_type_crash(pg_cursor):
    conn, cur = pg_cursor
    _create_table(cur)
    cur.execute(
        "CREATE UNIQUE INDEX legacy_global_idempotency_idx ON watchlist_slot_purchases(idempotency_key)"
    )

    migrate_watchlist_slot_purchases_idempotency_index(cur)

    indexes = _unique_indexes(cur)
    assert "legacy_global_idempotency_idx" not in indexes
    assert indexes["ux_watchlist_slot_purchases_user_idempotency"] == [
        "user_id",
        "idempotency_key",
    ]
    _assert_composite_uniqueness(conn, cur)


def test_already_migrated_schema_is_idempotent(pg_cursor):
    conn, cur = pg_cursor
    _create_table(cur)
    cur.execute(
        "CREATE UNIQUE INDEX ux_watchlist_slot_purchases_user_idempotency ON watchlist_slot_purchases(user_id, idempotency_key)"
    )

    migrate_watchlist_slot_purchases_idempotency_index(cur)
    migrate_watchlist_slot_purchases_idempotency_index(cur)

    assert _unique_indexes(cur)["ux_watchlist_slot_purchases_user_idempotency"] == [
        "user_id",
        "idempotency_key",
    ]
    _assert_composite_uniqueness(conn, cur)


def test_unrelated_indexes_are_preserved(pg_cursor):
    conn, cur = pg_cursor
    _create_table(cur)
    cur.execute("CREATE INDEX regular_idempotency_idx ON watchlist_slot_purchases(idempotency_key)")
    cur.execute("CREATE UNIQUE INDEX unique_other_key_idx ON watchlist_slot_purchases(other_key)")
    cur.execute(
        "CREATE UNIQUE INDEX unique_user_other_idx ON watchlist_slot_purchases(user_id, other_key)"
    )

    migrate_watchlist_slot_purchases_idempotency_index(cur)

    cur.execute(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = current_schema()
          AND tablename = 'watchlist_slot_purchases'
        """
    )
    index_names = {row[0] for row in cur.fetchall()}
    assert "regular_idempotency_idx" in index_names
    indexes = _unique_indexes(cur)
    assert indexes["unique_other_key_idx"] == ["other_key"]
    assert indexes["unique_user_other_idx"] == ["user_id", "other_key"]
    assert indexes["ux_watchlist_slot_purchases_user_idempotency"] == [
        "user_id",
        "idempotency_key",
    ]
    _assert_composite_uniqueness(conn, cur)
