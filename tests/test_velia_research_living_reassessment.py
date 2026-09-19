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
from services import velia_research_living_reassessment_service as reassess
from services import velia_research_meta_analysis_service as meta
from services import velia_research_reasoning_service as reasoning
from services import velia_research_report_service as reports
from services import velia_research_systematic_review_service as systematic


def test_reassessment_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_LITERATURE_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_CLAIM_LEDGER_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_META_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_SYSTEMATIC_REVIEW_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_LIVING_RESEARCH_ENABLED", "true")
    monkeypatch.delenv("VELIA_RESEARCH_LIVING_REASSESSMENT_ENABLED", raising=False)
    monkeypatch.delenv("VELIA_RESEARCH_LIVING_REASSESSMENT_WORKER_ENABLED", raising=False)
    state = reassess.status()
    assert state["enabled"] is False
    assert state["worker_enabled"] is False
    assert state["full_text_verified_extraction_required_for_new_meta_evidence"] is True
    assert state["llm_guessed_effect_sizes_allowed"] is False
    assert state["new_papers_auto_included"] is False
    assert state["original_systematic_review_mutated"] is False
    assert state["original_meta_snapshot_mutated"] is False
    assert state["old_report_mutated"] is False
    assert state["claim_promotion_allowed"] is False
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

    schema = "research_reassess_test_" + uuid.uuid4().hex
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
        "VELIA_RESEARCH_LIVING_REASSESSMENT_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("VELIA_RESEARCH_LIVING_RESEARCH_WORKER_ENABLED", "false")
    monkeypatch.setenv("VELIA_RESEARCH_LIVING_REASSESSMENT_WORKER_ENABLED", "false")

    chat.ensure_velia_chat_tables()
    projects.ensure_tables()
    center.ensure_tables()
    literature.ensure_tables()
    reasoning.ensure_tables()
    claims.ensure_tables()
    systematic.ensure_tables()
    meta.ensure_tables()
    living.ensure_tables()
    reassess.ensure_tables()
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
        {"goal": "Maintain and reassess a living evidence claim", "title": "Reassessment"},
        "reassessment-mission-" + str(uid),
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


def _review(uid, mission_id, claim_id):
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
            "inclusion": ["Eligible population, exposure and measured outcome"],
            "exclusion": ["Wrong population, exposure, comparator, outcome, or design"],
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
    source_id = f"reassess-source-{uid}-{index}"
    query_hash = review["search_plan"]["query_hashes"][0]
    doi = doi or f"10.9900/reassess.{uid}.{index}"
    title = title or f"Eligible baseline source {index}"
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
                f"reassess-ext-{uid}-{index}", doi, title,
                f"reassess-hash-{uid}-{index}",
            ))
    return source_id


def _include(uid, review_id, source_id):
    systematic.screen_source(uid, review_id, source_id, {
        "stage": "title_abstract",
        "decision": "include",
        "reason_code": "",
        "note": "Eligible at title/abstract screen.",
    })
    systematic.screen_source(uid, review_id, source_id, {
        "stage": "eligibility",
        "decision": "include",
        "reason_code": "",
        "note": "Eligible under the frozen protocol.",
    })


def _risk(level="low"):
    return {
        "domains": {
            "selection": level,
            "measurement": level,
            "confounding": level,
            "missing_data": level,
            "reporting": level,
        },
        "note": "Structured full-text risk-of-bias assessment.",
    }


def _study(uid, claim_id, source_id, r):
    return meta.register_study(uid, claim_id, {
        "source_id": source_id,
        "study_design": "cohort",
        "effect_type": "correlation",
        "statistics": {"r": r, "n": 180},
        "risk_of_bias": _risk(),
    })


