import asyncio
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from copy import deepcopy

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import admin_routes
from services import velia_flash_service as flash
from services import velia_model_lab_routes as routes
from services import velia_model_lab_service as lab
from services import velia_admin_security_service as security


@pytest.mark.parametrize("text,outcome", [("136", "passed"), ("136.", "passed"),
    ("135", "failed"), ("Не 136, а 135", "failed"), ("Ответ: 136", "failed")])
def test_exact_checker_does_not_accept_substrings(text, outcome):
    assert lab.evaluate(lab.builtin_cases()[0], {"ok": True, "text": text}) == outcome


@pytest.mark.parametrize("text,outcome", [('{"count":7,"name":"Мария"}', "passed"),
    ('{"count":7.0,"name":"Мария"}', "failed"),
    ('{"count":7,"name":"Мария","extra":1}', "failed"), ("```json\n{}\n```", "failed")])
def test_json_checker_checks_types_and_full_instructions(text, outcome):
    assert lab.evaluate(lab.builtin_cases()[4], {"ok": True, "text": text}) == outcome


def test_open_answers_and_network_errors_are_not_auto_passed():
    case = lab.builtin_cases()[-1]
    assert lab.evaluate(case, {"ok": True, "text": "Неизвестно"}) == "review"
    assert lab.evaluate(case, {"ok": False, "text": ""}) == "error"
    assert lab.evaluate(lab.builtin_cases()[0], {"ok": True, "text": "136", "finish_reason": "length"}) == "error"
    metrics = lab.summarize([
        {"outcome": "passed", "check_type": "exact", "latency_ms": 10},
        {"outcome": "error", "check_type": "exact", "latency_ms": 20},
        {"outcome": "error", "check_type": "review", "latency_ms": 20},
        {"outcome": "review", "check_type": "review", "review_verdict": "passed", "latency_ms": 30},
    ])
    assert metrics["automatic_score"] == 50
    assert metrics["automatic_total"] == 2
    assert metrics["reviewed_passed"] == 1
    assert metrics["error"] == 2


def test_comparison_rejects_different_exams_or_generation():
    left = {"kind": "benchmark", "status": "succeeded", "metrics": {"automatic_score": 50},
        "config": {"suite_digest": "exam1", "profile": {"generation": {"thinking": False}, "client_digest": "client1"}}}
    right = deepcopy(left)
    right["metrics"]["automatic_score"] = 75
    comparison = lab.compare_runs(left, right)
    assert comparison["delta"] == 25
    assert not comparison["revision_recorded"]
    right["config"]["suite_digest"] = "exam2"
    assert not lab.compare_runs(left, right)["eligible"]
    right = deepcopy(left)
    right["config"]["profile"]["generation"]["thinking"] = True
    assert not lab.compare_runs(left, right)["eligible"]
    right = deepcopy(left)
    right["status"] = "queued"
    assert not lab.compare_runs(left, right)["eligible"]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://prismml.com.evil.test/a", "https://user:pass@prismml.com/a", "https://[bad", "http://arxiv.org/a"])
def test_source_links_reject_unsafe_or_unrelated_urls(url):
    assert not lab._primary_url(url)


@pytest.mark.parametrize("operation,args", [(lab.snapshot, ()), (lab.get_run, ("id",)),
    (lab.enqueue, ("benchmark", "long enough goal", "label", str(uuid.uuid4()))),
    (lab.export_dataset, ()), (lab.cancel, ("id",)), (lab.add_example, ("p", "a", "train", True))])
def test_service_operations_enforce_owner(monkeypatch, operation, args):
    monkeypatch.setenv("ADMIN_ID", "123")
    with pytest.raises(PermissionError):
        operation(124, *args)


def _session(owner=123):
    return {"admin_user_id": owner, "csrf_token_hash": hashlib.sha256(b"csrf-good").hexdigest()}


def _request(monkeypatch, method, path, *, cookie="", data=None):
    monkeypatch.setenv("ADMIN_ID", "123")
    monkeypatch.setenv("VELIA_MODEL_LAB_EMBEDDED_WORKER_ENABLED", "false")
    monkeypatch.setattr(admin_routes, "get_admin_session", lambda raw: _session() if raw == "valid" else None)

    async def run():
        app = web.Application()
        admin_routes.setup_admin_routes(app)
        async with TestClient(TestServer(app)) as client:
            response = await client.request(method, path, data=data,
                headers={"Cookie": cookie} if cookie else {}, allow_redirects=False)
            return response.status, await response.text(), dict(response.headers)
    return asyncio.run(run())


