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
from services import velia_research_reasoning_service as reasoning
from services import velia_research_report_service as reports


def test_dataset_registry_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.delenv("VELIA_RESEARCH_DATASET_REGISTRY_ENABLED", raising=False)
    state = datasets.status()
    assert state["enabled"] is False
    assert state["numeric_only"] is True
    assert state["immutable_snapshots"] is True
    assert state["deterministic_train_test_split"] is True
    assert state["arbitrary_code"] is False
    assert state["arbitrary_shell"] is False
    assert state["arbitrary_file_access"] is False
    assert state["arbitrary_url_fetch"] is False
    assert state["dynamic_expression_eval"] is False


@pytest.fixture
def postgres(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration is required in CI")
    import psycopg2

    schema = "research_dataset_test_" + uuid.uuid4().hex
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
    reports.ensure_tables()
    try:
        yield connect
    finally:
        monkeypatch.setattr(projects, "_READY", False)
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _mission(uid: int, key: str):
    return center.create_mission(
        uid,
        {"goal": "Study reproducible battery measurements", "title": "Dataset research"},
        key,
    )


def _payload(seed=17):
    return {
        "name": "Battery capacity benchmark",
        "columns": ["cycle", "capacity"],
        "rows": [[i, 100.0 - i * 0.5] for i in range(1, 21)],
        "provenance": {
            "kind": "instrument_export",
            "source_label": "bench-7",
            "description": "Numeric instrument export normalized to two columns.",
            "license": "internal-research",
            "collected_at": "2026-09-18",
        },
        "split": {"test_fraction": 0.25, "seed": seed},
    }


def test_registry_is_immutable_deduplicated_and_owner_scoped(postgres):
    mission = _mission(81, "dataset-mission-0081")
    first = datasets.create(81, mission["id"], _payload())
    second = datasets.create(81, mission["id"], _payload())

    assert second["id"] == first["id"]
    assert second["dataset_hash"] == first["dataset_hash"]
    assert second["split_hash"] == first["split_hash"]
    assert first["row_count"] == 20
    assert first["column_count"] == 2
    assert first["split"]["train_count"] == 15
    assert first["split"]["test_count"] == 5

    fetched = datasets.get(81, first["id"], include_rows=True)
    assert fetched["rows"][0] == [1.0, 99.5]
    with pytest.raises(projects.ProjectError, match="research_dataset_not_found"):
        datasets.get(82, first["id"])


def test_train_test_membership_is_deterministic_and_disjoint(postgres):
    mission = _mission(83, "dataset-mission-0083")
    item = datasets.create(83, mission["id"], _payload(seed=99))

    train_snapshot = datasets.snapshot(83, item["id"], "train", ["cycle"])
    test_snapshot = datasets.snapshot(83, item["id"], "test", ["cycle"])
    train = set(datasets.materialize(83, train_snapshot)["cycle"])
    test = set(datasets.materialize(83, test_snapshot)["cycle"])

    assert train
    assert test
    assert train.isdisjoint(test)
    assert train | test == {float(i) for i in range(1, 21)}
    assert datasets.snapshot(83, item["id"], "test", ["cycle"]) == test_snapshot


@pytest.mark.parametrize("mutation", [
    lambda p: p.update({"rows": [[1, "not-numeric"], [2, 3], [3, 4], [4, 5]]}),
    lambda p: p.update({"columns": ["bad column", "capacity"]}),
    lambda p: p.update({"expression": "eval(user_input)"}),
    lambda p: p.update({"split": {"test_fraction": 0.99, "seed": 1}}),
])
def test_registry_rejects_non_numeric_or_unbounded_shapes(postgres, mutation):
    mission = _mission(84, "dataset-mission-" + uuid.uuid4().hex[:12])
    payload = _payload()
    mutation(payload)
    with pytest.raises(projects.ProjectError):
        datasets.create(84, mission["id"], payload)


def _hypothesis(uid: int, mission_id: str):
    return center.add_hypothesis(
        uid,
        mission_id,
        "Capacity changes approximately linearly over the measured cycles",
        "Use a held-out immutable test split to quantify the relationship.",
    )


def test_dataset_backed_plan_stores_hashes_not_numeric_arrays(postgres):
    mission = _mission(85, "dataset-mission-0085")
    dataset = datasets.create(85, mission["id"], _payload())
    hypothesis = _hypothesis(85, mission["id"])

    plan = pipeline.plan(85, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "linear_regression",
        "dataset_id": dataset["id"],
        "split": "test",
        "columns": ["cycle", "capacity"],
        "question": "Does the held-out relationship remain approximately linear?",
    })

    method = plan["method"]
    assert method["data_origin"] == "dataset_registry"
    assert "parameters" not in method
    assert method["dataset_snapshot"]["dataset_hash"] == dataset["dataset_hash"]
    assert method["dataset_snapshot"]["split_hash"] == dataset["split_hash"]
    assert method["dataset_snapshot"]["split"] == "test"
    assert method["dataset_snapshot"]["columns"] == ["cycle", "capacity"]
    assert method["invented_numeric_data"] is False

    encoded = json.dumps(method)
    assert "99.5" not in encoded
    assert '"rows"' not in encoded


