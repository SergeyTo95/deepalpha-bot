import json
import os
import uuid

import pytest

from services import velia_chat_service as chat
from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_claim_service as claims
from services import velia_research_literature_service as literature
from services import velia_research_living_service as living
from services import velia_research_meta_analysis_service as meta
from services import velia_research_reasoning_service as reasoning
from services import velia_research_report_service as reports
from services import velia_research_systematic_review_service as systematic


def test_living_research_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_LITERATURE_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_SYSTEMATIC_REVIEW_ENABLED", "true")
    monkeypatch.delenv("VELIA_RESEARCH_LIVING_RESEARCH_ENABLED", raising=False)
    monkeypatch.delenv("VELIA_RESEARCH_LIVING_RESEARCH_WORKER_ENABLED", raising=False)
    state = living.status()
    assert state["enabled"] is False
    assert state["worker_enabled"] is False
    assert state["frozen_queries_only"] is True
    assert state["new_papers_auto_included"] is False
    assert state["old_reports_mutated"] is False
    assert state["old_graphs_mutated"] is False
    assert state["claim_stage_promotion_allowed"] is False
    assert state["claim_stage_demotion_allowed"] is False
    assert state["arbitrary_code"] is False
    assert state["arbitrary_shell"] is False
    assert state["arbitrary_file_access"] is False
    assert state["arbitrary_url_fetch"] is False


def test_crossref_update_metadata_is_bounded_citation_verification(monkeypatch):
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_LITERATURE_ENABLED", "true")
    calls = []

    def fake_fetch(url, *, params):
        calls.append((url, params))
        return {
            "message": {
                "items": [{
                    "DOI": "10.1000/example",
                    "title": ["Example paper"],
                    "type": "journal-article",
                    "publisher": "Example Publisher",
                    "update-to": [{
                        "DOI": "10.1000/retraction",
                        "type": "retraction",
                        "label": "Retraction",
                        "source": "retraction-watch",
                    }],
                }]
            }
        }

    monkeypatch.setattr(literature, "_fetch_json", fake_fetch)
    result = literature.verify_doi_status("10.1000/example")
    assert calls[0][0] == literature.CROSSREF_URL
    assert calls[0][1]["filter"] == "doi:10.1000/example"
    assert result["registered"] is True
    assert result["status"] == "retracted_or_withdrawn"
    assert result["updates"][0]["source"] == "retraction-watch"
    assert len(result["metadata_hash"]) == 64


@pytest.fixture
def postgres(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration is required in CI")
    import psycopg2

    schema = "research_living_test_" + uuid.uuid4().hex
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
        "VELIA_RESEARCH_CLAIM_LEDGER_ENABLED",
        "VELIA_RESEARCH_META_ANALYSIS_ENABLED",
        "VELIA_RESEARCH_SYSTEMATIC_REVIEW_ENABLED",
        "VELIA_RESEARCH_LIVING_RESEARCH_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("VELIA_RESEARCH_LIVING_RESEARCH_WORKER_ENABLED", "false")

    chat.ensure_velia_chat_tables()
    projects.ensure_tables()
    center.ensure_tables()
    literature.ensure_tables()
    reasoning.ensure_tables()
    claims.ensure_tables()
    systematic.ensure_tables()
    meta.ensure_tables()
    living.ensure_tables()
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
        {"goal": "Maintain a living evidence review", "title": "Living research"},
        "living-mission-" + str(uid),
    )


def _hypothesis(uid, mission_id):
    return center.add_hypothesis(
        uid,
        mission_id,
        "Positive relationship",
        "The preregistered relationship is positive.",
    )


def _claim(uid, mission_id, hypothesis_id):
    return claims.create_claim(uid, mission_id, {
        "hypothesis_id": hypothesis_id,
        "statement": "Long-term exposure is associated with a positive measured outcome.",
        "expected_direction": "positive",
        "analysis_kind": "correlation",
        "source_ids": [],
    })