def _seed_synthesis(uid, mission_id):
    with projects.transaction(uid) as cur:
        cur.execute("""INSERT INTO velia_research_syntheses(
            synthesis_id,mission_id,user_id,evidence_hash,status,source_ids_json,
            result_json,safety_json,provider)
            VALUES(%s,%s,%s,%s,'completed','[]',%s,%s,'test')""",
            (
                "reassess-synthesis-" + str(uid),
                str(mission_id),
                int(uid),
                "reassess-evidence-" + str(uid),
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


def _fresh(query, doi="10.9901/new.paper", r_title="Long-term exposure measured outcome in adults"):
    return {
        "query": query,
        "query_hash": systematic._query_hash(query),
        "partial": False,
        "provider_gaps": [],
        "providers": ["crossref"],
        "safety": {"decision": "allowed"},
        "sources": [{
            "provider": "crossref",
            "external_id": doi,
            "doi": doi,
            "title": r_title,
            "authors": ["Researcher"],
            "published_year": 2026,
            "venue": "Journal",
            "source_type": "journal-article",
            "evidence_hint": "observational",
            "url": "https://doi.org/" + doi,
            "excerpt": "Abstract metadata is not accepted as a verified effect extraction.",
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
        "coverage_note": "No indexed update.",
    }


def _setup_baseline(uid):
    mission = _mission(uid)
    hypothesis = _hypothesis(uid, mission["id"])
    claim = _claim(uid, mission["id"], hypothesis["id"])
    review = _review(uid, mission["id"], claim["id"])
    s1 = _seed_source(uid, review, 1)
    s2 = _seed_source(uid, review, 2)
    _include(uid, review["id"], s1)
    _include(uid, review["id"], s2)
    _study(uid, claim["id"], s1, 0.30)
    _study(uid, claim["id"], s2, 0.34)
    meta_snapshot = meta.analyze_claim(uid, claim["id"])
    _seed_synthesis(uid, mission["id"])
    report = reports.build_report(uid, mission["id"])
    return mission, claim, review, s1, s2, meta_snapshot, report


def test_new_work_pauses_without_verified_extraction(postgres, monkeypatch):
    mission, claim, review, _s1, _s2, _meta, old_report = _setup_baseline(801)
    monkeypatch.setattr(literature, "live_discover", lambda q, max_results=12: _fresh(q))
    monkeypatch.setattr(literature, "verify_doi_status", _verified)

    scan = living.scan_review(801, review["id"])
    run = reassess.enqueue_for_scan(801, scan["id"])
    before = claims.latest_snapshot(801, claim["id"])
    result = reassess.run_now(801, run["id"])
    after = claims.latest_snapshot(801, claim["id"])

    assert result["status"] == "awaiting_extraction"
    assert result["result"]["awaiting_extractions"][0]["claim_id"] == claim["id"]
    assert result["result"]["policy"]["effect_size_guessing_allowed"] is False
    assert before["id"] == after["id"]
    assert reports.get_report(801, old_report["id"])["report_hash"] == old_report["report_hash"]


def test_extraction_requires_full_text_verified_provenance(postgres, monkeypatch):
    mission, claim, review, _s1, _s2, _meta, _report = _setup_baseline(802)
    monkeypatch.setattr(literature, "live_discover", lambda q, max_results=12: _fresh(q, "10.802/new"))
    monkeypatch.setattr(literature, "verify_doi_status", _verified)
    scan = living.scan_review(802, review["id"])
    run = reassess.enqueue_for_scan(802, scan["id"])
    waiting = reassess.run_now(802, run["id"])
    fp = waiting["result"]["awaiting_extractions"][0]["publication_fingerprint"]

    with pytest.raises(
        projects.ProjectError,
        match="research_living_full_text_verified_extraction_required",
    ):
        reassess.submit_extraction(802, run["id"], {
            "publication_fingerprint": fp,
            "claim_id": claim["id"],
            "decision": "include",
            "exclusion_reason": "",
            "study_design": "cohort",
            "effect_type": "correlation",
            "statistics": {"r": 0.42, "n": 220},
            "risk_of_bias": _risk(),
            "provenance": {
                "evidence_basis": "abstract_only",
                "extracted_by": "trusted_agent",
                "source_text_hash": "1" * 64,
                "locator": "abstract",
                "note": "Not sufficient for living pooled evidence.",
            },
        })


def test_verified_new_evidence_recomputes_pool_without_promoting_stage(postgres, monkeypatch):
    mission, claim, review, _s1, _s2, old_meta, old_report = _setup_baseline(803)
    original_claim = claims.latest_snapshot(803, claim["id"])
    monkeypatch.setattr(literature, "live_discover", lambda q, max_results=12: _fresh(q, "10.803/new"))
    monkeypatch.setattr(literature, "verify_doi_status", _verified)

    scan = living.scan_review(803, review["id"])
    run = reassess.enqueue_for_scan(803, scan["id"])
    waiting = reassess.run_now(803, run["id"])
    fp = waiting["result"]["awaiting_extractions"][0]["publication_fingerprint"]
    extraction = reassess.submit_extraction(803, run["id"], {
        "publication_fingerprint": fp,
        "claim_id": claim["id"],
        "decision": "include",
        "exclusion_reason": "",
        "study_design": "cohort",
        "effect_type": "correlation",
        "statistics": {"r": 0.44, "n": 240},
        "risk_of_bias": _risk(),
        "provenance": {
            "evidence_basis": "full_text_verified",
            "extracted_by": "trusted_agent",
            "source_text_hash": "2" * 64,
            "locator": "Results section, correlation analysis",
            "note": "Structured numeric extraction verified against full text.",
        },
    })
    assert extraction["provenance"]["full_text_fetched_by_velia"] is False

    completed = reassess.run_now(803, run["id"])
    assert completed["status"] == "completed"
    result = completed["result"]
    claim_result = result["claim_results"][0]
    assert claim_result["living_meta"]["available"] is True
    assert claim_result["living_meta"]["new_extraction_count"] == 1
    assert claim_result["living_meta"]["calibration"]["claim_promotion_allowed"] is False
    assert claim_result["claim_snapshot"]["stage"] == original_claim["stage"]
    assert "new_verified_external_evidence_added" in result["scientific_diff"][0]["changes"]

    assert meta.get_snapshot(803, old_meta["id"])["evidence_hash"] == old_meta["evidence_hash"]
    persisted_old = reports.get_report(803, old_report["id"])
    assert persisted_old["report_hash"] == old_report["report_hash"]
    new_report = reports.get_report(803, completed["new_report_id"])
    assert new_report["report"]["version"] == 9
    assert new_report["report"]["provenance"]["immutable_living_reassessment_id"] == completed["id"]
    assert new_report["report"]["provenance"]["immutable_living_reassessment_hash"] == completed["result_hash"]
    assert new_report["report"]["provenance"]["revision_of_report_id"] == old_report["id"]
    assert new_report["report"]["safety"]["living_reassessment_claim_promotion_allowed"] is False
    assert new_report["report"]["safety"]["living_research_reassessment_pending"] is False
    assert new_report["report"]["conclusion"]["confidence"] == "claim_ledger_bounded_after_living_reassessment"

    rebuilt = reports.build_report(803, mission["id"])
    assert rebuilt["id"] == new_report["id"]
    assert rebuilt["report_hash"] == new_report["report_hash"]


def test_retraction_removes_study_only_from_living_pool(postgres, monkeypatch):
    mission, claim, review, s1, s2, old_meta, old_report = _setup_baseline(804)
    before_meta_hash = old_meta["evidence_hash"]
    before_report_hash = old_report["report_hash"]

    monkeypatch.setattr(
        literature, "live_discover",
        lambda q, max_results=12: {**_fresh(q, "10.804/none"), "sources": []},
    )

    def verify(doi):
        if doi.endswith(".1"):
            return {
                "doi": doi,
                "registered": True,
                "status": "retracted_or_withdrawn",
                "updates": [{
                    "type": "retraction",
                    "doi": "10.804/retraction",
                    "label": "Retraction",
                    "source": "retraction-watch",
                }],
                "provider": "crossref",
                "metadata_hash": "b" * 64,
                "coverage_note": "Indexed retraction.",
            }
        return _verified(doi)

    monkeypatch.setattr(literature, "verify_doi_status", verify)
    scan = living.scan_review(804, review["id"])
    run = reassess.enqueue_for_scan(804, scan["id"])
    completed = reassess.run_now(804, run["id"])

    assert completed["status"] == "completed"
    pooled = completed["result"]["claim_results"][0]["living_meta"]
    assert s1 in pooled["removed_source_ids"]
    assert pooled["base_study_count"] == 2
    assert pooled["retained_study_count"] == 1
    assert pooled["available"] is False
    assert pooled["calibration"]["claim_action"] == "caution"
    assert "retracted_or_withdrawn_source_removed_from_living_pool" in pooled["calibration"]["reasons"]

    assert meta.get_snapshot(804, old_meta["id"])["evidence_hash"] == before_meta_hash
    assert reports.get_report(804, old_report["id"])["report_hash"] == before_report_hash
    assert completed["new_report_id"] != old_report["id"]


def test_objectively_ineligible_candidate_needs_no_extraction(postgres, monkeypatch):
    mission, claim, review, _s1, _s2, _meta, _report = _setup_baseline(805)
    result = _fresh("query", "10.805/old")
    result["sources"][0]["published_year"] = 1990
    monkeypatch.setattr(literature, "live_discover", lambda q, max_results=12: {**result, "query": q, "query_hash": systematic._query_hash(q)})
    monkeypatch.setattr(literature, "verify_doi_status", _verified)

    scan = living.scan_review(805, review["id"])
    run = reassess.enqueue_for_scan(805, scan["id"])
    completed = reassess.run_now(805, run["id"])
    assert completed["status"] == "completed"
    assessment = completed["result"]["candidate_assessments"][0]
    assert assessment["decision"] == "auto_exclude_objective_filter"
    assert "date_out_of_range" in assessment["reasons"]
    assert completed["result"]["affected_claim_count"] == 0


def test_reassessment_worker_claim_and_owner_isolation(postgres, monkeypatch):
    mission, claim, review, _s1, _s2, _meta, _report = _setup_baseline(806)
    monkeypatch.setattr(literature, "live_discover", lambda q, max_results=12: _fresh(q, "10.806/new"))
    monkeypatch.setattr(literature, "verify_doi_status", _verified)
    scan = living.scan_review(806, review["id"])
    run = reassess.enqueue_for_scan(806, scan["id"])

    with pytest.raises(projects.ProjectError, match="research_living_reassessment_not_found"):
        reassess.get_run(807, run["id"])

    monkeypatch.setenv("VELIA_RESEARCH_LIVING_REASSESSMENT_WORKER_ENABLED", "true")
    claimed = reassess.claim_next("reassessment-worker")
    assert claimed["id"] == run["id"]
    result = reassess.execute_claimed(claimed, "reassessment-worker")
    assert result["status"] == "awaiting_extraction"
