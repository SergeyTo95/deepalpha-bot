import json
import os
import uuid

import pytest

from services import velia_chat_service as chat
from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_compute_service as compute
from services import velia_research_dataset_service as datasets
from services import velia_research_experiment_pipeline_service as pipeline
from services import velia_research_literature_service as literature
from services import velia_research_protocol_service as protocol
from services import velia_research_reasoning_service as reasoning
from services import velia_research_report_service as reports


def test_protocol_officer_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_DATASET_REGISTRY_ENABLED", "true")
    monkeypatch.delenv("VELIA_RESEARCH_PROTOCOL_OFFICER_ENABLED", raising=False)
    state = protocol.status()
    assert state["enabled"] is False
    assert state["preregistration_required_for_test_split"] is True
    assert state["test_rows_exposed_by_api"] is False
    assert state["power_planning"] is True
    assert state["leakage_checks"] is True
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

    schema = "research_protocol_test_" + uuid.uuid4().hex
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
    pipeline.ensure_tables()
    protocol.ensure_tables()
    reports.ensure_tables()
    try:
        yield connect
    finally:
        monkeypatch.setattr(projects, "_READY", False)
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _mission(uid):
    return center.create_mission(
        uid,
        {"goal": "Validate a reproducible sensor relationship", "title": "Protocol study"},
        "protocol-mission-" + str(uid),
    )


def _dataset_payload(seed=33, duplicate=False):
    rows = [[float(i), float(2 * i + 3)] for i in range(1, 81)]
    if duplicate:
        rows[-1] = list(rows[0])
    return {
        "name": "Sensor calibration",
        "columns": ["input_signal", "output_signal"],
        "rows": rows,
        "provenance": {
            "kind": "instrument_export",
            "source_label": "calibration-rig",
            "description": "Bounded numeric calibration export.",
            "license": "internal-research",
            "collected_at": "2026-09-18",
        },
        "split": {"test_fraction": 0.25, "seed": seed},
    }


def _hypothesis(uid, mission_id):
    return center.add_hypothesis(
        uid,
        mission_id,
        "Output increases with input",
        "Confirm the relationship on a held-out preregistered test split.",
    )


def _protocol_payload(hypothesis_id, dataset_id, correction="bonferroni", family_size=2):
    return {
        "hypothesis_id": hypothesis_id,
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
        "family_size": family_size,
        "correction": correction,
        "outlier_policy": "report_only",
    }


def test_test_materialization_requires_confirmatory_authorization(postgres):
    mission = _mission(301)
    dataset = datasets.create(301, mission["id"], _dataset_payload())
    snap = datasets.snapshot(301, dataset["id"], "test", ["output_signal"])
    with pytest.raises(projects.ProjectError, match="research_preregistration_required"):
        datasets.materialize(301, snap)
    train = datasets.snapshot(301, dataset["id"], "train", ["output_signal"])
    assert len(datasets.materialize(301, train)["output_signal"]) == dataset["split"]["train_count"]


def test_locked_protocol_contains_dictionary_quality_power_and_hash(postgres):
    mission = _mission(302)
    dataset = datasets.create(302, mission["id"], _dataset_payload())
    hypothesis = _hypothesis(302, mission["id"])
    item = protocol.create_locked(
        302, mission["id"], _protocol_payload(hypothesis["id"], dataset["id"])
    )

    assert item["status"] == "locked"
    assert item["dataset_hash"] == dataset["dataset_hash"]
    assert item["split_hash"] == dataset["split_hash"]
    assert item["data_dictionary"]["input_signal"]["unit"] == "mV"
    assert item["quality"]["passed"] is True
    assert item["quality"]["missing_policy"] == "reject_missing"
    assert item["quality"]["test_values_used_to_choose_protocol"] is False
    assert item["quality"]["test_values_exposed"] is False
    assert item["power_plan"]["effective_planning_alpha"] == pytest.approx(0.025)
    assert item["power_plan"]["minimum_total_n"] >= 4
    assert len(item["protocol_hash"]) == 64


def test_confirmatory_test_plan_fails_without_preregistration(postgres):
    mission = _mission(303)
    dataset = datasets.create(303, mission["id"], _dataset_payload())
    hypothesis = _hypothesis(303, mission["id"])

    with pytest.raises(projects.ProjectError, match="research_preregistration_required"):
        pipeline.plan(303, mission["id"], {
            "hypothesis_id": hypothesis["id"],
            "analysis_kind": "correlation",
            "dataset_id": dataset["id"],
            "split": "test",
            "columns": ["input_signal", "output_signal"],
        })


def test_locked_protocol_unlocks_exact_held_out_analysis(postgres):
    mission = _mission(304)
    dataset = datasets.create(304, mission["id"], _dataset_payload(seed=9))
    hypothesis = _hypothesis(304, mission["id"])
    locked = protocol.create_locked(
        304, mission["id"], _protocol_payload(hypothesis["id"], dataset["id"])
    )

    plan = pipeline.plan(304, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "correlation",
        "dataset_id": dataset["id"],
        "split": "test",
        "columns": ["input_signal", "output_signal"],
    })
    assert plan["method"]["analysis_mode"] == "confirmatory"
    assert plan["method"]["protocol_snapshot"]["protocol_id"] == locked["id"]
    assert plan["method"]["protocol_snapshot"]["protocol_hash"] == locked["protocol_hash"]

    result = pipeline.run_ready(304, mission["id"], 1)
    assert result["executed"] == 1
    review = result["reviews"][0]
    assert review["replication"]["match"] is True


