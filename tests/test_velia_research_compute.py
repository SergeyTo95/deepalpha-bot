import os
import uuid

import pytest

from services import velia_chat_service as chat
from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_compute_service as compute


def test_compute_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_RESEARCH_CENTER_ENABLED", raising=False)
    monkeypatch.delenv("VELIA_RESEARCH_AUTONOMY_ENABLED", raising=False)
    monkeypatch.delenv("VELIA_RESEARCH_EXPERIMENT_EXECUTION_ENABLED", raising=False)
    monkeypatch.delenv("VELIA_RESEARCH_COMPUTE_ENABLED", raising=False)
    state = compute.status()
    assert state["enabled"] is False
    assert state["arbitrary_code"] is False
    assert state["arbitrary_shell"] is False
    assert state["arbitrary_file_access"] is False
    assert state["arbitrary_url_fetch"] is False
    assert state["dynamic_expression_eval"] is False
    assert "monte_carlo_sum" in state["allowlisted_operations"]


def test_allowlisted_numeric_algorithms_are_reproducible():
    desc = compute._HANDLERS["descriptive_stats"]({"values": [1, 2, 3, 4]}, compute.random.Random(0))
    assert desc["mean"] == 2.5
    assert desc["median"] == 2.5

    corr = compute._HANDLERS["pearson_correlation"](
        {"x": [1, 2, 3], "y": [2, 4, 6]}, compute.random.Random(0)
    )
    assert corr["pearson_r"] == pytest.approx(1.0)

    reg = compute._HANDLERS["linear_regression"](
        {"x": [1, 2, 3], "y": [3, 5, 7]}, compute.random.Random(0)
    )
    assert reg["slope"] == pytest.approx(2.0)
    assert reg["intercept"] == pytest.approx(1.0)
    assert reg["r_squared"] == pytest.approx(1.0)

    request = {
        "variables": [
            {"distribution": "normal", "mean": 10, "sd": 2},
            {"distribution": "bernoulli", "p": 0.4, "weight": 5},
        ],
        "trials": 1200,
        "threshold": 12,
    }
    first = compute._HANDLERS["monte_carlo_sum"](request, compute.random.Random(42))
    second = compute._HANDLERS["monte_carlo_sum"](request, compute.random.Random(42))
    assert first == second
    assert 0.0 <= first["probability_gte_threshold"] <= 1.0


@pytest.mark.parametrize("payload", [
    {"operation": "python", "parameters": {"code": "print(1)"}},
    {"operation": "descriptive_stats", "parameters": {"values": [1, 2]}, "code": "print(1)"},
    {"operation": "descriptive_stats", "parameters": {"values": [1, float("inf")]}},
    {"operation": "monte_carlo_sum", "parameters": {
        "variables": [{"distribution": "custom", "expression": "x*x"}], "trials": 100
    }},
])
def test_non_allowlisted_or_non_numeric_input_is_rejected(payload):
    if payload.get("operation") not in compute.ALLOWED_OPERATIONS or "code" in payload:
        with pytest.raises(projects.ProjectError):
            compute._validate_request(payload)
        return
    operation, parameters, _seed = compute._validate_request(payload)
    with pytest.raises(projects.ProjectError):
        compute._HANDLERS[operation](parameters, compute.random.Random(0))


