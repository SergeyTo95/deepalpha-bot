import os

import pytest

from services import velia_developer_coding_quote_service as quote


class _Cursor:
    def __init__(self, *, balance=500, quote_status="pending", quoted=120):
        self.balance = balance
        self.quote_status = quote_status
        self.quoted = quoted
        self.statements = []
        self._next = None

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append((sql, params))
        if "FROM velia_developer_coding_quotes WHERE job_id=%s FOR UPDATE" in sql:
            self._next = (
                "job-1",
                7,
                "conversation-1",
                self.quoted,
                500,
                self.quote_status,
                self.quoted if self.quote_status in {"charged", "consumed"} else 0,
                "coding-budget-v1",
            )
        elif "SELECT token_balance FROM users WHERE user_id=%s FOR UPDATE" in sql:
            self._next = (self.balance,)
        else:
            self._next = None

    def fetchone(self):
        value = self._next
        self._next = None
        return value

    def close(self):
        pass


class _Connection:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _job(steps):
    return {
        "job_id": "job-1",
        "total_steps": steps,
        "plan": {
            "steps": [
                {"index": index, "title": f"Step {index}", "files": [f"file{index}.py"]}
                for index in range(1, steps + 1)
            ]
        },
    }


def test_quote_is_once_per_whole_plan_and_uses_existing_execution_budgets(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_MAX_COST_PER_STEP_USD", "0.06")
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_MAX_JOB_COST_USD", "0.24")
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_USD_BUDGET_PER_TOKEN", "0.001")
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_MIN_QUOTE_TOKENS", "20")
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_MAX_QUOTE_TOKENS", "5000")

    assert quote.quote_tokens_for_job(_job(1)) == 60
    assert quote.quote_tokens_for_job(_job(2)) == 120
    assert quote.quote_tokens_for_job(_job(3)) == 180
    assert quote.quote_tokens_for_job(_job(4)) == 240
    assert quote.quote_tokens_for_job(_job(8)) == 240


def test_quote_bounds_can_be_configured_without_claiming_global_token_usd_value(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_MAX_COST_PER_STEP_USD", "0.06")
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_MAX_JOB_COST_USD", "0.24")
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_USD_BUDGET_PER_TOKEN", "0.1")
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_MIN_QUOTE_TOKENS", "25")
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_MAX_QUOTE_TOKENS", "200")
    assert quote.quote_tokens_for_job(_job(1)) == 25

    monkeypatch.setenv("VELIA_DEVELOPER_CODING_USD_BUDGET_PER_TOKEN", "0.0001")
    assert quote.quote_tokens_for_job(_job(8)) == 200


def test_charge_rechecks_locked_balance_and_debits_before_marking_quote(monkeypatch):
    cursor = _Cursor(balance=500, quoted=120)
    conn = _Connection(cursor)
    monkeypatch.setattr(quote, "ensure_coding_quote_tables", lambda: None)
    monkeypatch.setattr(quote, "get_connection", lambda: conn)

    result = quote.charge_quote(user_id=7, job_id="job-1")

    assert result["status"] == "charged"
    assert result["charged_tokens"] == 120
    sql = [statement for statement, _params in cursor.statements]
    locked_balance = next(i for i, value in enumerate(sql) if "SELECT token_balance" in value and "FOR UPDATE" in value)
    debit = next(i for i, value in enumerate(sql) if "UPDATE users SET token_balance=token_balance-%s" in value)
    mark = next(i for i, value in enumerate(sql) if "SET status='charged'" in value)
    assert locked_balance < debit < mark
    assert conn.commits == 1
    assert conn.rollbacks == 0


def test_charge_insufficient_balance_never_debits_user(monkeypatch):
    cursor = _Cursor(balance=119, quoted=120)
    conn = _Connection(cursor)
    monkeypatch.setattr(quote, "ensure_coding_quote_tables", lambda: None)
    monkeypatch.setattr(quote, "get_connection", lambda: conn)

    with pytest.raises(quote.CodingQuoteError) as exc:
        quote.charge_quote(user_id=7, job_id="job-1")

    assert exc.value.code == "developer_coding_insufficient_tokens"
    assert exc.value.quote["balance_tokens"] == 119
    assert not any("UPDATE users SET token_balance=token_balance-%s" in sql for sql, _ in cursor.statements)
    assert any("SET status='insufficient'" in sql for sql, _ in cursor.statements)
    assert conn.commits == 1


def test_consumed_quote_is_idempotent_and_never_debits_again(monkeypatch):
    cursor = _Cursor(balance=500, quote_status="consumed", quoted=120)
    conn = _Connection(cursor)
    monkeypatch.setattr(quote, "ensure_coding_quote_tables", lambda: None)
    monkeypatch.setattr(quote, "get_connection", lambda: conn)

    result = quote.charge_quote(user_id=7, job_id="job-1")

    assert result["status"] == "consumed"
    assert not any("UPDATE users SET token_balance" in sql for sql, _ in cursor.statements)
    assert conn.commits == 1


def test_refund_restores_only_a_charged_quote(monkeypatch):
    cursor = _Cursor(balance=380, quote_status="charged", quoted=120)
    conn = _Connection(cursor)
    monkeypatch.setattr(quote, "ensure_coding_quote_tables", lambda: None)
    monkeypatch.setattr(quote, "get_connection", lambda: conn)

    result = quote.refund_quote(user_id=7, job_id="job-1")

    assert result["status"] == "refunded"
    sql = [statement for statement, _params in cursor.statements]
    assert any("UPDATE users SET token_balance=token_balance+%s" in value for value in sql)
    assert any("SET status='refunded'" in value for value in sql)


def test_quote_presentation_payload_disables_execution_when_balance_is_insufficient(monkeypatch):
    monkeypatch.setattr(quote.presentation_store, "persist_context_best_effort", lambda **kwargs: None)
    result = {
        "developer_context": {"coding_job_id": "job-1"},
        "agent_context": {
            "presentation": {
                "kind": "coding_plan",
                "can_execute": True,
                "can_cancel": True,
                "execute_command": "Выполняй план",
                "cancel_command": "Отмени план",
                "coding": {"estimated_cost_usd": 0.01},
            }
        },
    }
    enriched = quote.enrich_result_with_quote(
        result,
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
        message="Добавь код",
        quote={
            "job_id": "job-1",
            "quoted_tokens": 120,
            "balance_tokens": 50,
            "status": "insufficient",
        },
    )
    presentation = enriched["agent_context"]["presentation"]
    assert presentation["can_execute"] is False
    assert presentation["can_cancel"] is False
    assert presentation["execute_command"] == ""
    assert presentation["coding"]["quoted_tokens"] == 120
    assert presentation["coding"]["balance_tokens"] == 50
    assert presentation["coding"]["quote_status"] == "insufficient"


def test_feature_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_CODING_TOKEN_QUOTE_ENABLED", raising=False)
    from services import velia_agent_chat_conflict_patch as runtime
    assert runtime._quote_enabled() is False
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_TOKEN_QUOTE_ENABLED", "true")
    assert runtime._quote_enabled() is True
