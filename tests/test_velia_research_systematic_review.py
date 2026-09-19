import json
import os
import uuid

import pytest

from services import velia_chat_service as chat
from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_claim_service as claims
from services import velia_research_literature_service as literature
from services import velia_research_meta_analysis_service as meta
from services import velia_research_reasoning_service as reasoning
from services import velia_research_report_service as reports
from services import velia_research_systematic_review_service as systematic


def test_systematic_review_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_LITERATURE_ENABLED", "true")
    monkeypatch.delenv("VELIA_RESEARCH_SYSTEMATIC_REVIEW_ENABLED", raising=False)
    state = systematic.status()
    assert state["enabled"] is False
    assert state["protocol_must_precede_literature_search"] is True
    assert state["immutable_search_plan"] is True
    assert state["meta_analysis_requires_final_include_when_enabled"] is True
    assert state["full_text_fetch_by_velia"] is False
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

    schema = "research_systematic_test_" + uuid.uuid4().hex
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
    ):
        monkeypatch.setenv(name, "true")

    chat.ensure_velia_chat_tables()
    projects.ensure_tables()
    center.ensure_tables()
    literature.ensure_tables()
    reasoning.ensure_tables()
    claims.ensure_tables()
    systematic.ensure_tables()
    meta.ensure_tables()
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
        {"goal": "Evaluate a preregistered evidence question", "title": "Systematic review"},
        "systematic-mission-" + str(uid),
    )


def _hypothesis(uid, mission_id):
    return center.add_hypothesis(
        uid,
        mission_id,
        "Positive association",
        "The preregistered evidence question predicts a positive association.",
    )


def _claim(uid, mission_id, hypothesis_id):
    return claims.create_claim(uid, mission_id, {
        "hypothesis_id": hypothesis_id,
        "statement": "The measured association is positive.",
        "expected_direction": "positive",
        "analysis_kind": "correlation",
        "source_ids": [],
    })