def test_pipeline_executes_only_verified_dataset_snapshot(postgres):
    mission = _mission(86, "dataset-mission-0086")
    dataset = datasets.create(86, mission["id"], _payload(seed=3))
    hypothesis = _hypothesis(86, mission["id"])
    plan = pipeline.plan(86, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "correlation",
        "dataset_id": dataset["id"],
        "split": "test",
        "columns": ["cycle", "capacity"],
    })

    result = pipeline.run_ready(86, mission["id"], 1)
    assert result["executed"] == 1
    review = result["reviews"][0]
    assert review["experiment_id"] == plan["id"]
    assert review["replication"]["match"] is True

    with projects.transaction() as cur:
        cur.execute(
            "SELECT result_json FROM velia_research_experiments WHERE experiment_id=%s",
            (plan["id"],),
        )
        stored = json.loads(cur.fetchone()["result_json"])
    assert stored["data_origin"] == "dataset_registry"
    assert stored["data_provenance"]["dataset_hash"] == dataset["dataset_hash"]
    assert stored["statistician"]["metrics"]["n"] == dataset["split"]["test_count"]


def test_snapshot_mismatch_fails_closed_before_compute(postgres):
    mission = _mission(87, "dataset-mission-0087")
    dataset = datasets.create(87, mission["id"], _payload(seed=4))
    hypothesis = _hypothesis(87, mission["id"])
    pipeline.plan(87, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "descriptive_stats",
        "dataset_id": dataset["id"],
        "split": "test",
        "columns": ["capacity"],
    })

    with projects.transaction() as cur:
        cur.execute(
            "UPDATE velia_research_datasets SET split_hash=%s WHERE dataset_id=%s",
            ("tampered", dataset["id"]),
        )

    with pytest.raises(projects.ProjectError, match="research_dataset_snapshot_mismatch"):
        pipeline.run_ready(87, mission["id"], 1)
    with projects.transaction() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM velia_research_compute_runs WHERE mission_id=%s AND user_id=%s",
            (mission["id"], 87),
        )
        assert cur.fetchone()["n"] == 0


def _seed_synthesis(uid: int, mission_id: str):
    result = {
        "summary": "Literature motivates a held-out numerical check.",
        "confidence": "moderate",
        "evidence_assessment": [],
        "contradictions": [],
        "limitations": [],
        "hypotheses": [],
        "open_questions": [],
    }
    with projects.transaction(uid) as cur:
        cur.execute("""INSERT INTO velia_research_syntheses(
            synthesis_id,mission_id,user_id,evidence_hash,status,source_ids_json,
            result_json,safety_json,provider)
            VALUES(%s,%s,%s,%s,'completed','[]',%s,%s,'test')""",
            (
                "dataset-synthesis-" + str(uid),
                str(mission_id),
                int(uid),
                "dataset-evidence-" + str(uid),
                json.dumps(result),
                json.dumps({"decision": "allowed", "read_only_only": False}),
            ))


def test_report_carries_dataset_and_split_provenance(postgres):
    mission = _mission(88, "dataset-mission-0088")
    dataset = datasets.create(88, mission["id"], _payload(seed=44))
    hypothesis = _hypothesis(88, mission["id"])
    _seed_synthesis(88, mission["id"])

    pipeline.plan(88, mission["id"], {
        "hypothesis_id": hypothesis["id"],
        "analysis_kind": "bootstrap_mean_ci",
        "dataset_id": dataset["id"],
        "split": "test",
        "columns": ["capacity"],
        "analysis_options": {"resamples": 200, "confidence": 0.95},
        "seed": 700,
    })
    pipeline.run_ready(88, mission["id"], 1)
    report = reports.build_report(88, mission["id"])["report"]

    computational = report["computational_evidence"]
    assert computational["review_count"] == 1
    assert len(computational["datasets"]) == 1
    snapshot = computational["datasets"][0]
    assert snapshot["dataset_id"] == dataset["id"]
    assert snapshot["dataset_hash"] == dataset["dataset_hash"]
    assert snapshot["split_hash"] == dataset["split_hash"]
    assert snapshot["split"] == "test"
    assert report["provenance"]["immutable_dataset_ids"] == [dataset["id"]]
    assert report["provenance"]["immutable_dataset_hashes"] == [dataset["dataset_hash"]]
    assert report["provenance"]["immutable_dataset_split_hashes"] == [dataset["split_hash"]]


def test_restricted_defensive_dataset_can_be_registered_but_not_computed(postgres):
    mission = center.create_mission(
        89,
        {"goal": "Study defensive ransomware detection metrics"},
        "dataset-restricted-0089",
    )
    assert mission["safety"]["read_only_only"] is True
    dataset = datasets.create(89, mission["id"], _payload())
    assert dataset["id"]

    hypothesis = center.add_hypothesis(
        89,
        mission["id"],
        "Defensive metrics may be stable",
        "Read-only defensive research only.",
    )
    with pytest.raises(projects.ProjectError, match="research_compute_read_only"):
        pipeline.plan(89, mission["id"], {
            "hypothesis_id": hypothesis["id"],
            "analysis_kind": "descriptive_stats",
            "dataset_id": dataset["id"],
            "split": "test",
            "columns": ["capacity"],
        })