def _review(uid, mission_id, claim_id=None):
    return systematic.create_protocol(uid, mission_id, {
        "claim_id": claim_id,
        "framework": "peco",
        "question": {
            "population": "Adults in eligible published studies",
            "exposure": "Long-term exposure",
            "comparator": "Lower or absent exposure",
            "outcome": "Measured outcome",
        },
        "criteria": {
            "inclusion": [
                "Eligible population with the preregistered exposure and outcome",
            ],
            "exclusion": [
                "Wrong population, exposure, comparator, outcome, or design",
            ],
            "study_designs": ["cohort", "other"],
            "evidence_hints": ["observational"],
            "year_from": 2000,
            "year_to": 2030,
        },
        "search_plan": {
            "queries": ["long-term exposure measured outcome adults"],
            "max_results_per_query": 12,
        },
    })


def _seed_source(uid, review, index, *, doi=None, title=None):
    source_id = f"living-source-{uid}-{index}"
    query_hash = review["search_plan"]["query_hashes"][0]
    doi = doi if doi is not None else f"10.7777/living.{uid}.{index}"
    title = title or f"Baseline eligible source {index}"
    with projects.transaction(uid) as cur:
        cur.execute("""INSERT INTO velia_research_literature_queries(
            mission_id,user_id,query_hash,query_text,status,safety_json,providers_json,result_count)
            VALUES(%s,%s,%s,%s,'completed','{}','[]',12)
            ON CONFLICT(mission_id,user_id,query_hash) DO NOTHING""",
            (review["mission_id"], int(uid), query_hash, review["search_plan"]["queries"][0]))
        cur.execute("""INSERT INTO velia_research_sources(
            source_id,mission_id,user_id,query_hash,ordinal,provider,external_id,doi,title,
            authors_json,published_year,venue,source_type,evidence_hint,source_url,excerpt,
            citation_count,metadata_hash)
            VALUES(%s,%s,%s,%s,%s,'crossref',%s,%s,%s,'[]',2025,'Journal','article',
                   'observational','','',10,%s)""",
            (
                source_id, review["mission_id"], int(uid), query_hash, int(index),
                f"living-ext-{uid}-{index}", doi, title, f"living-hash-{uid}-{index}",
            ))
    return source_id


def _include(uid, review_id, source_id):
    systematic.screen_source(uid, review_id, source_id, {
        "stage": "title_abstract",
        "decision": "include",
        "reason_code": "",
        "note": "Eligible at metadata screening.",
    })
    systematic.screen_source(uid, review_id, source_id, {
        "stage": "eligibility",
        "decision": "include",
        "reason_code": "",
        "note": "Eligible under frozen criteria.",
    })


def _risk():
    return {
        "domains": {
            "selection": "low",
            "measurement": "low",
            "confounding": "low",
            "missing_data": "low",
            "reporting": "low",
        },
        "note": "Structured assessment.",
    }


def _seed_synthesis(uid, mission_id):
    with projects.transaction(uid) as cur:
        cur.execute("""INSERT INTO velia_research_syntheses(
            synthesis_id,mission_id,user_id,evidence_hash,status,source_ids_json,
            result_json,safety_json,provider)
            VALUES(%s,%s,%s,%s,'completed','[]',%s,%s,'test')""",
            (
                "living-synthesis-" + str(uid),
                str(mission_id),
                int(uid),
                "living-evidence-" + str(uid),
                json.dumps({
                    "summary": "Existing literature synthesis.",
                    "confidence": "moderate",
                    "evidence_assessment": [],
                    "contradictions": [],
                    "limitations": [],
                    "hypotheses": [],
                    "open_questions": [],
                }),
                json.dumps({"decision": "allowed", "read_only_only": False}),
            ))


