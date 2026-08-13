from __future__ import annotations

import services.velia_video_quota_service as quota_service


class FakeCursor:
    def __init__(self, fetch_rows=None) -> None:
        self.executed = []
        self._fetch_rows = list(fetch_rows or [])
        self.closed = False

    def execute(self, statement, params=None) -> None:
        self.executed.append((str(statement), params))

    def fetchone(self):
        if not self._fetch_rows:
            raise AssertionError("unexpected fetchone")
        return self._fetch_rows.pop(0)

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, fetch_rows=None) -> None:
        self.cursor_obj = FakeCursor(fetch_rows)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_configured_admin_bypasses_both_daily_video_limits(monkeypatch) -> None:
    monkeypatch.setenv("VELYON_VIDEOS_DAILY_USER_LIMIT", "1")
    monkeypatch.setenv("VELYON_VIDEOS_DAILY_GLOBAL_LIMIT", "1")
    monkeypatch.setattr(quota_service, "configured_admin_id", lambda: 777)
    conn = FakeConnection()
    monkeypatch.setattr(quota_service, "get_connection", lambda: conn)

    error, reservation_id = quota_service.reserve_self_hosted_video_capacity(777)

    assert error is None
    assert reservation_id
    statements = [statement for statement, _ in conn.cursor_obj.executed]
    assert not any("COUNT(*)" in statement for statement in statements)
    assert any("INSERT INTO velia_video_reservations" in statement for statement in statements)
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert conn.cursor_obj.closed is True
    assert conn.closed is True


def test_admin_usage_is_excluded_from_customer_global_quota(monkeypatch) -> None:
    monkeypatch.setenv("VELYON_VIDEOS_DAILY_USER_LIMIT", "5")
    monkeypatch.setenv("VELYON_VIDEOS_DAILY_GLOBAL_LIMIT", "5")
    monkeypatch.setattr(quota_service, "configured_admin_id", lambda: 777)
    conn = FakeConnection(fetch_rows=[(0,), (0,)])
    monkeypatch.setattr(quota_service, "get_connection", lambda: conn)

    error, reservation_id = quota_service.reserve_self_hosted_video_capacity(123)

    assert error is None
    assert reservation_id
    global_queries = [
        (statement, params)
        for statement, params in conn.cursor_obj.executed
        if "COUNT(*)" in statement and "user_id<>%s" in statement
    ]
    assert len(global_queries) == 1
    assert global_queries[0][1] == (777, 777)


def test_customer_user_limit_is_still_enforced(monkeypatch) -> None:
    monkeypatch.setenv("VELYON_VIDEOS_DAILY_USER_LIMIT", "1")
    monkeypatch.setenv("VELYON_VIDEOS_DAILY_GLOBAL_LIMIT", "5")
    monkeypatch.setattr(quota_service, "configured_admin_id", lambda: 777)
    conn = FakeConnection(fetch_rows=[(1,)])
    monkeypatch.setattr(quota_service, "get_connection", lambda: conn)

    error, reservation_id = quota_service.reserve_self_hosted_video_capacity(123)

    assert error == "video_daily_user_limit_exceeded"
    assert reservation_id is None
    statements = [statement for statement, _ in conn.cursor_obj.executed]
    assert not any("INSERT INTO velia_video_reservations" in statement for statement in statements)
    assert conn.commits == 1
