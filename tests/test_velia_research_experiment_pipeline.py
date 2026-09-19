import json
import os
import uuid

import pytest

from services import velia_chat_service as chat
from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_compute_service as compute
from services import velia_research_director_service as director
from services import velia_research_experiment_pipeline_service as pipeline
from services import velia_research_literature_service as literature
from services import velia_research_reasoning_service as reasoning
from services import velia_research_report_service as reports


def test_pipeline_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_RESEARCH_EXPERIMENT_PIPELINE_ENABLED", raising=False)
    state = pipeline.status()
    assert state["enabled"] is False
    assert state["invented_numeric_data_allowed"] is False
    assert state["arbitrary_code"] is False
    assert state["arbitrary_shell"] is False
    assert state["arbitrary_file_access"] is False
    assert state["arbitrary_url_fetch"] is False


@pytest.fixture
def postgres(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration is required in CI")
    import psycopg2

    schema = "research_pipeline_test_" + uuid.uuid4().hex
    with psycopg2.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        return psycopg2.connect(url, options=f"-c search_path={schema}")

    monkeypatch.setattr(projects, "get_connection", connect)
    monkeypatch.setattr(chat, "get_connection", connect)
    monkeypatch.setattr(projects, "_READY", False)

    flags = {
        "VELIA_RESEARCH_CENTER_ENABLED": "true",
        "VELIA_RESEARCH_LITERATURE_ENABLED": "true",
        "VELIA_RESEARCH_REASONING_ENABLED": "true",
        "VELIA_RESEARCH_AUTONOMY_ENABLED": "true",
        "VELIA_RESEARCH_DIRECTOR_ENABLED": "true",
        "VELIA_RESEARCH_EXPERIMENT_EXECUTION_ENABLED": "true",
        "VELIA_RESEARCH_COMPUTE_ENABLED": "true",
        "VELIA_RESEARCH_EXPERIMENT_PIPELINE_ENABLED": "true",
        "VELIA_RESEARCH_AUTO_REPORT_ENABLED": "true",
    }
    for name, value in flags.items():
        monkeypatch.setenv(name, value)

    chat.ensure_velia_chat_tables()
    projects.ensure_tables()
    center.ensure_tables()
    literature.ensure_tables()
    reasoning.ensure_tables()
    compute.ensure_tables()
    pipeline.ensure_tables()
    reports.ensure_tables()
    director.ensure_tables()
    try:
        yield connect
    finally:
        monkeypatch.setattr(projects, "_READY", False)
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _mission_with_hypothesis(uid: int, key: str):
    mission = center.create_mission(
        uid,
        {"goal": "Analyze battery measurements with reproducible statistics"},
        key,
    )
    hypothesis = center.add_hypothesis(
        uid,
        mission["id"],
        "Measured capacity has a stable central tendency",
        "A bounded numerical summary can quantify the observed sample.",
    )
    return mission, hypothesis


def test_planner_maps_explicit_data_to_allowlisted_compute(postgres):
    mission, hypothesis = _mission_with_hypothesis(71, "pipeline-mission-0071")
    plan = pipeline.plan(71, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "descriptive_stats",
        "parameters": {"values": [10, 11, 12, 13, 14]},
        "question": "What is the observed capacity distribution?",
    })

    assert plan["status"] == "planned"
    assert plan["method"]["type"] == "safe_compute"
    assert plan["method"]["operation"] == "descriptive_stats"
    assert plan["method"]["data_origin"] == "explicit_structured_input"
    assert plan["method"]["invented_numeric_data"] is False
    assert plan["safety"]["decision"] == "allowed"

    again = pipeline.plan(71, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "descriptive_stats",
        "parameters": {"values": [10, 11, 12, 13, 14]},
        "question": "What is the observed capacity distribution?",
    })
    assert again["id"] == plan["id"]


@pytest.mark.parametrize("data", [
    {
        "hypothesis_id": "missing",
        "analysis_kind": "python",
        "parameters": {"code": "print(1)"},
    },
    {
        "hypothesis_id": "missing",
        "analysis_kind": "descriptive_stats",
        "parameters": {"values": [1, 2]},
        "code": "print(1)",
    },
])
def test_planner_rejects_non_allowlisted_shapes_before_execution(postgres, data):
    mission, hypothesis = _mission_with_hypothesis(72, "pipeline-mission-0072")
    data["hypothesis_id"] = hypothesis["id"]
    with pytest.raises(projects.ProjectError):
        pipeline.plan(72, mission["id"], data)


def test_pipeline_runs_statistician_skeptic_and_replication(postgres):
    mission, hypothesis = _mission_with_hypothesis(73, "pipeline-mission-0073")
    plan = pipeline.plan(73, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "bootstrap_mean_ci",
        "parameters": {
            "values": [8, 9, 10, 11, 12, 13],
            "resamples": 400,
            "confidence": 0.95,
        },
        "seed": 404,
    })

    result = pipeline.run_ready(73, mission["id"], 1)
    assert result["executed"] == 1
    review = result["reviews"][0]
    assert review["experiment_id"] == plan["id"]
    assert review["operation"] == "bootstrap_mean_ci"
    assert review["replication"]["match"] is True
    assert review["replication"]["recomputed_without_user_code"] is True
    assert review["statistician"]["role"] == "statistician"
    assert review["skeptic"]["role"] == "skeptic"
    assert review["result_hash"]

    stored = pipeline.list_reviews(73, mission["id"])
    assert [item["id"] for item in stored["reviews"]] == [review["id"]]
    assert pipeline.run_ready(73, mission["id"], 1)["executed"] == 0

    with projects.transaction() as cur:
        cur.execute("SELECT status,result_json FROM velia_research_experiments WHERE experiment_id=%s", (plan["id"],))
        row = cur.fetchone()
        assert row["status"] == "completed"
        snapshot = json.loads(row["result_json"])
        assert snapshot["replication"]["match"] is True