def _fresh_result(query, new_doi="10.8888/new.paper"):
    return {
        "query": query,
        "query_hash": systematic._query_hash(query),
        "partial": False,
        "provider_gaps": [],
        "providers": ["crossref", "europe_pmc"],
        "safety": {"decision": "allowed"},
        "sources": [{
            "provider": "crossref",
            "external_id": new_doi,
            "doi": new_doi,
            "title": "Long-term exposure and measured outcome in adults",
            "authors": ["A Researcher"],
            "published_year": 2026,
            "venue": "Journal",
            "source_type": "journal-article",
            "evidence_hint": "observational",
            "url": "https://doi.org/" + new_doi,
            "excerpt": "",
            "citation_count": 0,
        }],
    }


def _verified(doi):
    return {
        "doi": doi,
        "registered": True,
        "status": "verified_no_indexed_update",
        "updates": [],
        "provider": "crossref",
        "metadata_hash": "a" * 64,
        "coverage_note": "No indexed update in this test.",
    }


def test_living_scan_detects_new_work_once_and_versions_graph(postgres, monkeypatch):
    mission = _mission(701)
    hypothesis = _hypothesis(701, mission["id"])
    claim = _claim(701, mission["id"], hypothesis["id"])
    review = _review(701, mission["id"], claim["id"])
    baseline = _seed_source(701, review, 1)
    _include(701, review["id"], baseline)

    monkeypatch.setattr(
        literature, "live_discover",
        lambda query, max_results=12: _fresh_result(query),
    )
    monkeypatch.setattr(literature, "verify_doi_status", _verified)

    first = living.scan_review(701, review["id"])
    assert first["material_status"] == "new_evidence"
    assert first["new_work_count"] == 1
    assert first["scan"]["new_works"][0]["impacted_claims"][0]["claim_id"] == claim["id"]
    assert first["graph_revision"]["base_graph_hash"] == first["base_graph_hash"]
    assert len(first["graph_revision"]["graph_hash"]) == 64

    second = living.scan_review(701, review["id"])
    assert second["prior_scan_id"] == first["id"]
    assert second["material_status"] == "no_material_change"
    assert second["new_work_count"] == 0
    assert second["graph_revision"]["graph_hash"] != first["graph_revision"]["graph_hash"]


def test_retraction_alert_maps_to_used_claim_without_changing_stage(postgres, monkeypatch):
    mission = _mission(702)
    hypothesis = _hypothesis(702, mission["id"])
    claim = _claim(702, mission["id"], hypothesis["id"])
    review = _review(702, mission["id"], claim["id"])
    source = _seed_source(702, review, 1, doi="10.702/retracted")
    _include(702, review["id"], source)

    meta.register_study(702, claim["id"], {
        "source_id": source,
        "study_design": "cohort",
        "effect_type": "correlation",
        "statistics": {"r": 0.3, "n": 160},
        "risk_of_bias": _risk(),
    })
    before = claims.refresh_claim(702, claim["id"])

    monkeypatch.setattr(
        literature, "live_discover",
        lambda query, max_results=12: {
            **_fresh_result(query, "10.702/retracted"),
            "sources": [],
        },
    )
    monkeypatch.setattr(literature, "verify_doi_status", lambda doi: {
        "doi": doi,
        "registered": True,
        "status": "retracted_or_withdrawn",
        "updates": [{
            "type": "retraction",
            "doi": "10.702/retraction.notice",
            "label": "Retraction",
            "source": "retraction-watch",
        }],
        "provider": "crossref",
        "metadata_hash": "b" * 64,
        "coverage_note": "Indexed retraction.",
    })

    scan = living.scan_review(702, review["id"])
    after = claims.refresh_claim(702, claim["id"])
    assert scan["material_status"] == "citation_integrity_alert"
    assert scan["citation_alert_count"] == 1
    check = scan["citation_checks"][0]
    assert check["status"] == "retracted_or_withdrawn"
    assert check["verification"]["impacted_claim_ids"] == [claim["id"]]
    assert before["stage"] == after["stage"]
    assert before["evidence_hash"] == after["evidence_hash"]


