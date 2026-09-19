import os
import uuid

import pytest

from services import velia_chat_service as chat
from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_director_service as director
from services import velia_research_literature_service as literature
from services import velia_research_reasoning_service as reasoning


def test_director_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_LITERATURE_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_REASONING_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_AUTONOMY_ENABLED", "true")
    monkeypatch.delenv("VELIA_RESEARCH_DIRECTOR_ENABLED", raising=False)
    assert director.enabled() is False
    state = director.status()
    assert state["experiment_execution"] is False
    assert state["arbitrary_shell"] is False
    assert state["arbitrary_url_fetch"] is False


@pytest.fixture
def postgres(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration is required in CI")
    import psycopg2

    schema = "director_test_" + uuid.uuid4().hex
    with psycopg2.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        return psycopg2.connect(url, options=f"-c search_path={schema}")

    monkeypatch.setattr(projects, "get_connection", connect)
    monkeypatch.setattr(chat, "get_connection", connect)
    monkeypatch.setattr(projects, "_READY", False)
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_LITERATURE_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_REASONING_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_AUTONOMY_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_DIRECTOR_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_EXPERIMENT_EXECUTION_ENABLED", "false")

    chat.ensure_velia_chat_tables()
    center.ensure_tables()
    literature.ensure_tables()
    reasoning.ensure_tables()
    director.ensure_tables()
    try:
        yield connect
    finally:
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _mission(uid, key="director-mission-0001", goal="Study battery materials"):
    return center.create_mission(uid, {"goal": goal, "title": "Research"}, key)


def test_enqueue_is_single_active_run_and_owner_scoped(postgres):
    mission = _mission(41)
    first = director.enqueue(41, mission["id"], 2)
    second = director.enqueue(41, mission["id"], 3)
    assert second["id"] == first["id"]
    assert first["status"] == "queued"
    assert director.get_run(41, first["id"])["id"] == first["id"]
    with pytest.raises(projects.ProjectError, match="research_run_not_found"):
        director.get_run(42, first["id"])


def test_worker_claims_and_completes_high_confidence_cycle(postgres, monkeypatch):
    mission = _mission(43, "director-mission-0002")
    searches = []
    syntheses = []

    monkeypatch.setattr(literature, "collect", lambda uid, mid, query, limit: searches.append(query) or {
        "cached": False,
        "sources": [{"id": "s1"}],
    })
    monkeypatch.setattr(reasoning, "synthesize", lambda uid, mid, limit: syntheses.append(mid) or {
        "id": "syn-1",
        "cached": False,
        "result": {
            "confidence": "high",
            "contradictions": [],
            "open_questions": [],
        },
    })

    queued = director.enqueue(43, mission["id"], 3)
    claimed = director.claim_next("worker-a")
    assert claimed["id"] == queued["id"]
    assert claimed["status"] == "running"
    result = director.execute_claimed(claimed, "worker-a")
    assert result["status"] == "completed"
    assert result["completed_iterations"] == 1
    assert result["stop_reason"] == "evidence_sufficient"
    assert searches == [mission["goal"]]
    assert syntheses == [mission["id"]]


def test_open_questions_drive_bounded_second_iteration(postgres, monkeypatch):
    mission = _mission(44, "director-mission-0003")
    searches = []
    calls = {"n": 0}

    monkeypatch.setattr(literature, "collect", lambda uid, mid, query, limit: searches.append(query) or {
        "cached": False,
        "sources": [],
    })

    def synthesize(uid, mid, limit):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "id": "syn-1",
                "cached": False,
                "result": {
                    "confidence": "moderate",
                    "contradictions": ["conflict"],
                    "open_questions": ["Question one?", "Question two?", "Question three?"],
                },
            }
        return {
            "id": "syn-2",
            "cached": False,
            "result": {
                "confidence": "low",
                "contradictions": ["still unresolved"],
                "open_questions": ["Further question?"],
            },
        }

    monkeypatch.setattr(reasoning, "synthesize", synthesize)

    queued = director.enqueue(44, mission["id"], 2)
    claimed = director.claim_next("worker-b")
    assert claimed["id"] == queued["id"]
    result = director.execute_claimed(claimed, "worker-b")
    assert result["status"] == "completed"
    assert result["completed_iterations"] == 2
    assert result["stop_reason"] == "iteration_limit"
    assert searches == [mission["goal"], "Question one?", "Question two?"]


def test_cancelled_run_is_not_claimable(postgres):
    mission = _mission(45, "director-mission-0004")
    queued = director.enqueue(45, mission["id"], 2)
    cancelled = director.cancel_run(45, queued["id"])
    assert cancelled["status"] == "cancelled"
    assert director.claim_next("worker-c") is None


def test_stale_lease_is_reclaimed(postgres):
    mission = _mission(46, "director-mission-0005")
    queued = director.enqueue(46, mission["id"], 1)
    first = director.claim_next("worker-old")
    assert first["id"] == queued["id"]

    with projects.transaction() as cur:
        cur.execute("""UPDATE velia_research_autonomy_runs
            SET lease_until=NOW()-INTERVAL '10 seconds'
            WHERE run_id=%s""", (queued["id"],))

    second = director.claim_next("worker-new")
    assert second["id"] == queued["id"]
    assert second["status"] == "running"
    with projects.transaction() as cur:
        cur.execute("SELECT attempt_count,worker_id FROM velia_research_autonomy_runs WHERE run_id=%s", (queued["id"],))
        row = cur.fetchone()
        assert row["attempt_count"] == 2
        assert row["worker_id"] == "worker-new"


def test_restricted_defensive_mission_can_only_do_read_only_director_cycle(postgres, monkeypatch):
    mission = _mission(
        47,
        "director-mission-0006",
        "Исследуй обнаружение ransomware и защиту корпоративной сети",
    )
    assert mission["safety"]["read_only_only"] is True

    monkeypatch.setattr(literature, "collect", lambda *a, **k: {
        "cached": False,
        "sources": [],
    })
    monkeypatch.setattr(reasoning, "synthesize", lambda *a, **k: {
        "id": "syn-defensive",
        "cached": False,
        "result": {
            "confidence": "high",
            "contradictions": [],
            "open_questions": [],
        },
    })

    queued = director.enqueue(47, mission["id"], 1)
    claimed = director.claim_next("worker-defense")
    result = director.execute_claimed(claimed, "worker-defense")
    assert result["status"] == "completed"

    planned = center.plan_experiment(
        47,
        mission["id"],
        {"method": "Analyze a static defensive dataset"},
    )
    assert planned["execution_ready"] is False
    assert planned["safety"]["read_only_only"] is True


def test_abandoned_run_hits_attempt_ceiling(postgres):
    mission = _mission(48, "director-mission-0007")
    queued = director.enqueue(48, mission["id"], 1)
    claimed = director.claim_next("worker-old")
    assert claimed["id"] == queued["id"]

    with projects.transaction() as cur:
        cur.execute("""UPDATE velia_research_autonomy_runs
            SET attempt_count=%s,lease_until=NOW()-INTERVAL '10 seconds'
            WHERE run_id=%s""", (director.MAX_ATTEMPTS, queued["id"]))

    assert director.claim_next("worker-new") is None
    failed = director.get_run(48, queued["id"])
    assert failed["status"] == "failed"
    assert failed["stop_reason"] == "attempt_limit"
    assert failed["error_code"] == "research_run_attempt_limit"
