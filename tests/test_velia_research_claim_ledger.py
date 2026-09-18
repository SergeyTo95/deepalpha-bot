import json
import os
import uuid

import pytest

from services import velia_chat_service as chat
from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_claim_service as claims
from services import velia_research_compute_service as compute
from services import velia_research_dataset_service as datasets
from services import velia_research_experiment_pipeline_service as pipeline
from services import velia_research_literature_service as literature
from services import velia_research_protocol_service as protocol
from services import velia_research_reasoning_service as reasoning
from services import velia_research_report_service as reports


def test_claim_ledger_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.delenv("VELIA_RESEARCH_CLAIM_LEDGER_ENABLED", raising=False)
    state = claims.status()
    assert state["enabled"] is False
    assert state["immutable_claim_definitions"] is True
    assert state["immutable_stage_snapshots"] is True
    assert state["replication_requires_distinct_dataset_hash"] is True
    assert state["replication_requires_distinct_provenance_fingerprint"] is True
    assert state["claim_language_is_stage_bounded"] is True
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

    schema = "research_claim_test_" + uuid.uuid4().hex
    with psycopg2.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        return psycopg2.connect(url, options=f"-c search_path={schema}")

    monkeypatch.setattr(projects, "get_connection", connect)
    monkeypatch.setattr(chat, "get_connection", connect)
    monkeypatch.setattr(projects, "_READY", False)
    for name in (
        "VELIA_RESEARCH_CENTER_ENABLED",
        "VELIA_RESEARCH_LITERATURE_ENABLED",
        "VELIA_RESEARCH_REASONING_ENABLED",
        "VELIA_RESEARCH_AUTONOMY_ENABLED",
        "VELIA_RESEARCH_DIRECTOR_ENABLED",
        "VELIA_RESEARCH_EXPERIMENT_EXECUTION_ENABLED",
        "VELIA_RESEARCH_COMPUTE_ENABLED",
        "VELIA_RESEARCH_EXPERIMENT_PIPELINE_ENABLED",
        "VELIA_RESEARCH_DATASET_REGISTRY_ENABLED",
        "VELIA_RESEARCH_PROTOCOL_OFFICER_ENABLED",
        "VELIA_RESEARCH_CLAIM_LEDGER_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("VELIA_RESEARCH_AUTO_REPORT_ENABLED", "false")

    chat.ensure_velia_chat_tables()
    projects.ensure_tables()
    center.ensure_tables()
    literature.ensure_tables()
    reasoning.ensure_tables()
    compute.ensure_tables()
    datasets.ensure_tables()
    claims.ensure_tables()
    protocol.ensure_tables()
    pipeline.ensure_tables()
    reports.ensure_tables()
    try:
        yield connect
    finally:
        monkeypatch.setattr(projects, "_READY", False)
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _mission(uid: int):
    return center.create_mission(
        uid,
        {"goal": "Test a preregistered sensor relationship", "title": "Claim study"},
        "claim-mission-" + str(uid),
    )


def _hypothesis(uid: int, mission_id: str):
    return center.add_hypothesis(
        uid,
        mission_id,
        "Output rises with input",
        "A held-out correlation can test the preregistered directional claim.",
    )


def _dataset(label: str, *, direction: int = 1, offset: float = 0.0, seed: int = 17):
    rows = []
    for i in range(1, 161):
        x = float(i)
        y = float(direction * (2.0 * i) + offset + ((i % 7) * 0.01))
        rows.append([x, y])
    return {
        "name": "Sensor replication " + label,
        "columns": ["input_signal", "output_signal"],
        "rows": rows,
        "provenance": {
            "kind": "instrument_export",
            "source_label": label,
            "description": "Independent bounded calibration export.",
            "license": "internal-research",
            "collected_at": "2026-09-18",
        },
        "split": {"test_fraction": 0.25, "seed": seed},
    }


def _claim(uid: int, mission_id: str, hypothesis_id: str):
    return claims.create_claim(uid, mission_id, {
        "hypothesis_id": hypothesis_id,
        "statement": "Sensor output increases as sensor input increases.",
        "expected_direction": "positive",
        "analysis_kind": "correlation",
        "source_ids": [],
    })


def _protocol(uid: int, mission_id: str, hypothesis_id: str, dataset_id: str, claim_id: str):
    return protocol.create_locked(uid, mission_id, {
        "hypothesis_id": hypothesis_id,
        "claim_id": claim_id,
        "dataset_id": dataset_id,
        "analysis_kind": "correlation",
        "columns": ["input_signal", "output_signal"],
        "outcome_column": "output_signal",
        "data_dictionary": {
            "input_signal": {
                "unit": "mV",
                "role": "feature",
                "description": "Instrument input signal.",
            },
            "output_signal": {
                "unit": "mV",
                "role": "outcome",
                "description": "Instrument output signal.",
            },
        },
        "alpha": 0.05,
        "target_power": 0.8,
        "effect_size": 0.5,
        "family_size": 1,
        "correction": "none",
        "outlier_policy": "report_only",
    })


def _run_confirmatory(uid: int, mission_id: str, hypothesis_id: str, dataset_id: str, claim_id: str):
    plan = pipeline.plan(uid, mission_id, {
        "hypothesis_id": hypothesis_id,
        "claim_id": claim_id,
        "analysis_kind": "correlation",
        "dataset_id": dataset_id,
        "split": "test",
        "columns": ["input_signal", "output_signal"],
    })
    result = pipeline.run_ready(uid, mission_id, 1)
    assert result["executed"] == 1
    return plan, result["reviews"][0]


def test_claim_must_bind_protocol_and_exact_confirmatory_plan(postgres):
    mission = _mission(401)
    hypothesis = _hypothesis(401, mission["id"])
    claim_a = _claim(401, mission["id"], hypothesis["id"])
    claim_b = claims.create_claim(401, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "statement": "A second predeclared directional formulation.",
        "expected_direction": "positive",
        "analysis_kind": "correlation",
        "source_ids": [],
    })
    dataset = datasets.create(401, mission["id"], _dataset("rig-A"))
    locked = _protocol(401, mission["id"], hypothesis["id"], dataset["id"], claim_a["id"])

    assert locked["claim_id"] == claim_a["id"]
    assert locked["claim_hash"] == claim_a["claim_hash"]

    with pytest.raises(projects.ProjectError, match="research_preregistration_required"):
        pipeline.plan(401, mission["id"], {
            "hypothesis_id": hypothesis["id"],
            "claim_id": claim_b["id"],
            "analysis_kind": "correlation",
            "dataset_id": dataset["id"],
            "split": "test",
            "columns": ["input_signal", "output_signal"],
        })