def test_train_analysis_remains_exploratory_without_protocol(postgres):
    mission = _mission(305)
    dataset = datasets.create(305, mission["id"], _dataset_payload())
    hypothesis = _hypothesis(305, mission["id"])

    plan = pipeline.plan(305, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "descriptive_stats",
        "dataset_id": dataset["id"],
        "split": "train",
        "columns": ["output_signal"],
    })
    assert plan["method"]["analysis_mode"] == "exploratory"
    assert plan["method"]["protocol_snapshot"] is None
    assert pipeline.run_ready(305, mission["id"], 1)["executed"] == 1


def test_protocol_is_bound_to_exact_analysis_and_columns(postgres):
    mission = _mission(306)
    dataset = datasets.create(306, mission["id"], _dataset_payload())
    hypothesis = _hypothesis(306, mission["id"])
    protocol.create_locked(
        306, mission["id"], _protocol_payload(hypothesis["id"], dataset["id"])
    )

    with pytest.raises(projects.ProjectError, match="research_preregistration_required"):
        pipeline.plan(306, mission["id"], {
            "hypothesis_id": hypothesis["id"],
            "analysis_kind": "linear_regression",
            "dataset_id": dataset["id"],
            "split": "test",
            "columns": ["input_signal", "output_signal"],
        })


def test_quality_officer_blocks_cross_split_duplicate_leakage(postgres):
    mission = _mission(307)
    dataset = datasets.create(307, mission["id"], _dataset_payload(seed=2, duplicate=True))
    hypothesis = _hypothesis(307, mission["id"])
    assert (
        0 in dataset["split"]["test_indices"] and 79 in dataset["split"]["train_indices"]
    )
    with pytest.raises(projects.ProjectError, match="research_dataset_quality_failed"):
        protocol.create_locked(
            307, mission["id"], _protocol_payload(hypothesis["id"], dataset["id"])
        )


def test_multiple_comparison_corrections_are_deterministic(postgres):
    mission = _mission(308)
    dataset = datasets.create(308, mission["id"], _dataset_payload())
    hypothesis = _hypothesis(308, mission["id"])
    locked = protocol.create_locked(
        308, mission["id"], _protocol_payload(
            hypothesis["id"], dataset["id"], correction="bonferroni", family_size=3
        )
    )
    corrected = protocol.correct_p_values(308, locked["id"], [0.01, 0.02, 0.5])
    assert corrected["adjusted_p_values"] == pytest.approx([0.03, 0.06, 1.0])
    assert corrected["reject"] == [True, False, False]


def test_protocol_owner_isolation(postgres):
    mission = _mission(309)
    dataset = datasets.create(309, mission["id"], _dataset_payload())
    hypothesis = _hypothesis(309, mission["id"])
    locked = protocol.create_locked(
        309, mission["id"], _protocol_payload(hypothesis["id"], dataset["id"])
    )
    with pytest.raises(projects.ProjectError, match="research_protocol_not_found"):
        protocol.get(310, locked["id"])


def _seed_synthesis(uid, mission_id):
    with projects.transaction(uid) as cur:
        cur.execute("""INSERT INTO velia_research_syntheses(
            synthesis_id,mission_id,user_id,evidence_hash,status,source_ids_json,
            result_json,safety_json,provider)
            VALUES(%s,%s,%s,%s,'completed','[]',%s,%s,'test')""",
            (
                "protocol-synthesis-" + str(uid),
                str(mission_id),
                int(uid),
                "protocol-evidence-" + str(uid),
                json.dumps({
                    "summary": "Preregistered held-out analysis completed.",
                    "confidence": "moderate",
                    "evidence_assessment": [],
                    "contradictions": [],
                    "limitations": [],
                    "hypotheses": [],
                    "open_questions": [],
                }),
                json.dumps({"decision": "allowed", "read_only_only": False}),
            ))


def test_report_carries_locked_protocol_provenance(postgres):
    mission = _mission(311)
    dataset = datasets.create(311, mission["id"], _dataset_payload(seed=19))
    hypothesis = _hypothesis(311, mission["id"])
    locked = protocol.create_locked(
        311, mission["id"], _protocol_payload(hypothesis["id"], dataset["id"])
    )
    _seed_synthesis(311, mission["id"])
    pipeline.plan(311, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "correlation",
        "dataset_id": dataset["id"],
        "split": "test",
        "columns": ["input_signal", "output_signal"],
    })
    pipeline.run_ready(311, mission["id"], 1)
    report = reports.build_report(311, mission["id"])["report"]
    assert report["version"] == 4
    assert report["provenance"]["immutable_protocol_ids"] == [locked["id"]]
    assert report["provenance"]["immutable_protocol_hashes"] == [locked["protocol_hash"]]
    evidence = report["computational_evidence"]["reviews"][0]["data_provenance"]
    assert evidence["analysis_mode"] == "confirmatory"
    assert evidence["protocol_snapshot"]["protocol_hash"] == locked["protocol_hash"]
