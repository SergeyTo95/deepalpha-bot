import json
import os
import uuid

import pytest

from services import velia_chat_service as chat
from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_claim_service as claims
from services import velia_research_reasoning_service as reasoning
from services import velia_research_report_service as reports
from services import velia_research_timeline_service as timeline


def test_timeline_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_CLAIM_LEDGER_ENABLED", "true")
    monkeypatch.delenv("VELIA_RESEARCH_KNOWLEDGE_TIMELINE_ENABLED", raising=False)
    state = timeline.status()
    assert state["enabled"] is False
    assert state["immutable_source_history"] is True
    assert state["candidate_discovery_alerts"] is False
    assert state["material_change_alerts_only"] is True
    assert state["report_as_of_supported"] is True
    assert state["extra_model_calls"] == 0
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

    schema = "research_timeline_test_" + uuid.uuid4().hex
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
        "VELIA_RESEARCH_CLAIM_LEDGER_ENABLED",
        "VELIA_RESEARCH_KNOWLEDGE_TIMELINE_ENABLED",
    ):
        monkeypatch.setenv(name, "true")

    chat.ensure_velia_chat_tables()
    projects.ensure_tables()
    center.ensure_tables()
    reasoning.ensure_tables()
    claims.ensure_tables()
    reports.ensure_tables()
    timeline.ensure_tables()

    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_living_reassessments (
            run_id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL,
            review_id TEXT NOT NULL DEFAULT '',
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            status TEXT NOT NULL,
            input_hash TEXT NOT NULL DEFAULT '',
            worker_id TEXT NULL,
            lease_until TIMESTAMP NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            previous_report_id TEXT NULL,
            previous_report_hash TEXT NULL,
            result_hash TEXT NULL,
            result_json TEXT NULL,
            new_report_id TEXT NULL,
            new_report_hash TEXT NULL,
            error_code TEXT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )""")

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
        {"goal": "Track how a scientific claim evolves", "title": "Knowledge timeline"},
        "timeline-mission-" + str(uid),
    )


def _claim(uid, mission_id):
    hypothesis = center.add_hypothesis(
        uid,
        mission_id,
        "Registered relationship",
        "A bounded claim whose evidence state can evolve over time.",
    )
    claim = claims.create_claim(uid, mission_id, {
        "hypothesis_id": hypothesis["id"],
        "statement": "The registered relationship is positive.",
        "expected_direction": "positive",
        "analysis_kind": "correlation",
        "source_ids": [],
    })
    snapshot = claims.refresh_claim(uid, claim["id"])
    return claim, snapshot


def _insert_reassessment(uid, mission_id, claim_id, run_id, changes, *, action="no_change"):
    result = {
        "reassessment_version": 1,
        "run_id": run_id,
        "scan_id": "scan-" + run_id,
        "scientific_diff": [{
            "claim_id": claim_id,
            "before_snapshot_id": "before-" + run_id,
            "before_stage": "exploratory",
            "before_evidence_hash": "a" * 64,
            "before_wording": "Before wording",
            "after_snapshot_id": "after-" + run_id,
            "after_stage": "exploratory",
            "after_evidence_hash": "b" * 64,
            "after_wording": "After wording",
            "changes": changes,
            "living_calibration_action": action,
        }],
        "claim_results": [{
            "claim_id": claim_id,
            "living_meta": {
                "calibration": {
                    "claim_action": action,
                    "reasons": changes,
                    "claim_promotion_allowed": False,
                }
            },
        }],
    }
    with projects.transaction(uid) as cur:
        cur.execute("""INSERT INTO velia_research_living_reassessments(
            run_id,scan_id,mission_id,user_id,status,result_hash,result_json,
            previous_report_id,previous_report_hash,new_report_id,new_report_hash,
            created_at,updated_at)
            VALUES(%s,%s,%s,%s,'completed',%s,%s,%s,%s,%s,%s,NOW(),NOW())""",
            (
                run_id, "scan-" + run_id, mission_id, int(uid),
                "c" * 64, json.dumps(result),
                "report-before-" + run_id, "d" * 64,
                "report-after-" + run_id, "e" * 64,
            ))


def test_retraction_is_material_alert_but_new_evidence_only_is_not(postgres):
    mission = _mission(801)
    claim, _ = _claim(801, mission["id"])

    _insert_reassessment(
        801,
        mission["id"],
        claim["id"],
        "run-retraction",
        [
            "new_verified_external_evidence_added",
            "citation_integrity_alert",
            "retracted_or_withdrawn_evidence_removed_from_living_pool",
            "caution_added",
        ],
        action="caution",
    )
    _insert_reassessment(
        801,
        mission["id"],
        claim["id"],
        "run-new-paper-only",
        ["new_verified_external_evidence_added", "no_material_claim_change"],
        action="no_change",
    )

    result = timeline.list_alerts(801, mission["id"])
    retraction = [
        item for item in result["alerts"]
        if item["source_id"] == "run-retraction"
    ]
    discovery_only = [
        item for item in result["alerts"]
        if item["source_id"] == "run-new-paper-only"
    ]
    assert len(retraction) == 1
    assert retraction[0]["kind"] == "evidence_retracted"
    assert retraction[0]["severity"] == "critical"
    assert retraction[0]["acknowledged"] is False
    assert discovery_only == []

    acknowledged = timeline.acknowledge_alert(801, retraction[0]["id"])
    assert acknowledged["acknowledged"] is True
    unread = timeline.list_alerts(801, mission["id"], unread_only=True)
    assert all(item["id"] != retraction[0]["id"] for item in unread["alerts"])


def test_claim_timeline_tracks_materiality_and_confidence(postgres):
    mission = _mission(802)
    claim, first = _claim(802, mission["id"])
    claims.record_calibration(
        802,
        claim["id"],
        "meta_analysis",
        "meta-timeline-caution",
        {
            "claim_action": "caution",
            "reasons": ["high_heterogeneity"],
            "claim_promotion_allowed": False,
        },
    )
    second = claims.refresh_claim(802, claim["id"])
    assert second["id"] != first["id"]

    result = timeline.claim_timeline(802, claim["id"])
    snapshots = [item for item in result["timeline"] if item["type"] == "claim_snapshot"]
    assert len(snapshots) >= 2
    latest = snapshots[0]
    assert latest["material"] is True
    assert "caution_added" in latest["material_reasons"]
    assert latest["confidence_transition"] == "decreased"
    assert result["policy"]["candidate_discovery_alone_is_material"] is False


def test_report_as_of_returns_report_valid_at_requested_time(postgres):
    mission = _mission(803)
    _claim(803, mission["id"])

    with projects.transaction(803) as cur:
        cur.execute("""INSERT INTO velia_research_syntheses(
            synthesis_id,mission_id,user_id,evidence_hash,status,source_ids_json,
            result_json,safety_json,provider,created_at)
            VALUES(%s,%s,%s,%s,'completed','[]',%s,%s,'test','2026-09-18 08:00:00')""",
            (
                "timeline-synthesis-803",
                mission["id"],
                803,
                "evidence-803",
                json.dumps({
                    "summary": "Baseline",
                    "confidence": "uncertain",
                    "evidence_assessment": [],
                    "contradictions": [],
                    "limitations": [],
                    "hypotheses": [],
                    "open_questions": [],
                }),
                json.dumps({"decision": "allowed", "read_only_only": False}),
            ))
        cur.execute("""INSERT INTO velia_research_reports(
            report_id,mission_id,user_id,synthesis_id,evidence_hash,report_hash,
            report_json,created_at)
            VALUES
            (%s,%s,%s,%s,%s,%s,%s,'2026-09-18 10:00:00'),
            (%s,%s,%s,%s,%s,%s,%s,'2026-09-19 10:00:00')""",
            (
                "report-v8-803", mission["id"], 803, "timeline-synthesis-803",
                "evidence-803", "8" * 64, json.dumps({"version": 8}),
                "report-v9-803", mission["id"], 803, "timeline-synthesis-803",
                "evidence-803", "9" * 64, json.dumps({"version": 9}),
            ))

    midday = timeline.report_as_of(
        803, mission["id"], "2026-09-18T18:00:00Z"
    )
    assert midday["report"]["id"] == "report-v8-803"
    assert midday["report"]["version"] == 8

    later = timeline.report_as_of(
        803, mission["id"], "2026-09-19T12:00:00Z"
    )
    assert later["report"]["id"] == "report-v9-803"
    assert later["report"]["version"] == 9

    before = timeline.report_as_of(
        803, mission["id"], "2026-09-18T09:00:00Z"
    )
    assert before["report"] is None


def test_owner_isolation_for_timeline_and_alerts(postgres):
    mission = _mission(804)
    claim, _ = _claim(804, mission["id"])
    with pytest.raises(projects.ProjectError, match="research_claim_not_found"):
        timeline.claim_timeline(805, claim["id"])
    with pytest.raises(projects.ProjectError, match="research_mission_not_found"):
        timeline.list_alerts(805, mission["id"])