def _protocol(uid, mission_id, claim_id=None, *, query="adult exposure outcome"):
    return systematic.create_protocol(uid, mission_id, {
        "claim_id": claim_id,
        "framework": "peco",
        "question": {
            "population": "Adults in eligible published studies",
            "exposure": "The preregistered exposure",
            "comparator": "Lower or absent exposure",
            "outcome": "The preregistered measured outcome",
        },
        "criteria": {
            "inclusion": [
                "Human or eligible predeclared study population",
                "Reports the preregistered exposure and outcome",
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
            "queries": [query],
            "max_results_per_query": 12,
        },
    })


def _seed_source(uid, review, index, *, doi=None, title=None, year=2025, hint="observational", provider="crossref"):
    source_id = f"sys-source-{uid}-{index}"
    query_hash = review["search_plan"]["query_hashes"][0]
    doi = doi if doi is not None else f"10.4321/systematic.{uid}.{index}"
    title = title or f"Systematic source {index}"
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
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'[]',%s,'Journal','article',%s,
                   '','','0',%s)""",
            (
                source_id, review["mission_id"], int(uid), query_hash, int(index), provider,
                f"sys-ext-{uid}-{index}", doi, title, year, hint,
                f"sys-hash-{uid}-{index}",
            ))
    return source_id


def _include(uid, review_id, source_id):
    systematic.screen_source(uid, review_id, source_id, {
        "stage": "title_abstract",
        "decision": "include",
        "reason_code": "",
        "note": "Meets preregistered metadata screening criteria.",
    })
    return systematic.screen_source(uid, review_id, source_id, {
        "stage": "eligibility",
        "decision": "include",
        "reason_code": "",
        "note": "Eligible under the frozen protocol.",
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
        "note": "Structured risk-of-bias assessment.",
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
                "systematic-synthesis-" + str(uid),
                str(mission_id),
                int(uid),
                "systematic-evidence-" + str(uid),
                json.dumps({
                    "summary": "Contextual literature synthesis only.",
                    "confidence": "moderate",
                    "evidence_assessment": [],
                    "contradictions": [],
                    "limitations": [],
                    "hypotheses": [],
                    "open_questions": [],
                }),
                json.dumps({"decision": "allowed", "read_only_only": False}),
            ))


def test_review_protocol_must_precede_any_search(postgres):
    mission = _mission(601)
    query = "already searched"
    query_hash = systematic._query_hash(query)
    with projects.transaction(601) as cur:
        cur.execute("""INSERT INTO velia_research_literature_queries(
            mission_id,user_id,query_hash,query_text,status,safety_json,providers_json,result_count)
            VALUES(%s,%s,%s,%s,'completed','{}','[]',0)""",
            (mission["id"], 601, query_hash, query))
    with pytest.raises(projects.ProjectError, match="research_review_protocol_must_precede_search"):
        _protocol(601, mission["id"], query=query)


def test_preregistered_search_executes_only_frozen_queries(postgres, monkeypatch):
    mission = _mission(602)
    review = _protocol(602, mission["id"], query="frozen query")
    calls = []

    def fake_collect(user_id, mission_id, query, max_results):
        calls.append((user_id, mission_id, query, max_results))
        return {
            "query_hash": systematic._query_hash(query),
            "partial": False,
            "provider_gaps": [],
            "sources": [],
        }

    monkeypatch.setattr(literature, "collect", fake_collect)
    result = systematic.execute_search(602, review["id"])
    assert calls == [(602, mission["id"], "frozen query", 12)]
    assert result["search_run"]["partial"] is False
    assert result["search_run"]["identified_records"] == 0
    cached = systematic.execute_search(602, review["id"])
    assert cached["cached"] is True
    assert len(calls) == 1


def test_duplicate_publication_and_prisma_flow_are_deterministic(postgres):
    mission = _mission(603)
    review = _protocol(603, mission["id"])
    canonical = _seed_source(603, review, 1, doi="10.5555/same.paper")
    duplicate = _seed_source(603, review, 2, doi="10.5555/same.paper", provider="europe_pmc")
    excluded = _seed_source(603, review, 3)

    _include(603, review["id"], canonical)
    duplicate_action = systematic.screen_source(603, review["id"], duplicate, {
        "stage": "title_abstract",
        "decision": "include",
        "reason_code": "",
        "note": "Would otherwise be eligible.",
    })
    assert duplicate_action["decision"] == "duplicate"
    assert duplicate_action["reason_code"] == "duplicate_publication"
    assert duplicate_action["canonical_source_id"] == canonical

    systematic.screen_source(603, review["id"], excluded, {
        "stage": "title_abstract",
        "decision": "exclude",
        "reason_code": "population_mismatch",
        "note": "Population does not satisfy the preregistered question.",
    })
    flow = systematic.prisma_flow(603, review["id"])
    assert flow["identified_records"] == 3
    assert flow["duplicate_records_detected"] == 1
    assert flow["unique_publications"] == 2
    assert flow["title_abstract_screened"] == 3
    assert flow["title_abstract_excluded"] == 1
    assert flow["eligibility_assessed"] == 1
    assert flow["included_in_review"] == 1
    assert flow["included_source_ids"] == [canonical]


def test_objective_preregistered_filter_blocks_ineligible_include(postgres):
    mission = _mission(604)
    review = _protocol(604, mission["id"])
    old_source = _seed_source(604, review, 1, year=1995)
    with pytest.raises(projects.ProjectError, match="research_review_source_fails_preregistered_filter"):
        systematic.screen_source(604, review["id"], old_source, {
            "stage": "title_abstract",
            "decision": "include",
            "reason_code": "",
            "note": "Attempted include despite objective date filter.",
        })


def test_meta_analysis_accepts_only_final_systematic_includes(postgres):
    mission = _mission(605)
    hypothesis = _hypothesis(605, mission["id"])
    claim = _claim(605, mission["id"], hypothesis["id"])
    review = _protocol(605, mission["id"], claim["id"])
    included_a = _seed_source(605, review, 1)
    included_b = _seed_source(605, review, 2)
    excluded = _seed_source(605, review, 3)
    _include(605, review["id"], included_a)
    _include(605, review["id"], included_b)
    systematic.screen_source(605, review["id"], excluded, {
        "stage": "title_abstract",
        "decision": "include",
        "reason_code": "",
        "note": "Passes first-stage screening.",
    })
    systematic.screen_source(605, review["id"], excluded, {
        "stage": "eligibility",
        "decision": "exclude",
        "reason_code": "outcome_mismatch",
        "note": "Does not report the preregistered outcome.",
    })

    _study(605, claim["id"], included_a, 0.31)
    _study(605, claim["id"], included_b, 0.36)
    with pytest.raises(projects.ProjectError, match="research_meta_source_not_systematically_included"):
        _study(605, claim["id"], excluded, 0.55)

    result = meta.analyze_claim(605, claim["id"])
    assert len(result["study_ids"]) == 2
    assert result["calibration"]["claim_promotion_allowed"] is False


def test_screening_freezes_after_first_meta_snapshot(postgres):
    mission = _mission(606)
    hypothesis = _hypothesis(606, mission["id"])
    claim = _claim(606, mission["id"], hypothesis["id"])
    review = _protocol(606, mission["id"], claim["id"])
    first = _seed_source(606, review, 1)
    second = _seed_source(606, review, 2)
    _include(606, review["id"], first)
    _include(606, review["id"], second)
    _study(606, claim["id"], first, 0.28)
    _study(606, claim["id"], second, 0.34)
    meta.analyze_claim(606, claim["id"])

    with pytest.raises(projects.ProjectError, match="research_review_screening_locked_after_meta_analysis"):
        systematic.screen_source(606, review["id"], first, {
            "stage": "eligibility",
            "decision": "exclude",
            "reason_code": "other_preregistered_reason",
            "note": "Post-analysis outcome-driven reclassification must be blocked.",
        })


def test_evidence_graph_links_review_claim_sources_and_meta_analysis(postgres):
    mission = _mission(607)
    hypothesis = _hypothesis(607, mission["id"])
    claim = _claim(607, mission["id"], hypothesis["id"])
    review = _protocol(607, mission["id"], claim["id"])
    first = _seed_source(607, review, 1)
    second = _seed_source(607, review, 2)
    _include(607, review["id"], first)
    _include(607, review["id"], second)
    _study(607, claim["id"], first, 0.32)
    _study(607, claim["id"], second, 0.37)
    meta_result = meta.analyze_claim(607, claim["id"])

    graph = systematic.build_evidence_graph(607, review["id"])
    node_types = {node["type"] for node in graph["graph"]["nodes"]}
    relations = {edge["relation"] for edge in graph["graph"]["edges"]}
    assert {"systematic_review", "scholarly_source", "claim", "meta_study", "meta_analysis"} <= node_types
    assert {"included", "evaluates_claim", "has_meta_study", "extracts_from_source", "calibrated_by", "contains_study"} <= relations
    assert any(
        node["id"] == "meta_analysis:" + meta_result["id"]
        for node in graph["graph"]["nodes"]
    )
    assert len(graph["graph_hash"]) == 64


def test_report_v7_carries_review_flow_and_graph_hashes(postgres):
    mission = _mission(608)
    hypothesis = _hypothesis(608, mission["id"])
    claim = _claim(608, mission["id"], hypothesis["id"])
    review = _protocol(608, mission["id"], claim["id"])
    source = _seed_source(608, review, 1)
    _include(608, review["id"], source)
    _seed_synthesis(608, mission["id"])

    report = reports.build_report(608, mission["id"])["report"]
    assert report["version"] == 7
    assert report["systematic_review"]["review"]["id"] == review["id"]
    assert report["provenance"]["immutable_systematic_protocol_hash"] == review["protocol_hash"]
    assert report["provenance"]["immutable_systematic_flow_hash"] == report["systematic_review"]["flow"]["flow_hash"]
    assert report["provenance"]["immutable_evidence_graph_hash"] == report["systematic_review"]["evidence_graph"]["graph_hash"]
    assert report["safety"]["systematic_review_protocol_preregistered"] is True
    assert report["safety"]["systematic_review_arbitrary_full_text_fetch"] is False


def test_systematic_review_owner_isolation(postgres):
    mission = _mission(609)
    review = _protocol(609, mission["id"])
    with pytest.raises(projects.ProjectError, match="research_review_protocol_not_found"):
        systematic.get_protocol(610, review["id"])