@pytest.fixture
def postgres(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration is required in CI")
    import psycopg2

    schema = "research_compute_test_" + uuid.uuid4().hex
    with psycopg2.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        return psycopg2.connect(url, options=f"-c search_path={schema}")

    monkeypatch.setattr(projects, "get_connection", connect)
    monkeypatch.setattr(chat, "get_connection", connect)
    monkeypatch.setattr(projects, "_READY", False)
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_AUTONOMY_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_EXPERIMENT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_COMPUTE_ENABLED", "true")

    chat.ensure_velia_chat_tables()
    projects.ensure_tables()
    center.ensure_tables()
    compute.ensure_tables()
    try:
        yield connect
    finally:
        monkeypatch.setattr(projects, "_READY", False)
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _safe_experiment(user_id):
    mission = center.create_mission(
        user_id,
        {"goal": "Analyze battery material measurements with reproducible statistics"},
        f"compute-mission-{user_id}",
    )
    experiment = center.plan_experiment(
        user_id,
        mission["id"],
        {"type": "simulation", "description": "Analyze bounded numerical measurements only"},
    )
    assert experiment["execution_ready"] is True
    return mission, experiment


def test_safe_compute_persists_immutable_reproducibility_snapshot(postgres):
    mission, experiment = _safe_experiment(31)
    payload = {
        "operation": "bootstrap_mean_ci",
        "parameters": {"values": [9, 10, 11, 12, 13], "resamples": 400, "confidence": 0.95},
        "seed": 12345,
    }
    first = compute.execute(31, experiment["id"], payload, "compute-run-0001")
    replay = compute.execute(31, experiment["id"], payload, "compute-run-0001")

    assert replay["id"] == first["id"]
    assert first["mission_id"] == mission["id"]
    assert first["result"]["reproducibility"]["seed"] == 12345
    assert first["result"]["reproducibility"]["user_code_executed"] is False
    assert first["input_hash"]
    assert first["result_hash"]
    assert compute.get_run(31, first["id"])["result"] == first["result"]
    listed = compute.list_runs(31, experiment["id"])
    assert [item["id"] for item in listed["runs"]] == [first["id"]]

    events = center.list_events(31, mission["id"])
    assert any(event["type"] == "compute_run_completed" for event in events)

    with pytest.raises(projects.ProjectError, match="idempotency_conflict"):
        compute.execute(
            31,
            experiment["id"],
            {"operation": "descriptive_stats", "parameters": {"values": [1, 2, 3]}},
            "compute-run-0001",
        )
    with pytest.raises(projects.ProjectError, match="research_compute_run_not_found"):
        compute.get_run(32, first["id"])


def test_restricted_defensive_research_cannot_cross_read_only_boundary(postgres):
    mission = center.create_mission(
        41,
        {"goal": "Defensive cybersecurity research for ransomware detection and incident response"},
        "compute-restricted-41",
    )
    assert mission["safety"]["read_only_only"] is True
    experiment = center.plan_experiment(
        41,
        mission["id"],
        {"type": "simulation", "description": "Compare existing defensive benchmark metrics"},
    )
    assert experiment["execution_ready"] is False

    with pytest.raises(projects.ProjectError, match="research_compute_read_only"):
        compute.execute(
            41,
            experiment["id"],
            {"operation": "descriptive_stats", "parameters": {"values": [1, 2, 3]}},
            "compute-run-restricted",
        )


def test_blocked_experiment_never_executes(postgres):
    mission = center.create_mission(
        51,
        {"goal": "General chemistry literature review"},
        "compute-safe-parent",
    )
    experiment = center.plan_experiment(
        51,
        mission["id"],
        {"description": "Synthesize ricin toxin and optimize yield"},
    )
    assert experiment["status"] == "blocked"

    with pytest.raises(projects.ProjectError, match="research_experiment_not_active"):
        compute.execute(
            51,
            experiment["id"],
            {"operation": "descriptive_stats", "parameters": {"values": [1, 2, 3]}},
            "compute-run-blocked",
        )


def test_compute_requires_both_execution_and_compute_feature_gates(postgres, monkeypatch):
    _mission, experiment = _safe_experiment(61)
    monkeypatch.setenv("VELIA_RESEARCH_COMPUTE_ENABLED", "false")
    with pytest.raises(projects.ProjectError, match="research_compute_disabled"):
        compute.execute(
            61,
            experiment["id"],
            {"operation": "descriptive_stats", "parameters": {"values": [1, 2, 3]}},
            "compute-run-disabled",
        )
