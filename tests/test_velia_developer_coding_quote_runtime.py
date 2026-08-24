from types import SimpleNamespace

import pytest

from services import velia_agent_chat_conflict_patch as runtime


JOB = {
    "job_id": "job-1",
    "project_id": "project-1",
    "status": "planned",
    "estimated_cost_usd": 0.01,
    "total_steps": 2,
    "plan": {"summary": "Implement feature", "steps": [{"index": 1}, {"index": 2}]},
}


def _patch_router(monkeypatch, *, message, active_job):
    monkeypatch.setattr(runtime.agent_planner, "chat_agent_enabled", lambda: True)
    monkeypatch.setattr(runtime.agent_patch, "_latest_request_user_message", lambda request_id, user_id: message)
    monkeypatch.setattr(runtime.agent_planner, "active_chat_job", lambda *args: None)
    monkeypatch.setattr(runtime.agent_planner, "is_agent_request", lambda value: False)
    monkeypatch.setattr(runtime.agent_planner, "is_cancel", lambda value: False)
    monkeypatch.setattr(runtime.agent_planner, "is_status", lambda value: False)
    monkeypatch.setattr(runtime.coding_service, "is_coding_request", lambda value: "endpoint" in value.lower())
    monkeypatch.setattr(runtime.coding_service, "is_cancel", lambda value: False)
    monkeypatch.setattr(runtime.coding_service, "is_status_request", lambda value: False)
    monkeypatch.setattr(runtime.coding_service, "active_job", active_job)


def _presentation(result, **kwargs):
    value = dict(result)
    value["agent_context"] = {
        "presentation": {
            "schema_version": 2,
            "kind": "coding_plan" if result.get("reason") == "developer_coding_plan_ready" else "coding_completed",
            "summary": "Implement feature",
            "status": "planned" if result.get("reason") == "developer_coding_plan_ready" else "completed",
            "can_execute": result.get("reason") == "developer_coding_plan_ready",
            "can_cancel": result.get("reason") == "developer_coding_plan_ready",
            "execute_command": "Выполняй план" if result.get("reason") == "developer_coding_plan_ready" else "",
            "cancel_command": "Отмени план" if result.get("reason") == "developer_coding_plan_ready" else "",
            "actions": [],
            "coding": {"estimated_cost_usd": float(result.get("estimated_cost_usd") or 0.0)},
        }
    }
    return value


def test_disabled_quote_gate_preserves_existing_coding_flow(monkeypatch):
    monkeypatch.setattr(runtime, "_quote_enabled", lambda: False)
    calls = []

    def active(*args):
        calls.append("active")
        return JOB

    _patch_router(monkeypatch, message="Выполни план", active_job=active)
    monkeypatch.setattr(
        runtime.coding_quote,
        "charge_quote",
        lambda **kwargs: pytest.fail("disabled quote gate must not touch billing"),
    )
    monkeypatch.setattr(runtime.developer_presentation, "enrich_result_best_effort", _presentation)

    def original_generate(prompt, *, user_id, conversation_id, request_id=None):
        calls.append("generate")
        return {
            "ok": True,
            "provider": "velia_coding_agent",
            "reason": "developer_coding_completed",
            "request_id": request_id,
            "text": "done",
        }

    module = SimpleNamespace(generate_velia_chat_result=original_generate)
    runtime.install(module)
    result = module.generate_velia_chat_result(
        "prompt", user_id=7, conversation_id="conversation-1", request_id="request-1"
    )
    assert result["reason"] == "developer_coding_completed"
    assert "generate" in calls


def test_approval_charges_before_inner_coding_execution_and_consumes_on_success(monkeypatch):
    monkeypatch.setattr(runtime, "_quote_enabled", lambda: True)
    events = []
    active_values = iter([JOB, JOB, None])
    _patch_router(
        monkeypatch,
        message="Выполни план",
        active_job=lambda *args: next(active_values, None),
    )
    monkeypatch.setattr(runtime.coding_quote, "quote_for_job", lambda job_id: {
        "job_id": job_id,
        "user_id": 7,
        "quoted_tokens": 120,
        "balance_tokens": 500,
        "status": "pending",
    })

    def charge(**kwargs):
        events.append("charge")
        return {
            "job_id": "job-1",
            "user_id": 7,
            "quoted_tokens": 120,
            "balance_tokens": 500,
            "status": "charged",
            "charged_tokens": 120,
        }

    monkeypatch.setattr(runtime.coding_quote, "charge_quote", charge)
    monkeypatch.setattr(runtime.coding_quote, "consume_quote", lambda job_id: events.append("consume") or {})
    monkeypatch.setattr(runtime.coding_quote, "refund_quote", lambda **kwargs: events.append("refund") or {})
    monkeypatch.setattr(runtime.coding_quote, "enrich_result_with_quote", lambda result, **kwargs: result)
    monkeypatch.setattr(runtime, "_decorate_quote_presentation", lambda result, **kwargs: result)
    monkeypatch.setattr(runtime.developer_presentation, "enrich_result_best_effort", _presentation)

    def original_generate(prompt, *, user_id, conversation_id, request_id=None):
        events.append("generate")
        return {
            "ok": True,
            "provider": "velia_coding_agent",
            "reason": "developer_coding_completed",
            "request_id": request_id,
            "text": "done",
        }

    module = SimpleNamespace(generate_velia_chat_result=original_generate)
    runtime.install(module)
    result = module.generate_velia_chat_result(
        "prompt", user_id=7, conversation_id="conversation-1", request_id="request-1"
    )

    assert result["reason"] == "developer_coding_completed"
    assert events[:2] == ["charge", "generate"]
    assert "consume" in events
    assert "refund" not in events


