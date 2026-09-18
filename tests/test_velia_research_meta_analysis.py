import json
import math
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


def test_meta_analysis_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.setenv("VELIA_RESEARCH_CLAIM_LEDGER_ENABLED", "true")
    monkeypatch.delenv("VELIA_RESEARCH_META_ANALYSIS_ENABLED", raising=False)
    state = meta.status()
    assert state["enabled"] is False
    assert state["fixed_effect"] == "inverse_variance"
    assert state["random_effects"] == "dersimonian_laird"
    assert state["claim_promotion_allowed"] is False
    assert state["publication_bias_diagnostic_is_proof"] is False
    assert state["arbitrary_code"] is False
    assert state["arbitrary_shell"] is False
    assert state["arbitrary_file_access"] is False
    assert state["arbitrary_url_fetch"] is False


def test_effect_normalization_is_deterministic():
    corr = meta._normalize("correlation", {"r": 0.5, "n": 103})
    assert corr["effect"] == pytest.approx(math.atanh(0.5))
    assert corr["variance"] == pytest.approx(0.01)
    assert corr["display_effect"] == pytest.approx(0.5)

    smd = meta._normalize("standardized_mean_difference", {
        "n_treatment": 30,
        "mean_treatment": 12.0,
        "sd_treatment": 2.0,
        "n_control": 30,
        "mean_control": 10.0,
        "sd_control": 2.0,
    })
    assert smd["normalized_metric"] == "hedges_g"
    assert 0.9 < smd["effect"] < 1.0
    assert smd["variance"] > 0.0

    lor = meta._normalize("log_odds_ratio", {
        "event_treatment": 20,
        "non_event_treatment": 80,
        "event_control": 10,
        "non_event_control": 90,
    })
    assert lor["effect"] == pytest.approx(math.log(2.25))
    assert lor["display_effect"] == pytest.approx(2.25)
    assert lor["continuity_correction_0_5"] is False


@pytest.fixture
def postgres(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration is required in CI")
    import psycopg2

    schema = "research_meta_test_" + uuid.uuid4().hex
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
    ):
        monkeypatch.setenv(name, "true")

    chat.ensure_velia_chat_tables()
    projects.ensure_tables()
    center.ensure_tables()
    literature.ensure_tables()
    reasoning.ensure_tables()
    claims.ensure_tables()
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
        {"goal": "Evaluate a source-backed research claim", "title": "Meta study"},
        "meta-mission-" + str(uid),
    )


def _hypothesis(uid, mission_id):
    return center.add_hypothesis(
        uid,
        mission_id,
        "Positive association",
        "Independent publications can calibrate the claim without promoting its stage.",
    )


def _claim(uid, mission_id, hypothesis_id):
    return claims.create_claim(uid, mission_id, {
        "hypothesis_id": hypothesis_id,
        "statement": "The measured association is positive.",
        "expected_direction": "positive",
        "analysis_kind": "correlation",
        "source_ids": [],
    })


def _source(uid, mission_id, index, *, doi=None, provider="crossref"):
    query_hash = "q-" + str(uid)
    source_id = f"source-{uid}-{index}"
    doi = doi if doi is not None else f"10.1234/meta.{uid}.{index}"
    with projects.transaction(uid) as cur:
        cur.execute("""INSERT INTO velia_research_literature_queries(
            mission_id,user_id,query_hash,query_text,status,safety_json,providers_json,result_count)
            VALUES(%s,%s,%s,'meta test','completed','{}','[]',20)
            ON CONFLICT(mission_id,user_id,query_hash) DO NOTHING""",
            (str(mission_id), int(uid), query_hash))
        cur.execute("""INSERT INTO velia_research_sources(
            source_id,mission_id,user_id,query_hash,ordinal,provider,external_id,doi,title,
            authors_json,published_year,venue,source_type,evidence_hint,source_url,excerpt,
            citation_count,metadata_hash)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'[]',2025,'Journal','article','observational',
                   '','','0',%s)""",
            (
                source_id, str(mission_id), int(uid), query_hash, int(index), provider,
                "ext-" + str(uid) + "-" + str(index), doi,
                "Study " + str(index), "hash-" + str(uid) + "-" + str(index),
            ))
    return source_id


