import hashlib
import json
import os
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services import velia_superagent_chat_service as agent

ROOT = Path(__file__).resolve().parents[1]
PROJECT = {"id": "project-1", "repository_id": 42, "selected_branch": "develop"}
JOB = {"job_id": "job-1", "user_id": 7, "conversation_id": "conversation-1", "project_id": "project-1",
    "base_branch": "develop", "goal": "Fix endpoint", "plan": {"title": "Fix endpoint"}}


def test_real_core_process_lifecycle_and_credential_isolation(monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", "--invalid-option-that-must-not-reach-child")
    first = agent.run_transition("start", JOB, PROJECT)
    assert first["authorized"] is True
    ready = agent.run_transition("ready", JOB, PROJECT, state=first["state"], summary="Draft PR ready; CI pending")
    assert ready["stage"] == {"stage": "ended"}
    assert ready["state"]["tickets"][0]["ended"]["outcome"] == "completed"


def test_persisted_state_cannot_be_moved_to_another_account():
    first = agent.run_transition("start", JOB, PROJECT)
    with pytest.raises(agent.SuperagentError):
        agent.run_transition("ready", {**JOB, "user_id": 8}, PROJECT, state=first["state"])


def test_missing_runtime_fails_closed(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _: None)
    with pytest.raises(agent.SuperagentError):
        agent.run_transition("start", JOB, PROJECT)


def test_changed_branch_rejected_before_claim():
    with pytest.raises(agent.SuperagentError):
        agent.claim(JOB, {**PROJECT, "selected_branch": "main"})


def test_normal_chat_never_enters_developer_router(monkeypatch):
    monkeypatch.setenv("VELIA_SUPERAGENT_CHAT_ENABLED", "true")
    assert agent.route_free(SimpleNamespace(), user_id=7, conversation_id="c", content="Привет!",
        chat_mode="flash", request_id="r") is None


@pytest.mark.parametrize("mode", ["flash", "quantum", "pro"])
def test_coding_chat_uses_owned_repository_and_selected_context(monkeypatch, mode):
    from services import velia_developer_chat_runtime_patch as developer
    from services import velia_developer_project_service as projects
    monkeypatch.setenv("VELIA_SUPERAGENT_CHAT_ENABLED", "true")
    monkeypatch.setattr(projects, "developer_enabled", lambda: True)
    monkeypatch.setattr(projects, "list_projects", lambda user: [PROJECT] if user == 7 else [])
    monkeypatch.setattr(developer, "_bound_project", lambda *args: None)
    bindings = []
    monkeypatch.setattr(developer, "_bind_project", lambda *args: bindings.append(args))
    def coded(**kwargs):
        assert agent.selected_mode() == mode
        assert kwargs["project"]["id"] == "project-1"
        return {"ok": True, "text": "Plan", "reason": "developer_coding_plan_ready"}
    monkeypatch.setattr(developer, "_coding_chat_result", coded)
    chat = SimpleNamespace(_superagent_quote_generate=lambda prompt, generator, **kwargs: generator(prompt))
    result = agent.route_free(chat, user_id=7, conversation_id="c", content="Исправь баг в репозитории",
        chat_mode=mode, request_id="r")
    assert result["provider"] == "velia-" + mode and not result["fallback_used"]
    assert bindings == [(7, "c", "project-1")]
    assert agent.selected_mode() is None


@pytest.mark.parametrize("mode", ["flash", "quantum", "pro"])
def test_selected_coding_transport_is_one_attempt_no_tools_or_paid_fallback(monkeypatch, mode):
    import velia_free_models as free_models
    from services import velia_quantum_service as quantum
    from services import velia_developer_coding_service as coding
    import velia_quantum_transport as transport
    from velia_quantum_provider import QuantumConfig, ZAI_URL
    config = QuantumConfig("zai", ZAI_URL, "glm-4.5-flash" if mode == "flash" else "glm-4.7-flash", "fixture-key", 8192,
        thinking_enabled=mode == "pro")
    monkeypatch.setattr(free_models, "configuration", lambda value: config if value == mode else None)
    @contextmanager
    def client(_config):
        yield object()
    monkeypatch.setattr(quantum, "_provider_client", client)
    calls = []
    @contextmanager
    def complete(client, selected, body, **kwargs):
        calls.append((selected, body, kwargs))
        delta = {"choices": [{"delta": {"content": '{"summary":"ok"}'}, "finish_reason": "stop"}]}
        response = SimpleNamespace(headers={"Content-Type": "text/event-stream"},
            iter_content=lambda _: iter([("data: " + json.dumps(delta) + "\n\ndata: [DONE]\n\n").encode()]))
        yield response
    monkeypatch.setattr(transport, "completion", complete)
    monkeypatch.setattr(coding.kimi_gateway, "call_kimi", lambda **kwargs: pytest.fail("paid fallback forbidden"))
    with agent.model_context(mode):
        result = coding._model_call(prompt="Return JSON", feature="test", request_id="r", user_id=7, max_tokens=1400, timeout=30)
    assert json.loads(result["text"]) == {"summary": "ok"}
    assert len(calls) == 1
    selected, body, kwargs = calls[0]
    assert selected == config and body["model"] == config.model
    assert body["max_tokens"] == 1400 and "tools" not in body
    assert kwargs == {"timeout": (10, 30), "retry_overload": False}
    assert body["thinking"]["type"] == ("enabled" if mode == "pro" else "disabled")


def test_approval_cannot_switch_model_before_any_github_write(monkeypatch):
    from services import velia_developer_coding_service as coding
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_ENABLED", "true")
    monkeypatch.setattr(coding, "active_job", lambda *args: {**JOB, "status": "planned", "plan": {"executor_mode": "flash"}})
    monkeypatch.setattr(coding.write_service, "require_write_permissions", lambda *args: pytest.fail("no GitHub access allowed"))
    with agent.model_context("pro"), pytest.raises(coding.DeveloperCodingError, match="developer_coding_model_mismatch"):
        coding.execute_job(user_id=7, conversation_id="conversation-1", project=PROJECT)


def test_chat_approval_runs_actual_core_and_bounded_executor_to_pr(monkeypatch):
    from services import velia_developer_coding_service as coding
    from services import velia_developer_chat_runtime_patch as developer
    monkeypatch.setenv("VELIA_SUPERAGENT_CHAT_ENABLED", "true")
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_ENABLED", "true")
    project = {**PROJECT, "repository_full_name": "owner/repo"}
    job = {**JOB, "status": "planned", "total_steps": 1, "estimated_cost_usd": 0,
        "plan": {"title": "Fix endpoint", "executor_mode": "pro", "steps": [{"index": 1, "title": "Fix", "files": ["app.py"]}]}}
    monkeypatch.setattr(coding, "active_job", lambda *args: job.copy())
    monkeypatch.setattr(coding.write_service, "require_write_permissions", lambda *args: {})
    events = []
    states = []
    def claim(value, repository):
        events.append("claim")
        return agent.run_transition("start", value, repository)["state"]
    def report(action, value, repository, state, summary):
        events.append(action)
        result = agent.run_transition(action, value, repository, state=state, summary=summary)
        states.append(result["state"])
        return result["state"]
    monkeypatch.setattr(agent, "claim", claim)
    monkeypatch.setattr(agent, "report", report)
    monkeypatch.setattr(coding, "_update_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(coding, "_work_branch", lambda _: "velia/superagent-test")
    monkeypatch.setattr(coding.write_service, "create_work_branch", lambda *args: events.append("branch"))
    monkeypatch.setattr(coding, "_execute_step", lambda **kwargs: {"title": "Fix", "summary": "Fix endpoint", "commit_sha": "abc", "files": ["app.py"]})
    def pull(*args, **kwargs):
        events.append("pr")
        assert kwargs["branch"].startswith("velia/")
        return {"number": 7, "url": "https://github.com/owner/repo/pull/7", "draft": True}
    monkeypatch.setattr(coding.write_service, "create_draft_pull_request", pull)
    monkeypatch.setattr(coding.write_service, "commit_status", lambda *args: {"total": 0, "checks": []})
    with agent.model_context("pro"):
        result = developer._coding_chat_result(user_id=7, conversation_id="conversation-1", request_id="r",
            message="Выполняй план", project=project)
    assert result["reason"] == "developer_coding_completed"
    assert "https://github.com/owner/repo/pull/7" in result["text"]
    assert events == ["claim", "branch", "progress", "pr", "ready"]
    assert states[-1]["tickets"][0]["ended"]["outcome"] == "completed"


def test_other_active_agent_blocks_new_coding_plan(monkeypatch):
    from services import velia_agent_chat_planner_service as planner
    monkeypatch.setenv("VELIA_SUPERAGENT_CHAT_ENABLED", "true")
    monkeypatch.setattr(planner, "chat_agent_enabled", lambda: True)
    monkeypatch.setattr(planner, "active_chat_job", lambda *args: {"job_id": "other"})
    result = agent.route_free(SimpleNamespace(), user_id=7, conversation_id="c", content="Исправь код",
        chat_mode="pro", request_id="r")
    assert result["reason"] == "developer_superagent_task_conflict"


def test_vendored_source_and_bundle_integrity():
    root = ROOT / "agent_core/superagent/chat/vendor"
    provenance = json.loads((root / "provenance.json").read_text())
    assert provenance["commit"] == "6337fc823ce20251c8de8085948165c81aaa701e"
    for name, digest in provenance["sha256"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    subprocess.run(["node", "--test", str(root.parent / "bridge.test.mjs")], check=True, capture_output=True)


def test_postgres_atomic_claim_reports_and_duplicate_rejection(monkeypatch):
    """Railway's mandatory backend CI provides PostgreSQL, including on PR builds."""
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration")
    import psycopg2
    from services import velia_developer_coding_service as coding
    schema = "sa_" + uuid.uuid4().hex
    def connect():
        conn = psycopg2.connect(dsn)
        with conn.cursor() as cursor:
            cursor.execute('SET search_path TO "' + schema + '"')
        return conn
    admin = psycopg2.connect(dsn)
    try:
        with admin.cursor() as cursor:
            cursor.execute('CREATE SCHEMA "' + schema + '"')
        admin.commit()
        monkeypatch.setattr(agent, "get_connection", connect)
        monkeypatch.setattr(coding, "get_connection", connect)
        monkeypatch.setattr(coding, "_SCHEMA_READY", False)
        coding.ensure_coding_tables()
        conn = connect()
        with conn.cursor() as cursor:
            cursor.execute("""INSERT INTO velia_developer_coding_jobs
                (job_id,user_id,conversation_id,project_id,goal,base_branch)
                VALUES (%s,%s,%s,%s,%s,%s)""", tuple(JOB[k] for k in
                ("job_id", "user_id", "conversation_id", "project_id", "goal", "base_branch")))
        conn.commit()
        conn.close()
        # Create the schema before racing two workers: DDL is not the concurrency mechanism.
        state = agent.claim(JOB, PROJECT)
        conn = connect()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM velia_superagent_chat_tasks")
            cursor.execute("UPDATE velia_developer_coding_jobs SET status='planned'")
        conn.commit()
        conn.close()
        def attempt():
            try:
                return agent.claim(JOB, PROJECT)
            except agent.SuperagentError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            states = list(pool.map(lambda _: attempt(), range(2)))
        assert sum(value is not None for value in states) == 1
        state = next(value for value in states if value is not None)
        assert agent.report("progress", JOB, PROJECT, state, "Commit abc")["tickets"][0]["assignments"][0]["report"]["summary"] == "Commit abc"
        with pytest.raises(agent.SuperagentError):
            agent.report("progress", {**JOB, "user_id": 8}, PROJECT, state, "cross-account")
        ready = agent.report("ready", JOB, PROJECT, state, "Draft PR ready")
        assert ready["tickets"][0]["ended"]["outcome"] == "completed"
        with pytest.raises(agent.SuperagentError):
            agent.claim(JOB, PROJECT)
    finally:
        with admin.cursor() as cursor:
            cursor.execute('DROP SCHEMA "' + schema + '" CASCADE')
        admin.commit()
        admin.close()