def test_restricted_defensive_mission_never_enters_compute_pipeline(postgres):
    mission = center.create_mission(
        74,
        {"goal": "Defensive cybersecurity research for ransomware detection and incident response"},
        "pipeline-restricted-0074",
    )
    hypothesis = center.add_hypothesis(
        74,
        mission["id"],
        "Defensive anomaly metrics may improve incident response",
        "Compare already published defensive benchmark data.",
    )
    assert mission["safety"]["read_only_only"] is True
    with pytest.raises(projects.ProjectError, match="research_compute_read_only"):
        pipeline.plan(74, mission["id"], {
            "hypothesis_id": hypothesis["id"],
            "analysis_kind": "descriptive_stats",
            "parameters": {"values": [1, 2, 3]},
        })


def _seed_completed_synthesis(uid: int, mission_id: str, suffix: str):
    synthesis_id = "syn-" + suffix
    result = {
        "summary": "Evidence supports a bounded quantitative follow-up.",
        "confidence": "moderate",
        "evidence_assessment": [],
        "contradictions": [],
        "limitations": ["No external empirical replication in this snapshot."],
        "hypotheses": [],
        "open_questions": [],
    }
    with projects.transaction(uid) as cur:
        cur.execute("""INSERT INTO velia_research_syntheses(
            synthesis_id,mission_id,user_id,evidence_hash,status,source_ids_json,
            result_json,safety_json,provider)
            VALUES(%s,%s,%s,%s,'completed','[]',%s,%s,'test')""",
            (
                synthesis_id,
                str(mission_id),
                int(uid),
                "evidence-" + suffix,
                json.dumps(result),
                json.dumps({"decision": "allowed", "read_only_only": False}),
            ))
    return synthesis_id


def test_report_versions_when_compute_evidence_changes(postgres):
    mission, hypothesis = _mission_with_hypothesis(75, "pipeline-mission-0075")
    synthesis_id = _seed_completed_synthesis(75, mission["id"], "0075")

    before = reports.build_report(75, mission["id"])
    assert before["synthesis_id"] == synthesis_id
    assert before["report"]["computational_evidence"]["review_count"] == 0

    pipeline.plan(75, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "bootstrap_mean_difference_ci",
        "parameters": {
            "a": [10, 11, 12, 13, 14],
            "b": [8, 9, 9, 10, 11],
            "resamples": 300,
            "confidence": 0.95,
        },
        "seed": 77,
    })
    pipeline.run_ready(75, mission["id"], 1)

    after = reports.build_report(75, mission["id"])
    assert after["id"] != before["id"]
    assert after["report_hash"] != before["report_hash"]
    assert after["report"]["computational_evidence"]["review_count"] == 1
    assert after["report"]["computational_evidence"]["reviews"][0]["replication"]["match"] is True
    assert len(after["report"]["provenance"]["immutable_compute_review_ids"]) == 1

    listed = reports.list_reports(75, mission["id"])
    assert len(listed["reports"]) == 2


def test_director_executes_ready_plan_and_attaches_final_report(postgres, monkeypatch):
    mission, hypothesis = _mission_with_hypothesis(76, "pipeline-mission-0076")
    pipeline.plan(76, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "linear_regression",
        "parameters": {"x": [1, 2, 3, 4], "y": [3, 5, 7, 9]},
    })

    searches = []
    monkeypatch.setattr(literature, "collect", lambda uid, mid, query, limit: searches.append(query) or {
        "cached": False,
        "sources": [{"id": "source-1"}],
    })
    monkeypatch.setattr(reasoning, "synthesize", lambda uid, mid, limit: {
        "id": "syn-director",
        "cached": False,
        "result": {
            "confidence": "high",
            "contradictions": [],
            "open_questions": [],
        },
    })
    monkeypatch.setattr(reports, "build_report", lambda uid, mid: {
        "id": "report-final",
        "report_hash": "report-hash-final",
    })

    queued = director.enqueue(76, mission["id"], 1)
    claimed = director.claim_next("pipeline-worker")
    assert claimed["id"] == queued["id"]
    completed = director.execute_claimed(claimed, "pipeline-worker")

    assert completed["status"] == "completed"
    assert completed["stop_reason"] == "evidence_sufficient"
    assert len(completed["summary"]["experiment_reviews"]) == 1
    assert completed["summary"]["experiment_reviews"][0]["replication_match"] is True
    assert completed["summary"]["final_report_id"] == "report-final"
    assert completed["summary"]["final_report_hash"] == "report-hash-final"
    assert searches == [mission["goal"]]
