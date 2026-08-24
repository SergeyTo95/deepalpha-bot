from types import SimpleNamespace

from services import velia_agent_chat_conflict_patch as conflict_patch
from services import velia_agent_chat_planner_service as agent_planner
from services import velia_agent_chat_runtime_patch as agent_patch
from services import velia_developer_coding_service as coding_service


def test_overlapping_task_and_coding_request_reaches_existing_quote_gate(monkeypatch):
    """Agent Core must not swallow a coding request that also sounds like a task."""
    message = "Создай задачу: исправь баг авторизации"
    developer_calls = []

    def developer_generate(prompt, *, user_id, conversation_id, request_id=None):
        developer_calls.append((prompt, user_id, conversation_id, request_id))
        return {
            "ok": True,
            "text": "Технический план готов.",
            "provider": "velia_coding_agent",
            "model": "coding-agent-v1",
            "reason": "developer_coding_plan_ready",
            "request_id": str(request_id or ""),
            "estimated_cost_usd": 0.01,
            "developer_context": {"read_only": True, "write_pending_approval": True},
        }

    module = SimpleNamespace(generate_velia_chat_result=developer_generate)

    monkeypatch.setattr(agent_planner, "chat_agent_enabled", lambda: True)
    monkeypatch.setattr(agent_patch, "_latest_request_user_message", lambda *_args: message)

    def forbidden_agent_plan(*_args, **_kwargs):
        raise AssertionError("Agent Core planner must not own an explicit coding request")

    monkeypatch.setattr(agent_planner, "create_chat_plan", forbidden_agent_plan)
    monkeypatch.setattr(agent_planner, "active_chat_job", lambda *_args: None)

    coding_job = {
        "job_id": "coding-1",
        "project_id": "project-1",
        "status": "awaiting_confirmation",
        "estimated_cost_usd": 0.01,
        "total_steps": 2,
        "plan": {"steps": [{"id": "s1"}, {"id": "s2"}]},
    }
    active_job_calls = 0

    def active_job(*_args):
        nonlocal active_job_calls
        active_job_calls += 1
        # Conflict preflight and pre-execution quote check see no pre-existing job.
        # After the inner Coding Agent creates its read-only plan, the quote layer
        # must discover that new job and price it.
        return coding_job if active_job_calls >= 3 else None

    monkeypatch.setattr(coding_service, "active_job", active_job)
    monkeypatch.setattr(conflict_patch, "_quote_enabled", lambda: True)
    monkeypatch.setattr(
        conflict_patch.coding_quote,
        "reconcile_quotes_for_user",
        lambda **_kwargs: None,
    )

    quote_calls = []

    def create_quote(*, user_id, conversation_id, job):
        quote_calls.append((user_id, conversation_id, job["job_id"]))
        return {
            "job_id": job["job_id"],
            "user_id": user_id,
            "conversation_id": conversation_id,
            "quoted_tokens": 120,
            "balance_tokens": 500,
            "status": "pending",
        }

    monkeypatch.setattr(conflict_patch.coding_quote, "create_quote", create_quote)
    monkeypatch.setattr(
        conflict_patch.coding_quote,
        "charge_quote",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("planning/quoting must never charge tokens")
        ),
    )
    monkeypatch.setattr(
        conflict_patch.coding_quote,
        "quote_text",
        lambda _message, quote: (
            f"Стоимость выполнения всего плана — {quote['quoted_tokens']} VELIA-токенов. "
            f"Баланс: {quote['balance_tokens']}. Выполняем?"
        ),
    )
    monkeypatch.setattr(
        conflict_patch.coding_quote,
        "enrich_result_with_quote",
        lambda result, **_kwargs: result,
    )
    monkeypatch.setattr(
        conflict_patch.developer_presentation,
        "enrich_result_best_effort",
        lambda result, **_kwargs: result,
    )

    # Match production wrapper order: Developer/Coding -> Agent Core -> conflict/quote.
    agent_patch.install(module)
    conflict_patch.install(module)

    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["reason"] == "developer_coding_plan_ready"
    assert developer_calls == [("prompt", 7, "conversation-1", "request-1")]
    assert quote_calls == [(7, "conversation-1", "coding-1")]
    assert "120 VELIA-токенов" in result["text"]
    assert "Баланс: 500" in result["text"]