@pytest.mark.parametrize("method,path", [("GET", "/admin/research"),
    ("GET", "/admin/research/dataset.jsonl"), ("GET", "/admin/research/abc"),
    ("POST", "/admin/research/runs"), ("POST", "/admin/research/abc/cancel"),
    ("POST", "/admin/research/abc/review/answer"), ("POST", "/admin/research/examples"),
    ("POST", "/admin/research/examples/abc/approval")])
def test_all_lab_routes_require_existing_owner_session(monkeypatch, method, path):
    status, _, headers = _request(monkeypatch, method, path)
    assert status == (302 if method == "GET" else 401)
    if method == "GET":
        assert headers["Location"] == "/admin/login"


def test_mutations_require_csrf_and_do_not_enqueue_when_denied(monkeypatch):
    def forbidden(*args):
        pytest.fail("Unauthorized request reached the job queue")
    monkeypatch.setattr(lab, "enqueue", forbidden)
    status, _, _ = _request(monkeypatch, "POST", "/admin/research/runs",
        cookie="velia_admin_session=valid", data={"_csrf": "wrong"})
    assert status == 403


def test_non_owner_session_is_rejected_before_reading_results(monkeypatch):
    monkeypatch.setattr(admin_routes, "_current_admin", lambda request: _session(124))
    monkeypatch.setattr(lab, "snapshot", lambda owner: pytest.fail("Non-owner accessed lab data"))
    status, _, _ = _request(monkeypatch, "GET", "/admin/research", cookie="velia_admin_session=valid")
    assert status == 403


def test_owner_ui_escapes_saved_labels_and_uses_private_headers(monkeypatch):
    data = {"runs": [{"id": "abc", "label": "<script>alert(1)</script>", "kind": "benchmark",
        "status": "queued", "report": {}, "created_at": "today"}], "examples": [], "worker": {"alive": False},
        "capabilities": {"enabled": True, "flash": False, "search": False, "teacher": False,
        "teacher_provider": "kimi", "profile": {"model": "velia-flash", "revision": ""}}}
    monkeypatch.setattr(lab, "snapshot", lambda owner: data)
    status, body, headers = _request(monkeypatch, "GET", "/admin/research", cookie="velia_admin_session=valid; velia_admin_csrf=csrf-good")
    assert status == 200
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "name='_csrf' value='csrf-good'" in body
    assert headers["Cache-Control"] == "no-store"
    assert "Обучение весов и выпуск новой модели пока не подключены" in body


