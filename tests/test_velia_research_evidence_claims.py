import json
import os
import uuid

import pytest

from services import velia_chat_service as chat
from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_evidence_claim_service as evidence_claims
from services import velia_research_literature_service as literature
from services import velia_research_reasoning_service as reasoning


@pytest.fixture
def postgres(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration is required in CI")
    import psycopg2

    schema = "evidence_claims_test_" + uuid.uuid4().hex
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
    try:
        yield connect
    finally:
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _seed_source(monkeypatch, user_id, mission_id):
    monkeypatch.setattr(literature.search_quality, "plan_query", lambda **kwargs: {
        "query": "prospective biomarker early detection",
        "model_planned": True,
        "quality_version": literature.search_quality.QUALITY_VERSION,
    })
    monkeypatch.setattr(literature, "_europe_pmc", lambda query, limit: [{
        "provider": "europe_pmc",
        "external_id": "MED:777",
        "doi": "10.1000/evidence-claim",
        "title": "Prospective biomarker study",
        "authors": ["Researcher A"],
        "published_year": 2026,
        "venue": "Clinical Evidence",
        "source_type": "observational",
        "evidence_hint": "observational",
        "url": "https://europepmc.org/article/MED/777",
        "excerpt": "Prospective evidence with important limitations.",
        "citation_count": 8,
    }])
    monkeypatch.setattr(literature, "_crossref", lambda query, limit: [{
        "provider": "crossref",
        "external_id": "10.1000/evidence-claim",
        "doi": "10.1000/evidence-claim",
        "title": "Prospective biomarker study",
        "authors": ["Researcher A"],
        "published_year": 2026,
        "venue": "Clinical Evidence",
        "source_type": "journal-article",
        "evidence_hint": "observational",
        "url": "https://doi.org/10.1000/evidence-claim",
        "excerpt": "Prospective evidence with important limitations.",
        "citation_count": 8,
    }])
    return literature.collect(user_id, mission_id, max_results=8)


def test_evidence_claims_are_separate_and_source_linked(postgres, monkeypatch):
    mission = center.create_mission(81, {
        "goal": "Evaluate a biomarker for early cancer detection",
        "title": "Biomarker evidence",
    }, "evidence-claims-0001")
    _seed_source(monkeypatch, 81, mission["id"])
    source = literature.list_sources(81, mission["id"])["sources"][0]

    monkeypatch.setattr(reasoning.llm_service, "resolve_text_provider", lambda feature: "kimi")
    monkeypatch.setattr(reasoning.llm_service, "_call_gemini", lambda *a, **k: json.dumps({
        "summary": "The available evidence is encouraging but not definitive.",
        "confidence": "moderate",
        "evidence_assessment": [{
            "source_id": source["id"],
            "stance": "supports",
            "strength": "moderate",
            "notes": "The prospective study supports diagnostic value with cohort limitations.",
        }],
        "evidence_claims": [{
            "statement": "The biomarker shows evidence of diagnostic value in the studied cohort.",
            "verdict": "supported",
            "confidence": "moderate",
            "source_ids": [source["id"]],
            "rationale": "Prospective evidence supports the claim, but external validation is still needed.",
        }],
        "contradictions": [],
        "limitations": ["Single cohort."],
        "hypotheses": [],
        "open_questions": ["Does performance generalize?"],
    }))
    reasoning.synthesize(81, mission["id"])

    result = evidence_claims.list_evidence_claims(81, mission["id"])
    claim = result["evidence_claims"][0]
    assert result["experimental_claim_ledger_separate"] is True
    assert claim["verdict"] == "supported"
    assert claim["confidence"] == "moderate"
    assert claim["source_ids"] == [source["id"]]
    assert claim["sources"][0]["doi"] == "10.1000/evidence-claim"
    assert claim["sources"][0]["url"].startswith("https://")


def test_legacy_synthesis_uses_assessment_fallback(postgres, monkeypatch):
    mission = center.create_mission(82, {
        "goal": "Evaluate older evidence",
        "title": "Legacy synthesis",
    }, "evidence-claims-0002")
    _seed_source(monkeypatch, 82, mission["id"])
    source = literature.list_sources(82, mission["id"])["sources"][0]

    monkeypatch.setattr(reasoning.llm_service, "resolve_text_provider", lambda feature: "kimi")
    monkeypatch.setattr(reasoning.llm_service, "_call_gemini", lambda *a, **k: json.dumps({
        "summary": "Legacy synthesis.",
        "confidence": "low",
        "evidence_assessment": [{
            "source_id": source["id"],
            "stance": "mixed",
            "strength": "low",
            "notes": "Results are mixed and external validation is missing.",
        }],
        "contradictions": [],
        "limitations": [],
        "hypotheses": [],
        "open_questions": [],
    }))
    reasoning.synthesize(82, mission["id"])

    result = evidence_claims.list_evidence_claims(82, mission["id"])
    claim = result["evidence_claims"][0]
    assert claim["origin"] == "legacy_assessment_fallback"
    assert claim["verdict"] == "conflicting"
    assert claim["confidence"] == "low"
