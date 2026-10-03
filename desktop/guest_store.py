"""Persistent anonymous trial counters; no prompts, IPs or account data stored."""
import sqlite3
from contextlib import closing

LIMIT = 30


class GuestLimitReached(Exception):
    pass


class GuestStore:
    def __init__(self, dsn=None, *, sqlite_path=None):
        self.dsn, self.sqlite_path = dsn, sqlite_path
        if not dsn and not sqlite_path:
            raise ValueError("Guest quota database is required")

    def connect(self):
        if self.sqlite_path is not None:
            return sqlite3.connect(self.sqlite_path, timeout=5)
        import psycopg2
        return psycopg2.connect(self.dsn, connect_timeout=5,
            options="-c statement_timeout=5000 -c lock_timeout=5000")

    def execute(self, cursor, sql, args=()):
        cursor.execute(sql.replace("?", "%s") if self.sqlite_path is None else sql, args)

    def initialize(self):
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS velia_web_guest_usage (
                quota_key TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0,
                CHECK (used >= 0 AND used <= 30))""")
            conn.commit()

    def remaining(self, keys):
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            self.execute(cur, "SELECT used FROM velia_web_guest_usage WHERE quota_key IN (?, ?)", tuple(keys))
            return LIMIT - max((int(row[0]) for row in cur.fetchall()), default=0)

    def reserve(self, keys):
        keys = sorted(keys)
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            try:
                if self.sqlite_path is not None:
                    cur.execute("BEGIN IMMEDIATE")
                for key in keys:
                    self.execute(cur, "INSERT INTO velia_web_guest_usage (quota_key, used) VALUES (?, 0) ON CONFLICT (quota_key) DO NOTHING", (key,))
                self.execute(cur, "SELECT used FROM velia_web_guest_usage WHERE quota_key IN (?, ?) ORDER BY quota_key" +
                    (" FOR UPDATE" if self.sqlite_path is None else ""), tuple(keys))
                rows = cur.fetchall()
                if len(rows) != 2:
                    raise RuntimeError("Invalid guest quota state")
                if max(int(row[0]) for row in rows) >= LIMIT:
                    raise GuestLimitReached()
                self.execute(cur, "UPDATE velia_web_guest_usage SET used = used + 1 WHERE quota_key IN (?, ?)", tuple(keys))
                conn.commit()
                return LIMIT - max(int(row[0]) for row in rows) - 1
            except Exception:
                conn.rollback()
                raise

    def delete_probe_keys(self, keys):
        """Operator qualification only; never exposed as a public route."""
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            self.execute(cur, "DELETE FROM velia_web_guest_usage WHERE quota_key IN (?, ?)", tuple(keys))
            conn.commit()
