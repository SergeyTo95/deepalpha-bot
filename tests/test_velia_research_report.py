import json
import os
import uuid

import pytest

from services import velia_chat_service as chat
from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_literature_service as literature
from services import velia_research_reasoning_service as reasoning
from services import velia_research_report_service as reports


@pytest.fixture
def postgres(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration is required in CI")
    import psycopg2

    schema = "report_test_" + uuid.uuid4().hex
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
    monkeypatch.setenv("VELIA_RESEARCH_AUTONOMY_ENABLED", "false")
    monkeypatch.setenv("VELIA_RESEARCH_EXPERIMENT_EXECUTION_ENABLED", "false")

    chat.ensure_velia_chat_tables()
    center.ensure_tables()
    literature.ensure_tables()
    reasoning.ensure_tables()
    reports.ensure_tables()
    try:
        yield connect
    finally:
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _seed_medical_evidence(monkeypatch, user_id, mission_id):
    monkeypatch.setattr(literature.search_quality, "plan_query", lambda **kwargs: {
        "query": "cancer therapy randomized trial",
        "model_planned": True,
        "quality_version": literature.search_quality.QUALITY_VERSION,
    })
    monkeypatch.setattr(literature, "_europe_pmc", lambda query, limit: [{
        "provider": "europe_pmc",
        "external_id": "MED:777",
        "doi": "10.1000/report-rct",
        "title": "Randomized controlled trial of a cancer therapy",
        "authors": ["Researcher One", "Researcher Two"],
        "published_year": 2026,
        "venue": "Clinical Journal",
        "source_type": "Randomized Controlled Trial",
        "evidence_hint": "rct",
        "url": "https://europepmc.org/article/MED/777",
        "excerpt": "A bounded clinical trial abstract.",
        "citation_count": 18,
    }])
    monkeypatch.setattr(literature, "_crossref", lambda query, limit: [])
    return literature.collect(user_id, mission_id, max_results=8)


def _synthesize(monkeypatch, user_id, mission_id, model_calls):
    source_id = literature.list_sources(user_id, mission_id)["sources"][0]["id"]
    monkeypatch.setattr(reasoning.llm_service, "resolve_text_provider", lambda feature: "gemini")

    def model(prompt, **kwargs):
        model_calls.append(kwargs["feature"])
        return json.dumps({
            "summary": "The persisted trial supports further research but replication is needed.",
            "confidence": "moderate",
            "evidence_assessment": [{
                "source_id": source_id,
                "stance": "supports",
                "strength": "moderate",
                "notes": "Single RCT in the current evidence snapshot.",
            }],
            "contradictions": [],
            "limitations": ["Only one RCT is available in this snapshot."],
            "hypotheses": [{
                "title": "The reported effect is reproducible in an independent cohort",
                "rationale": "Replication would test robustness.",
                "testable_prediction": "An independent preregistered study shows a consistent direction.",
            }],
            "open_questions": ["Does the effect generalize across populations?"],
        }, ensure_ascii=False)

    monkeypatch.setattr(reasoning.llm_service, "_call_gemini", model)
    return reasoning.synthesize(user_id, mission_id, 8)


def test_report_is_immutable_idempotent_and_has_no_extra_model_call(postgres, monkeypatch):
    mission = center.create_mission(51, {
        "goal": "Исследуй клинические данные по терапии рака",
        "title": "Oncology report",
    }, "report-mission-0001")
    _seed_medical_evidence(monkeypatch, 51, mission["id"])
    calls = []
    synthesis = _synthesize(monkeypatch, 51, mission["id"], calls)
    assert calls == ["research_center"]

    first = reports.build_report(51, mission["id"])
    second = reports.build_report(51, mission["id"])

    assert first["id"] == second["id"]
    assert first["report_hash"] == second["report_hash"]
    assert first["synthesis_id"] == synthesis["id"]
    assert calls == ["research_center"]

    report = first["report"]
    assert report["conclusion"]["confidence"] == "moderate"
    assert report["evidence"]["source_count"] == 1
    assert report["evidence"]["profile"] == {"rct": 1}
    assert report["evidence"]["citations"][0]["doi"] == "10.1000/report-rct"
    assert report["provenance"]["synthesis_id"] == synthesis["id"]
    assert report["medical_boundary"]["patient_specific_diagnosis"] is False
    assert report["medical_boundary"]["patient_specific_prescribing"] is False
    assert report["safety"]["operational_harmful_instructions_allowed"] is False


def test_report_requires_completed_synthesis(postgres):
    mission = center.create_mission(52, {
        "goal": "Study battery materials",
        "title": "Materials",
    }, "report-mission-0002")
    with pytest.raises(projects.ProjectError, match="research_synthesis_required"):
        reports.build_report(52, mission["id"])


def test_report_is_owner_scoped(postgres, monkeypatch):
    mission = center.create_mission(53, {
        "goal": "Исследуй клинические данные по терапии рака",
        "title": "Owner scoped report",
    }, "report-mission-0003")
    _seed_medical_evidence(monkeypatch, 53, mission["id"])
    calls = []
    _synthesize(monkeypatch, 53, mission["id"], calls)
    report = reports.build_report(53, mission["id"])

    with pytest.raises(projects.ProjectError, match="research_report_not_found"):
        reports.get_report(54, report["id"])
    with pytest.raises(projects.ProjectError, match="research_mission_not_found"):
        reports.list_reports(54, mission["id"])


def test_sensitive_research_report_preserves_read_only_safety(postgres, monkeypatch):
    mission = center.create_mission(55, {
        "goal": "Исследуй обнаружение ransomware и защиту корпоративной сети",
        "title": "Defensive security",
    }, "report-mission-0004")
    assert mission["safety"]["read_only_only"] is True

    monkeypatch.setattr(literature.search_quality, "plan_query", lambda **kwargs: {
        "query": "defensive ransomware detection",
        "model_planned": True,
        "quality_version": literature.search_quality.QUALITY_VERSION,
    })
    monkeypatch.setattr(literature, "_crossref", lambda query, limit: [{
        "provider": "crossref",
        "external_id": "10.1000/defense",
        "doi": "10.1000/defense",
        "title": "Defensive ransomware detection",
        "authors": [],
        "published_year": 2026,
        "venue": "Security Journal",
        "source_type": "journal-article",
        "evidence_hint": "unknown",
        "url": "https://doi.org/10.1000/defense",
        "excerpt": "Detection and mitigation evidence.",
        "citation_count": 2,
    }])
    literature.collect(55, mission["id"])

    source_id = literature.list_sources(55, mission["id"])["sources"][0]["id"]
    monkeypatch.setattr(reasoning.llm_service, "resolve_text_provider", lambda feature: "gemini")
    monkeypatch.setattr(reasoning.llm_service, "_call_gemini", lambda *a, **k: json.dumps({
        "summary": "Evidence discusses defensive detection and mitigation.",
        "confidence": "low",
        "evidence_assessment": [{
            "source_id": source_id,
            "stance": "context",
            "strength": "low",
            "notes": "Defensive scope only.",
        }],
        "contradictions": [],
        "limitations": ["Limited evidence."],
        "hypotheses": [],
        "open_questions": ["Which defensive controls generalize best?"],
    }))
    reasoning.synthesize(55, mission["id"])
    report = reports.build_report(55, mission["id"])
    assert report["report"]["safety"]["mission"]["read_only_only"] is True