def test_single_confirmatory_support_is_preregistered_not_replicated(postgres):
    mission = _mission(402)
    hypothesis = _hypothesis(402, mission["id"])
    claim = _claim(402, mission["id"], hypothesis["id"])
    dataset = datasets.create(402, mission["id"], _dataset("rig-A"))
    _protocol(402, mission["id"], hypothesis["id"], dataset["id"], claim["id"])
    _, review = _run_confirmatory(402, mission["id"], hypothesis["id"], dataset["id"], claim["id"])

    snapshot = review["claim_snapshot"]
    assert snapshot["stage"] == "preregistered"
    assert "One preregistered held-out analysis supports" in snapshot["wording"]
    assert snapshot["evidence"]["experiment_evidence"][0]["signal"] == "supports"
    assert snapshot["evidence"]["experiment_evidence"][0]["replication_verified"] is True


def test_distinct_dataset_and_provenance_promotes_to_replicated(postgres):
    mission = _mission(403)
    hypothesis = _hypothesis(403, mission["id"])
    claim = _claim(403, mission["id"], hypothesis["id"])

    first = datasets.create(403, mission["id"], _dataset("rig-A", offset=0.0, seed=11))
    _protocol(403, mission["id"], hypothesis["id"], first["id"], claim["id"])
    _run_confirmatory(403, mission["id"], hypothesis["id"], first["id"], claim["id"])

    second = datasets.create(403, mission["id"], _dataset("rig-B", offset=3.0, seed=29))
    _protocol(403, mission["id"], hypothesis["id"], second["id"], claim["id"])
    _, review = _run_confirmatory(403, mission["id"], hypothesis["id"], second["id"], claim["id"])

    assert review["claim_snapshot"]["stage"] == "replicated"
    ledger = claims.mission_ledger(403, mission["id"])
    assert ledger["stage_counts"]["replicated"] == 1
    assert len(ledger["replication_edges"]) == 1
    assert ledger["replication_edges"][0]["relation"] == "dataset_distinct_replication"
    evidence = review["claim_snapshot"]["evidence"]["experiment_evidence"]
    assert len({item["dataset_hash"] for item in evidence}) == 2
    assert len({item["provenance_fingerprint"] for item in evidence}) == 2


def test_same_provenance_fingerprint_does_not_count_as_replication(postgres):
    mission = _mission(404)
    hypothesis = _hypothesis(404, mission["id"])
    claim = _claim(404, mission["id"], hypothesis["id"])

    first = datasets.create(404, mission["id"], _dataset("same-rig", offset=0.0, seed=13))
    _protocol(404, mission["id"], hypothesis["id"], first["id"], claim["id"])
    _run_confirmatory(404, mission["id"], hypothesis["id"], first["id"], claim["id"])

    second = datasets.create(404, mission["id"], _dataset("same-rig", offset=9.0, seed=31))
    assert second["dataset_hash"] != first["dataset_hash"]
    _protocol(404, mission["id"], hypothesis["id"], second["id"], claim["id"])
    _, review = _run_confirmatory(404, mission["id"], hypothesis["id"], second["id"], claim["id"])

    assert review["claim_snapshot"]["stage"] == "preregistered"
    assert claims.mission_ledger(404, mission["id"])["replication_edges"] == []