def _risk(level="low"):
    return {
        "domains": {
            "selection": level,
            "measurement": level,
            "confounding": level,
            "missing_data": level,
            "reporting": level,
        },
        "note": "Structured reviewer assessment.",
    }


def _study(uid, claim_id, source_id, r, n=160, risk="low"):
    return meta.register_study(uid, claim_id, {
        "source_id": source_id,
        "study_design": "cohort",
        "effect_type": "correlation",
        "statistics": {"r": r, "n": n},
        "risk_of_bias": _risk(risk),
    })


def _seed_synthesis(uid, mission_id, summary):
    with projects.transaction(uid) as cur:
        cur.execute("""INSERT INTO velia_research_syntheses(
            synthesis_id,mission_id,user_id,evidence_hash,status,source_ids_json,
            result_json,safety_json,provider)
            VALUES(%s,%s,%s,%s,'completed','[]',%s,%s,'test')""",
            (
                "meta-synthesis-" + str(uid),
                str(mission_id),
                int(uid),
                "meta-evidence-" + str(uid),
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


def test_supportive_meta_analysis_cannot_promote_claim_stage(postgres):
    mission = _mission(501)
    hypothesis = _hypothesis(501, mission["id"])
    claim = _claim(501, mission["id"], hypothesis["id"])
    for idx, r in enumerate((0.42, 0.47, 0.51), start=1):
        _study(501, claim["id"], _source(501, mission["id"], idx), r)

    result = meta.analyze_claim(501, claim["id"])
    assert result["calibration"]["pooled_signal"] == "supports"
    assert result["calibration"]["claim_action"] == "no_change"
    assert result["calibration"]["claim_promotion_allowed"] is False
    assert result["claim_snapshot"]["stage"] == "exploratory"
    assert "does not promote" in result["claim_snapshot"]["wording"]
    assert result["heterogeneity"]["i_squared_percent"] >= 0.0
    assert result["leave_one_out"]["available"] is True
    assert result["publication_bias"]["available"] is False


def test_robust_opposite_meta_analysis_can_only_downgrade_to_contradicted(postgres):
    mission = _mission(502)
    hypothesis = _hypothesis(502, mission["id"])
    claim = _claim(502, mission["id"], hypothesis["id"])
    for idx, r in enumerate((-0.55, -0.50, -0.58), start=1):
        _study(502, claim["id"], _source(502, mission["id"], idx), r)

    result = meta.analyze_claim(502, claim["id"])
    assert result["calibration"]["pooled_signal"] == "contradicts"
    assert result["calibration"]["claim_action"] == "contradict"
    assert result["leave_one_out"]["sign_stable"] is True
    assert result["claim_snapshot"]["stage"] == "contradicted"
    assert "no affirmative conclusion is permitted" in result["claim_snapshot"]["wording"]


def test_high_heterogeneity_forces_caution_not_hard_contradiction(postgres):
    mission = _mission(503)
    hypothesis = _hypothesis(503, mission["id"])
    claim = _claim(503, mission["id"], hypothesis["id"])
    for idx, r in enumerate((-0.82, -0.75, 0.76, 0.80), start=1):
        _study(503, claim["id"], _source(503, mission["id"], idx), r, n=220)

    result = meta.analyze_claim(503, claim["id"])
    assert result["heterogeneity"]["i_squared_percent"] >= 75.0
    assert result["calibration"]["claim_action"] == "caution"
    assert "high_heterogeneity_i2_gte_75" in result["calibration"]["reasons"]
    assert result["claim_snapshot"]["stage"] == "exploratory"


def test_duplicate_publication_fingerprint_is_rejected(postgres):
    mission = _mission(504)
    hypothesis = _hypothesis(504, mission["id"])
    claim = _claim(504, mission["id"], hypothesis["id"])
    doi = "10.5555/same.paper"
    first = _source(504, mission["id"], 1, doi=doi)
    second = _source(504, mission["id"], 2, doi=doi, provider="europe_pmc")
    _study(504, claim["id"], first, 0.3)
    with pytest.raises(projects.ProjectError, match="research_meta_duplicate_publication"):
        _study(504, claim["id"], second, 0.31)


def test_risk_of_bias_majority_forces_caution(postgres):
    mission = _mission(505)
    hypothesis = _hypothesis(505, mission["id"])
    claim = _claim(505, mission["id"], hypothesis["id"])
    for idx, r in enumerate((0.35, 0.38, 0.40), start=1):
        _study(
            505, claim["id"], _source(505, mission["id"], idx), r,
            risk="high" if idx <= 2 else "low",
        )
    result = meta.analyze_claim(505, claim["id"])
    assert result["risk_of_bias"]["high_risk_majority"] is True
    assert result["calibration"]["claim_action"] == "caution"
    assert "majority_high_risk_of_bias" in result["calibration"]["reasons"]


def test_egger_is_screening_only_when_ten_studies_exist(postgres):
    mission = _mission(506)
    hypothesis = _hypothesis(506, mission["id"])
    claim = _claim(506, mission["id"], hypothesis["id"])
    values = (0.12, 0.15, 0.18, 0.20, 0.22, 0.25, 0.27, 0.30, 0.33, 0.36)
    for idx, r in enumerate(values, start=1):
        _study(506, claim["id"], _source(506, mission["id"], idx), r, n=80 + idx * 20)
    result = meta.analyze_claim(506, claim["id"])
    diagnostic = result["publication_bias"]
    assert diagnostic["available"] is True
    assert diagnostic["proof_of_publication_bias"] is False
    assert diagnostic["method"] == "egger_regression_normal_approximation"
    assert 0.0 <= diagnostic["approx_two_sided_p"] <= 1.0


def test_mixed_effect_types_fail_closed(postgres):
    mission = _mission(507)
    hypothesis = _hypothesis(507, mission["id"])
    claim = _claim(507, mission["id"], hypothesis["id"])
    _study(507, claim["id"], _source(507, mission["id"], 1), 0.4)
    meta.register_study(507, claim["id"], {
        "source_id": _source(507, mission["id"], 2),
        "study_design": "rct",
        "effect_type": "log_odds_ratio",
        "statistics": {
            "event_treatment": 20,
            "non_event_treatment": 80,
            "event_control": 12,
            "non_event_control": 88,
        },
        "risk_of_bias": _risk(),
    })
    with pytest.raises(projects.ProjectError, match="research_meta_mixed_effect_types"):
        meta.analyze_claim(507, claim["id"])


def test_report_v6_carries_meta_provenance_and_bounded_conclusion(postgres):
    mission = _mission(508)
    hypothesis = _hypothesis(508, mission["id"])
    claim = _claim(508, mission["id"], hypothesis["id"])
    for idx, r in enumerate((-0.52, -0.56, -0.50), start=1):
        _study(508, claim["id"], _source(508, mission["id"], idx), r)
    meta_result = meta.analyze_claim(508, claim["id"])
    assert meta_result["claim_snapshot"]["stage"] == "contradicted"
    _seed_synthesis(508, mission["id"], "This conclusively proves the positive association.")

    report = reports.build_report(508, mission["id"])["report"]
    assert report["version"] == 6
    assert "contradicts the claim" in report["conclusion"]["summary"]
    assert "conclusively proves" not in report["conclusion"]["summary"]
    assert "conclusively proves" in report["conclusion"]["literature_synthesis_summary"]
    assert report["meta_analysis"]["snapshot_ids"] == [meta_result["id"]]
    assert report["provenance"]["immutable_meta_snapshot_ids"] == [meta_result["id"]]
    assert report["provenance"]["immutable_meta_evidence_hashes"] == [meta_result["evidence_hash"]]
    assert report["safety"]["meta_analysis_claim_promotion_allowed"] is False


def test_meta_owner_isolation(postgres):
    mission = _mission(509)
    hypothesis = _hypothesis(509, mission["id"])
    claim = _claim(509, mission["id"], hypothesis["id"])
    study = _study(509, claim["id"], _source(509, mission["id"], 1), 0.4)
    with pytest.raises(projects.ProjectError, match="research_meta_study_not_found"):
        meta.get_study(510, study["id"])