@pytest.fixture
def database(monkeypatch):
    """Optional real PostgreSQL integration; never uses production DATABASE_URL."""
    url = os.getenv("VELIA_MODEL_LAB_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set VELIA_MODEL_LAB_TEST_DATABASE_URL to an isolated PostgreSQL database")
    import psycopg2
    from psycopg2 import sql
    schema = "lab_test_" + uuid.uuid4().hex
    admin = psycopg2.connect(url, connect_timeout=5)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA {} ").format(sql.Identifier(schema)))
    admin.commit()
    admin.close()

    def connect():
        conn = psycopg2.connect(url, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SET search_path TO {} ").format(sql.Identifier(schema)))
        conn.commit()
        return conn

    monkeypatch.setattr(lab, "get_connection", connect)
    monkeypatch.setattr(security, "get_connection", connect)
    monkeypatch.setattr(lab, "_ready", False)
    monkeypatch.setenv("ADMIN_ID", "123")
    monkeypatch.setenv("VELIA_MODEL_LAB_ENABLED", "true")
    monkeypatch.setenv("VELIA_FLASH_ENABLED", "true")
    monkeypatch.setenv("VELIA_FLASH_BASE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("VELIA_FLASH_API_KEY", "test-only-key")
    lab.ensure_tables()

    def execute(query, params=None):
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall() if cur.description else []
            conn.commit()
            return rows
        finally:
            conn.close()

    yield execute
    admin = psycopg2.connect(url, connect_timeout=5)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    admin.commit()
    admin.close()


def _enqueue():
    return lab.enqueue(123, "benchmark", "Проверка качества Flash перед обучением", "Flash baseline", str(uuid.uuid4()))


def test_durable_enqueue_is_idempotent_bounded_and_audited(database):
    key = str(uuid.uuid4())
    first = lab.enqueue(123, "benchmark", "Проверка качества Flash", "Baseline", key)
    assert lab.enqueue(123, "benchmark", "Проверка качества Flash", "Baseline", key) == first
    for _ in range(3):
        _enqueue()
    with pytest.raises(ValueError, match="четыре"):
        _enqueue()
    assert database("SELECT COUNT(*) FROM velia_model_lab_runs")[0][0] == 4
    assert database("SELECT COUNT(*) FROM velia_admin_audit_log")[0][0] == 4


def test_expired_worker_cannot_finish_reclaimed_or_cancelled_run(database):
    run_id = _enqueue()
    first = lab.claim_next("first")
    assert lab.claim_next("second") is None
    database("UPDATE velia_model_lab_runs SET lease_until=NOW()-INTERVAL '1 second' WHERE id=%s", (run_id,))
    second = lab.claim_next("second")
    assert second["worker_token"] != first["worker_token"]
    assert not lab._finish(first, "succeeded", {"stale": True})
    assert lab.cancel(123, run_id)
    assert not lab._finish(second, "succeeded")
    assert lab.get_run(123, run_id)["status"] == "cancelled"


def test_cancel_during_inference_discards_late_answer(database, monkeypatch):
    run_id = _enqueue()
    run = lab.claim_next("worker")
    @contextmanager
    def slot():
        yield True
    monkeypatch.setattr(lab, "_flash_slot", slot)
    def generate(messages, **kwargs):
        lab.cancel(123, run_id)
        return {"ok": True, "text": "136", "model": "velia-flash"}
    monkeypatch.setattr(flash, "generate", generate)
    assert lab.execute_claimed(run, "worker") == "cancelled"
    assert lab.get_run(123, run_id)["answers"] == []


def test_benchmark_persists_actual_outputs_and_separates_manual_review(database, monkeypatch):
    cases = [lab.builtin_cases()[i] for i in (0, 4, 9)]
    monkeypatch.setattr(lab, "builtin_cases", lambda: deepcopy(cases))
    @contextmanager
    def slot():
        yield True
    monkeypatch.setattr(lab, "_flash_slot", slot)
    responses = ["135", '{"count":7,"name":"Мария"}', "Нужен случайный A/B-тест."]
    calls = []
    def generate(messages, **kwargs):
        calls.append(deepcopy(messages))
        return {"ok": True, "text": responses[len(calls)-1], "model": "velia-flash", "provider": "bonsai", "usage": {"completion_tokens": 10}}
    monkeypatch.setattr(flash, "generate", generate)
    run_id = _enqueue()
    for i in range(3):
        database("UPDATE velia_model_lab_runs SET not_before=NOW() WHERE id=%s", (run_id,))
        assert lab.execute_claimed(lab.claim_next("worker"), "worker") == ("succeeded" if i == 2 else "queued")
    run = lab.get_run(123, run_id)
    assert [a["outcome"] for a in run["answers"]] == ["failed", "passed", "review"]
    assert run["metrics"]["automatic_score"] == 50
    assert run["metrics"]["needs_review"] == 1
    assert calls == [c["messages"] for c in cases]  # References never sent to the model.
    assert lab.review(123, run_id, run["answers"][-1]["id"], "passed", "Корректное предложение проверки")
    assert lab.get_run(123, run_id)["metrics"]["reviewed_passed"] == 1
    assert not lab.review(123, run_id, run["answers"][0]["id"], "passed", "")


def test_busy_flash_requeues_without_recording_a_failed_answer(database, monkeypatch):
    run_id = _enqueue()
    @contextmanager
    def busy():
        yield False
    monkeypatch.setattr(lab, "_flash_slot", busy)
    assert lab.execute_claimed(lab.claim_next("worker"), "worker") == "queued"
    assert lab.get_run(123, run_id)["answers"] == []


def test_changed_generation_config_stops_mixed_benchmark(database, monkeypatch):
    run_id = _enqueue()
    run = lab.claim_next("worker")
    monkeypatch.setenv("VELIA_FLASH_MAX_OUTPUT_TOKENS", "64")
    monkeypatch.setattr(flash, "generate", lambda *args, **kwargs: pytest.fail("Changed model was evaluated"))
    assert lab.execute_claimed(run, "worker") == "failed"
    assert lab.get_run(123, run_id)["error_code"] == "model_configuration_changed"


def test_dataset_export_excludes_holdout_and_unreviewed_data(database):
    lab.add_example(123, "Вопрос обучения", "Ответ обучения", "train", True)
    draft = lab.add_example(123, "Непроверенный вопрос", "Черновой ответ", "train", False)
    lab.add_example(123, "Независимый экзамен", "Контрольный ответ", "holdout", True)
    records = [json.loads(line) for line in lab.export_dataset(123).splitlines()]
    assert len(records) == 1
    assert records[0]["messages"][-1] == {"role": "assistant", "content": "Ответ обучения"}
    lab.approve_example(123, draft, True)
    assert len(lab.export_dataset(123).splitlines()) == 2
    with pytest.raises(ValueError, match="контрольные"):
        lab.add_example(123, lab.builtin_cases()[0]["messages"][-1]["content"], "136", "train", True)
    with pytest.raises(ValueError, match="уже сохранён"):
        lab.add_example(123, "Независимый экзамен", "Другой ответ", "train", True)


def test_audit_failure_rolls_back_queue_mutation(database, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(lab, "_audit", fail)
    with pytest.raises(RuntimeError):
        _enqueue()
    assert database("SELECT COUNT(*) FROM velia_model_lab_runs")[0][0] == 0


def test_teacher_capability_requires_provider_gates_and_can_fallback_to_kimi(monkeypatch):
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER_RESEARCH", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini")
    monkeypatch.delenv("GEMINI_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_BACKGROUND_ENABLED", raising=False)
    monkeypatch.setenv("KIMI_API_KEY", "kimi")
    monkeypatch.setenv("KIMI_ENABLED", "true")
    monkeypatch.setenv("KIMI_BACKGROUND_ENABLED", "true")
    caps = lab.capabilities()
    assert caps["teacher"] is True
    assert caps["teacher_provider"] == "kimi"
    monkeypatch.setenv("KIMI_BACKGROUND_ENABLED", "false")
    caps = lab.capabilities()
    assert caps["teacher"] is False
    assert caps["teacher_provider"] == "gemini"


def test_gemini_gateway_accepts_research_center_feature(monkeypatch):
    from services import gemini_gateway
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_BACKGROUND_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    assert gemini_gateway.FEATURE_FLAGS["research_center"] == "VELIA_RESEARCH_CENTER_ENABLED"
    assert gemini_gateway._precheck("research_center", True) is None


def test_research_validates_citations_and_records_no_training(database, monkeypatch):
    from services import llm_service, web_search_service
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-search")
    monkeypatch.setenv("LLM_PROVIDER_RESEARCH", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "test-teacher")
    queries = []
    def search(query, limit):
        queries.append(query)
        return [{"title": "Bonsai", "url": "https://github.com/PrismML-Eng/Bonsai-demo", "snippet": "Untrusted search snippet"}]
    monkeypatch.setattr(web_search_service, "search_web", search)
    report = {"summary": "Проверить совместимость адаптера.", "hypotheses": [{"title": "LoRA",
        "method": "Исследовать адаптер", "test": "Независимые задачи", "risk": "Рост размера", "source_ids": ["S1"]}], "unknowns": ["Формат адаптера"]}
    monkeypatch.setattr(llm_service, "_provider_result", lambda *args, **kwargs: {"ok": True, "text": json.dumps(report), "model": "teacher"})
    run_id = lab.enqueue(123, "research", "Улучшить интеллект компактного Flash", "Bonsai research", str(uuid.uuid4()))
    assert lab.execute_claimed(lab.claim_next("worker"), "worker") == "succeeded"
    run = lab.get_run(123, run_id)
    assert len(queries) == 2
    assert run["report"]["evidence_level"] == "search_snippets"
    assert run["report"]["training_performed"] is False
    report["hypotheses"][0]["source_ids"] = ["S99"]
    run_id = lab.enqueue(123, "research", "Улучшить интеллект компактного Flash", "Bad citation", str(uuid.uuid4()))
    assert lab.execute_claimed(lab.claim_next("worker"), "worker") == "failed"
    assert lab.get_run(123, run_id)["error_code"] == "invalid_research_report"


native_postgres = pytest.mark.skipif(os.getenv("VELIA_MODEL_LAB_NATIVE_POSTGRES_TESTS") != "1",
    reason="Requires ordinary multi-session PostgreSQL; enabled in model-lab CI")


@native_postgres
def test_native_two_workers_cannot_claim_the_same_run(database):
    from concurrent.futures import ThreadPoolExecutor
    run_id = _enqueue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lab.claim_next, ("first", "second")))
    successful = [r for r in claims if r]
    assert len(successful) == 1
    assert successful[0]["id"] == run_id


@native_postgres
def test_native_concurrent_enqueue_preserves_queue_cap(database):
    from concurrent.futures import ThreadPoolExecutor
    def enqueue_once(_):
        try:
            return _enqueue()
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(enqueue_once, range(8)))
    assert len([r for r in results if r]) == lab.MAX_PENDING
    assert database("SELECT COUNT(*) FROM velia_model_lab_runs")[0][0] == lab.MAX_PENDING


@native_postgres
def test_native_live_chat_lock_prevents_lab_inference(database, monkeypatch):
    run_id = _enqueue()
    conn = lab.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(hashtextextended('velia:flash:worker', 0))")
        conn.commit()
        monkeypatch.setattr(flash, "generate", lambda *args, **kwargs: pytest.fail("Lab overlapped live Flash"))
        assert lab.execute_claimed(lab.claim_next("worker"), "worker") == "queued"
        assert lab.get_run(123, run_id)["answers"] == []
    finally:
        conn.close()