def test_preregistered_opposite_direction_marks_claim_contradicted(postgres):
    mission = _mission(405)
    hypothesis = _hypothesis(405, mission["id"])
    claim = _claim(405, mission["id"], hypothesis["id"])

    first = datasets.create(405, mission["id"], _dataset("rig-positive", direction=1, seed=3))
    _protocol(405, mission["id"], hypothesis["id"], first["id"], claim["id"])
    _run_confirmatory(405, mission["id"], hypothesis["id"], first["id"], claim["id"])

    second = datasets.create(405, mission["id"], _dataset("rig-negative", direction=-1, seed=7))
    _protocol(405, mission["id"], hypothesis["id"], second["id"], claim["id"])
    _, review = _run_confirmatory(405, mission["id"], hypothesis["id"], second["id"], claim["id"])

    snapshot = review["claim_snapshot"]
    assert snapshot["stage"] == "contradicted"
    assert "no affirmative conclusion is permitted" in snapshot["wording"]
    signals = {item["signal"] for item in snapshot["evidence"]["experiment_evidence"]}
    assert signals == {"supports", "contradicts"}

    ledger = claims.mission_ledger(405, mission["id"])
    assert ledger["replication_edges"][0]["relation"] == "dataset_distinct_conflict"


def test_claim_definition_cannot_be_created_after_confirmatory_test_exists(postgres, monkeypatch):
    mission = _mission(406)
    hypothesis = _hypothesis(406, mission["id"])

    monkeypatch.setenv("VELIA_RESEARCH_CLAIM_LEDGER_ENABLED", "false")
    dataset = datasets.create(406, mission["id"], _dataset("legacy-rig"))
    locked = protocol.create_locked(406, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "dataset_id": dataset["id"],
        "analysis_kind": "correlation",
        "columns": ["input_signal", "output_signal"],
        "outcome_column": "output_signal",
        "data_dictionary": {
            "input_signal": {"unit": "mV", "role": "feature", "description": ""},
            "output_signal": {"unit": "mV", "role": "outcome", "description": ""},
        },
        "alpha": 0.05,
        "target_power": 0.8,
        "effect_size": 0.5,
        "family_size": 1,
        "correction": "none",
        "outlier_policy": "report_only",
    })
    assert locked["claim_id"] is None
    pipeline.plan(406, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "correlation",
        "dataset_id": dataset["id"],
        "split": "test",
        "columns": ["input_signal", "output_signal"],
    })

    monkeypatch.setenv("VELIA_RESEARCH_CLAIM_LEDGER_ENABLED", "true")
    with pytest.raises(projects.ProjectError, match="research_claim_must_precede_confirmatory_test"):
        _claim(406, mission["id"], hypothesis["id"])


def _seed_synthesis(uid: int, mission_id: str, summary: str):
    with projects.transaction(uid) as cur:
        cur.execute("""INSERT INTO velia_research_syntheses(
            synthesis_id,mission_id,user_id,evidence_hash,status,source_ids_json,
            result_json,safety_json,provider)
            VALUES(%s,%s,%s,%s,'completed','[]',%s,%s,'test')""",
            (
                "claim-synthesis-" + str(uid),
                str(mission_id),
                int(uid),
                "claim-evidence-" + str(uid),
                json.dumps({
                    "summary": summary,
                    "confidence": "high",
                    "evidence_assessment": [],
                    "contradictions": [],
                    "limitations": [],
                    "hypotheses": [],
                    "open_questions": [],
                }),
                json.dumps({"decision": "allowed", "read_only_only": False}),
            ))


def test_report_uses_ledger_wording_not_stronger_llm_synthesis(postgres):
    mission = _mission(407)
    hypothesis = _hypothesis(407, mission["id"])
    claim = _claim(407, mission["id"], hypothesis["id"])
    dataset = datasets.create(407, mission["id"], _dataset("rig-negative", direction=-1))
    _protocol(407, mission["id"], hypothesis["id"], dataset["id"], claim["id"])
    _run_confirmatory(407, mission["id"], hypothesis["id"], dataset["id"], claim["id"])
    _seed_synthesis(407, mission["id"], "This definitively proves the positive claim.")

    report = reports.build_report(407, mission["id"])["report"]
    assert report["version"] == 5
    assert report["conclusion"]["confidence"] == "claim_ledger_bounded"
    assert "contradicts the claim" in report["conclusion"]["summary"]
    assert "definitively proves" not in report["conclusion"]["summary"]
    assert "definitively proves" in report["conclusion"]["literature_synthesis_summary"]
    assert report["claim_ledger"]["stage_counts"]["contradicted"] == 1
    assert report["safety"]["claim_overstatement_allowed"] is False
    assert report["provenance"]["immutable_claim_ids"] == [claim["id"]]
    assert len(report["provenance"]["immutable_claim_evidence_hashes"]) == 1


def test_claim_owner_isolation(postgres):
    mission = _mission(408)
    hypothesis = _hypothesis(408, mission["id"])
    claim = _claim(408, mission["id"], hypothesis["id"])
    with pytest.raises(projects.ProjectError, match="research_claim_not_found"):
        claims.get_claim(409, claim["id"])
