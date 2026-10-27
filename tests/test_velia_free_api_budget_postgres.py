"""Run against an isolated PostgreSQL test database, never DATABASE_URL."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import psycopg2

from services import velia_free_api_budget_service as budget


@pytest.fixture
def pg(monkeypatch):
    uri = os.getenv("TEST_DATABASE_URL", "")
    if not uri:
        pytest.skip("isolated TEST_DATABASE_URL required")
    monkeypatch.setattr(budget, "get_connection", lambda: psycopg2.connect(uri, connect_timeout=5))
    return uri


def test_concurrent_workers_and_restart_cannot_exceed_monthly_cap(pg):
    provider = "test-" + uuid.uuid4().hex
    def request(_):
        try:
            budget.reserve(provider, "dedicated-test-key", 1, 11)
            return True
        except budget.FreeAPIError as exc:
            assert exc.code == "free_api_quota_exhausted"
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request, range(36)))
    assert sum(results) == 11
    # No in-memory state is needed to enforce the cap after a new connection.
    with pytest.raises(budget.FreeAPIError, match="quota_exhausted"):
        budget.reserve(provider, "dedicated-test-key", 1, 11)
    with psycopg2.connect(pg) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT used, account FROM velia_free_api_usage WHERE provider=%s", (provider,))
        used, account = cursor.fetchone()
        assert used == 11 and "dedicated-test-key" not in account


def test_user_limit_failure_rolls_back_global_reservation(pg):
    provider = "test-" + uuid.uuid4().hex
    budget.reserve(provider, "key", 2, 10, user_id=1, daily_user_limit=2)
    with pytest.raises(budget.FreeAPIError, match="quota_exhausted"):
        budget.reserve(provider, "key", 1, 10, user_id=1, daily_user_limit=2)
    # A second user can reserve the remaining eight units. A leaked global
    # reservation from the failed daily-user check would reject this request.
    budget.reserve(provider, "key", 8, 10, user_id=2, daily_user_limit=8)
    with psycopg2.connect(pg) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT used FROM velia_free_api_usage WHERE provider=%s", (provider,))
        assert cursor.fetchone()[0] == 10