def test_insufficient_balance_cancels_before_inner_execution(monkeypatch):
    monkeypatch.setattr(runtime, "_quote_enabled", lambda: True)
    active_values = iter([JOB, JOB])
    _patch_router(
        monkeypatch,
        message="Выполни план",
        active_job=lambda *args: next(active_values, JOB),
    )
    quote_value = {
        "job_id": "job-1",
        "user_id": 7,
        "quoted_tokens": 120,
        "balance_tokens": 50,
        "status": "insufficient",
    }
    monkeypatch.setattr(runtime.coding_quote, "quote_for_job", lambda job_id: {**quote_value, "status": "pending"})

    def insufficient(**kwargs):
        raise runtime.coding_quote.CodingQuoteError(
            "developer_coding_insufficient_tokens", status=402, quote=quote_value
        )

    monkeypatch.setattr(runtime.coding_quote, "charge_quote", insufficient)
    updates = []
    monkeypatch.setattr(runtime.coding_service, "_update_job", lambda job_id, **fields: updates.append((job_id, fields)))
    monkeypatch.setattr(runtime.coding_quote, "enrich_result_with_quote", lambda result, **kwargs: result)
    monkeypatch.setattr(runtime, "_decorate_quote_presentation", lambda result, **kwargs: result)
    monkeypatch.setattr(runtime.developer_presentation, "enrich_result_best_effort", _presentation)

    module = SimpleNamespace(
        generate_velia_chat_result=lambda *args, **kwargs: pytest.fail(
            "insufficient balance must stop before Coding Agent execution"
        )
    )
    runtime.install(module)
    result = module.generate_velia_chat_result(
        "prompt", user_id=7, conversation_id="conversation-1", request_id="request-1"
    )

    assert result["reason"] == "developer_coding_insufficient_tokens"
    assert updates[-1][1]["status"] == "cancelled"
    assert updates[-1][1]["error_code"] == "developer_coding_insufficient_tokens"


def test_failed_execution_refunds_charged_quote(monkeypatch):
    monkeypatch.setattr(runtime, "_quote_enabled", lambda: True)
    active_values = iter([JOB, JOB, None])
    _patch_router(
        monkeypatch,
        message="Выполни план",
        active_job=lambda *args: next(active_values, None),
    )
    monkeypatch.setattr(runtime.coding_quote, "quote_for_job", lambda job_id: {
        "job_id": job_id,
        "user_id": 7,
        "quoted_tokens": 120,
        "balance_tokens": 500,
        "status": "pending",
    })
    monkeypatch.setattr(runtime.coding_quote, "charge_quote", lambda **kwargs: {
        "job_id": "job-1",
        "user_id": 7,
        "quoted_tokens": 120,
        "balance_tokens": 500,
        "status": "charged",
        "charged_tokens": 120,
    })
    events = []
    monkeypatch.setattr(runtime.coding_quote, "refund_quote", lambda **kwargs: events.append("refund") or {
        "quoted_tokens": 120,
        "balance_tokens": 500,
        "status": "refunded",
    })
    monkeypatch.setattr(runtime.coding_quote, "consume_quote", lambda job_id: pytest.fail("failed execution must not consume quote"))
    monkeypatch.setattr(runtime.coding_quote, "enrich_result_with_quote", lambda result, **kwargs: result)
    monkeypatch.setattr(runtime, "_decorate_quote_presentation", lambda result, **kwargs: result)
    monkeypatch.setattr(runtime.developer_presentation, "enrich_result_best_effort", _presentation)

    def original_generate(prompt, *, user_id, conversation_id, request_id=None):
        return {
            "ok": True,
            "provider": "velia_coding_agent",
            "reason": "developer_coding_failed",
            "request_id": request_id,
            "text": "failed",
        }

    module = SimpleNamespace(generate_velia_chat_result=original_generate)
    runtime.install(module)
    result = module.generate_velia_chat_result(
        "prompt", user_id=7, conversation_id="conversation-1", request_id="request-1"
    )
    assert result["reason"] == "developer_coding_failed"
    assert events == ["refund"]


def test_quote_summary_is_visible_to_current_android_schema(monkeypatch):
    monkeypatch.setattr(
        runtime.developer_presentation.presentation_store,
        "persist_context_best_effort",
        lambda **kwargs: None,
    )
    result = {
        "agent_context": {
            "presentation": {
                "schema_version": 2,
                "kind": "coding_plan",
                "summary": "Добавить endpoint и тесты.",
                "can_execute": True,
                "can_cancel": True,
                "execute_command": "Выполняй план",
                "cancel_command": "Отмени план",
                "coding": {},
            }
        }
    }
    decorated = runtime._decorate_quote_presentation(
        result,
        quote={"quoted_tokens": 120, "balance_tokens": 500, "status": "pending"},
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
        message="Добавь endpoint",
    )
    summary = decorated["agent_context"]["presentation"]["summary"]
    assert "120 VELIA-токенов" in summary
    assert "Баланс: 500" in summary
    assert "Выполняем?" in summary