def test_old_report_remains_immutable_and_new_report_is_v8_revision(postgres, monkeypatch):
    mission = _mission(703)
    hypothesis = _hypothesis(703, mission["id"])
    claim = _claim(703, mission["id"], hypothesis["id"])
    review = _review(703, mission["id"], claim["id"])
    source = _seed_source(703, review, 1)
    _include(703, review["id"], source)
    _seed_synthesis(703, mission["id"])

    before = reports.build_report(703, mission["id"])
    assert before["report"]["version"] == 7

    monkeypatch.setattr(
        literature, "live_discover",
        lambda query, max_results=12: _fresh_result(query, "10.703/new"),
    )
    monkeypatch.setattr(literature, "verify_doi_status", _verified)
    scan = living.scan_review(703, review["id"])
    after = reports.build_report(703, mission["id"])

    persisted_before = reports.get_report(703, before["id"])
    assert persisted_before["report_hash"] == before["report_hash"]
    assert persisted_before["report"] == before["report"]
    assert after["report"]["version"] == 8
    assert after["id"] != before["id"]
    assert after["report"]["provenance"]["revision_of_report_id"] == before["id"]
    assert after["report"]["provenance"]["revision_of_report_hash"] == before["report_hash"]
    assert after["report"]["provenance"]["immutable_living_scan_id"] == scan["id"]
    assert after["report"]["provenance"]["immutable_living_graph_revision_hash"] == scan["graph_revision"]["graph_hash"]
    assert after["report"]["safety"]["living_research_old_report_mutation"] is False
    assert after["report"]["safety"]["living_research_auto_claim_stage_change"] is False
    assert after["report"]["conclusion"]["confidence"] == "claim_ledger_bounded_with_living_reassessment_pending"


def test_watch_is_bounded_and_scheduled_scan_advances_next_due(postgres, monkeypatch):
    mission = _mission(704)
    review = _review(704, mission["id"])
    source = _seed_source(704, review, 1)
    _include(704, review["id"], source)

    with pytest.raises(projects.ProjectError, match="invalid_research_living_watch"):
        living.configure_watch(704, review["id"], {"cadence_hours": 1, "active": True})

    watch = living.configure_watch(704, review["id"], {
        "cadence_hours": 24,
        "active": True,
    })
    assert watch["active"] is True
    monkeypatch.setenv("VELIA_RESEARCH_LIVING_RESEARCH_WORKER_ENABLED", "true")
    monkeypatch.setattr(
        literature, "live_discover",
        lambda query, max_results=12: {
            **_fresh_result(query, "10.704/none"),
            "sources": [],
        },
    )
    monkeypatch.setattr(literature, "verify_doi_status", _verified)

    result = living.run_due_once("living-test-worker")
    assert result is not None
    assert result["trigger_kind"] == "scheduled"
    updated = living.get_watch(704, review["id"])
    assert updated["last_scan_at"] is not None
    assert updated["lease_owner"] is None
    assert updated["lease_until"] is None


def test_verification_outage_is_partial_not_false_integrity_alert(postgres, monkeypatch):
    mission = _mission(705)
    review = _review(705, mission["id"])
    source = _seed_source(705, review, 1)
    _include(705, review["id"], source)

    monkeypatch.setattr(
        literature, "live_discover",
        lambda query, max_results=12: {
            **_fresh_result(query, "10.705/none"),
            "sources": [],
        },
    )

    def unavailable(_doi):
        raise projects.ProjectError("research_citation_verification_unavailable", 502)

    monkeypatch.setattr(literature, "verify_doi_status", unavailable)
    scan = living.scan_review(705, review["id"])
    assert scan["material_status"] == "no_material_change"
    assert scan["citation_alert_count"] == 0
    assert scan["citation_checks"][0]["status"] == "verification_unavailable"


def test_living_scan_owner_isolation(postgres, monkeypatch):
    mission = _mission(706)
    review = _review(706, mission["id"])
    monkeypatch.setattr(
        literature, "live_discover",
        lambda query, max_results=12: _fresh_result(query),
    )
    scan = living.scan_review(706, review["id"])
    with pytest.raises(projects.ProjectError, match="research_living_scan_not_found"):
        living.get_scan(707, scan["id"])
