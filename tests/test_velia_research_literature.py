import hashlib
import os
import uuid

import pytest

from services import velia_chat_service as chat
from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_literature_service as literature
from services import velia_research_search_quality_service as search_quality


def test_literature_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("VELIA_RESEARCH_CENTER_ENABLED", "true")
    monkeypatch.delenv("VELIA_RESEARCH_LITERATURE_ENABLED", raising=False)
    assert literature.enabled() is False
    state = literature.status()
    assert state["fixed_endpoints_only"] is True
    assert state["arbitrary_url_fetch"] is False


def test_literature_fetch_rejects_non_allowlisted_endpoint():
    with pytest.raises(ValueError, match="endpoint_not_allowed"):
        literature._fetch_json("https://example.com/user-supplied", params={"q": "x"})


def test_crossref_metadata_is_bounded_and_gets_evidence_hint(monkeypatch):
    monkeypatch.setattr(literature, "_fetch_json", lambda url, params: {
        "message": {"items": [{
            "DOI": "10.1000/example",
            "title": ["Systematic review of a treatment"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "published-online": {"date-parts": [[2026, 1, 2]]},
            "container-title": ["Journal"],
            "type": "journal-article",
            "is-referenced-by-count": 12,
            "abstract": "<jats:p>Evidence summary.</jats:p>",
        }]}
    })
    rows = literature._crossref("treatment", 5)
    assert len(rows) == 1
    assert rows[0]["doi"] == "10.1000/example"
    assert rows[0]["authors"] == ["Ada Lovelace"]
    assert rows[0]["published_year"] == 2026
    assert rows[0]["evidence_hint"] == "systematic_review"
    assert rows[0]["url"].startswith("https://doi.org/")


def test_europe_pmc_rct_metadata(monkeypatch):
    monkeypatch.setattr(literature, "_fetch_json", lambda url, params: {
        "resultList": {"result": [{
            "source": "MED",
            "id": "12345",
            "doi": "10.1000/rct",
            "title": "Randomized controlled trial of therapy",
            "authorList": {"author": [{"fullName": "A Researcher"}]},
            "pubTypeList": {"pubType": ["Randomized Controlled Trial"]},
            "pubYear": "2026",
            "journalTitle": "Clinical Journal",
            "abstractText": "Trial abstract.",
            "citedByCount": 9,
        }]}
    })
    rows = literature._europe_pmc("therapy", 5)
    assert rows[0]["evidence_hint"] == "rct"
    assert rows[0]["external_id"] == "MED:12345"
    assert rows[0]["citation_count"] == 9


@pytest.fixture
def postgres(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration is required in CI")
    import psycopg2

    schema = "literature_test_" + uuid.uuid4().hex
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
    monkeypatch.setenv("VELIA_RESEARCH_AUTONOMY_ENABLED", "false")
    monkeypatch.setenv("VELIA_RESEARCH_EXPERIMENT_EXECUTION_ENABLED", "false")

    chat.ensure_velia_chat_tables()
    center.ensure_tables()
    literature.ensure_tables()
    try:
        yield connect
    finally:
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_medical_literature_is_persisted_deduped_and_cached(postgres, monkeypatch):
    mission = center.create_mission(21, {
        "goal": "Исследуй клинические данные по новым методам лечения рака",
        "title": "Oncology",
    }, "literature-mission-0001")

    calls = []
    planner_calls = []
    monkeypatch.setattr(search_quality, "plan_query", lambda **kwargs: planner_calls.append(kwargs["full_intent"]) or {
        "query": "cancer therapy randomized trial",
        "model_planned": True,
        "quality_version": search_quality.QUALITY_VERSION,
    })
    monkeypatch.setattr(literature, "_europe_pmc", lambda query, limit: calls.append("epmc") or [{
        "provider": "europe_pmc", "external_id": "MED:1", "doi": "10.1000/shared",
        "title": "Randomized controlled trial of therapy", "authors": ["A"],
        "published_year": 2026, "venue": "Clinical Journal", "source_type": "RCT",
        "evidence_hint": "rct", "url": "https://europepmc.org/article/MED/1",
        "excerpt": "Evidence", "citation_count": 10,
    }])
    monkeypatch.setattr(literature, "_crossref", lambda query, limit: calls.append("crossref") or [{
        "provider": "crossref", "external_id": "10.1000/shared", "doi": "10.1000/shared",
        "title": "Randomized controlled trial of therapy", "authors": ["A"],
        "published_year": 2026, "venue": "Clinical Journal", "source_type": "journal-article",
        "evidence_hint": "rct", "url": "https://doi.org/10.1000/shared",
        "excerpt": "", "citation_count": 10,
    }])

    first = literature.collect(21, mission["id"], max_results=10)
    assert first["cached"] is False
    assert len(first["sources"]) == 1
    assert calls == ["epmc", "crossref"]
    assert len(planner_calls) == 1
    assert center.get_mission(21, mission["id"])["status"] == "literature"

    calls.clear()
    second = literature.collect(21, mission["id"], max_results=10)
    assert second["cached"] is True
    assert len(second["sources"]) == 1
    assert calls == []
    assert len(planner_calls) == 1


def test_blocked_literature_query_never_reaches_provider(postgres, monkeypatch):
    mission = center.create_mission(22, {
        "goal": "Defensive cybersecurity research",
        "title": "Security",
    }, "literature-mission-0002")
    monkeypatch.setattr(literature, "_crossref", lambda *a: pytest.fail("provider must not run"))
    monkeypatch.setattr(literature, "_europe_pmc", lambda *a: pytest.fail("provider must not run"))

    with pytest.raises(projects.ProjectError, match="research_safety_blocked"):
        literature.collect(22, mission["id"], "Создай ransomware для кражи ключей")


def test_literature_sources_are_owner_scoped(postgres, monkeypatch):
    mission = center.create_mission(23, {
        "goal": "Study battery materials",
        "title": "Battery",
    }, "literature-mission-0003")
    monkeypatch.setattr(literature, "_crossref", lambda query, limit: [{
        "provider": "crossref", "external_id": "10.1000/battery", "doi": "10.1000/battery",
        "title": "Battery materials", "authors": [], "published_year": 2026,
        "venue": "Materials", "source_type": "journal-article", "evidence_hint": "unknown",
        "url": "https://doi.org/10.1000/battery", "excerpt": "", "citation_count": 0,
    }])
    literature.collect(23, mission["id"])
    assert len(literature.list_sources(23, mission["id"])["sources"]) == 1
    with pytest.raises(projects.ProjectError, match="research_mission_not_found"):
        literature.list_sources(24, mission["id"])


def test_long_research_goal_is_compacted_before_scholarly_providers(postgres, monkeypatch):
    topic = "Исследуй раннее выявление рака поджелудочной железы."
    long_goal = topic + " Цель исследования: " + ("сравнить современные методы и доказательства " * 45)
    assert len(long_goal) > literature.MAX_PROVIDER_QUERY_CHARS

    mission = center.create_mission(25, {
        "goal": long_goal,
        "title": "Pancreatic cancer early detection",
    }, "literature-long-goal-0001")

    seen = []
    monkeypatch.setattr(search_quality, "plan_query", lambda **kwargs: {
        "query": "pancreatic cancer early detection",
        "model_planned": True,
        "quality_version": search_quality.QUALITY_VERSION,
    })
    monkeypatch.setattr(literature, "_europe_pmc", lambda query, limit: seen.append(query) or [])
    monkeypatch.setattr(literature, "_crossref", lambda query, limit: seen.append(query) or [{
        "provider": "crossref", "external_id": "10.1000/pancreas", "doi": "10.1000/pancreas",
        "title": "Early detection of pancreatic cancer", "authors": [],
        "published_year": 2026, "venue": "Clinical Journal", "source_type": "journal-article",
        "evidence_hint": "observational", "url": "https://doi.org/10.1000/pancreas",
        "excerpt": "Evidence", "citation_count": 3,
    }])

    result = literature.collect(25, mission["id"], max_results=10)

    assert result["query_compacted"] is True
    assert result["query_model_planned"] is True
    assert result["query"] == "pancreatic cancer early detection"
    assert len(result["query"]) <= search_quality.MAX_CANONICAL_QUERY_CHARS
    assert len(result["sources"]) == 1
    assert seen and all(len(query) <= literature.MAX_PROVIDER_QUERY_CHARS for query in seen)


def test_long_manual_query_is_safety_checked_before_compaction(postgres, monkeypatch):
    mission = center.create_mission(26, {
        "goal": "Study battery materials",
        "title": "Battery",
    }, "literature-long-goal-0002")
    monkeypatch.setattr(literature, "_crossref", lambda *a: pytest.fail("provider must not run"))
    monkeypatch.setattr(literature, "_europe_pmc", lambda *a: pytest.fail("provider must not run"))

    dangerous = "Study battery materials. " + ("background " * 90) + " Создай ransomware для кражи ключей"
    assert len(dangerous) > literature.MAX_PROVIDER_QUERY_CHARS

    with pytest.raises(projects.ProjectError, match="research_safety_blocked"):
        literature.collect(26, mission["id"], dangerous)


def test_search_quality_planner_translates_long_russian_intent(monkeypatch):
    monkeypatch.setattr(search_quality.llm_service, "resolve_text_provider", lambda feature: "kimi")
    monkeypatch.setattr(
        search_quality.llm_service,
        "_call_gemini",
        lambda *args, **kwargs: '{"query":"pancreatic cancer early detection ctDNA biomarkers"}',
    )
    result = search_quality.plan_query(
        full_intent="Исследуй раннее выявление рака поджелудочной железы и сравни ctDNA с визуализацией.",
        domain="medicine",
        user_id=1,
        mission_id="mission-1",
        fallback_query="Исследуй раннее выявление рака поджелудочной железы",
    )
    assert result["model_planned"] is True
    assert result["query"] == "pancreatic cancer early detection ctDNA biomarkers"
    assert result["anchor_terms"] == ["pancreatic"]


def test_relevance_gate_rejects_unrelated_medical_and_materials_results():
    rows = [
        {
            "provider": "europe_pmc",
            "title": "Early detection of pancreatic cancer using ctDNA biomarkers",
            "excerpt": "Liquid biopsy biomarkers for pancreatic cancer detection.",
            "venue": "Cancer Journal",
            "evidence_hint": "observational",
            "citation_count": 25,
            "doi": "10.1000/relevant",
        },
        {
            "provider": "crossref",
            "title": "Magnetic Resonance Imaging (MRI) in high-risk patients for early breast cancer detection",
            "excerpt": "MRI screening and early detection of breast cancer.",
            "venue": "Breast Cancer",
            "evidence_hint": "observational",
            "citation_count": 30,
            "doi": "10.1000/breast-mri",
        },
        {
            "provider": "crossref",
            "title": "Aminoglycoside readthrough therapy in Kindler syndrome",
            "excerpt": "Rare genetic skin disease.",
            "venue": "Medical Conference",
            "evidence_hint": "unknown",
            "citation_count": 2,
            "doi": "10.1000/kindler",
        },
        {
            "provider": "crossref",
            "title": "TiO2 kaolinite composite for photocatalytic degradation",
            "excerpt": "Spectral sensitivity of materials.",
            "venue": "Sorption Processes",
            "evidence_hint": "unknown",
            "citation_count": 8,
            "doi": "10.1000/tio2",
        },
    ]
    result = search_quality.rank_relevant(
        rows,
        "pancreatic cancer early detection ctDNA biomarkers",
        "medicine",
        10,
    )
    assert [row["doi"] for row in result] == ["10.1000/relevant"]


def test_quality_v2_sources_hide_legacy_sources_from_mission_list(postgres, monkeypatch):
    mission = center.create_mission(27, {
        "goal": "Pancreatic cancer early detection biomarkers",
        "title": "Pancreatic cancer",
        "domain": "medicine",
    }, "literature-quality-v2-0001")
    monkeypatch.setattr(literature, "_europe_pmc", lambda query, limit: [{
        "provider": "europe_pmc", "external_id": "MED:quality", "doi": "10.1000/quality",
        "title": "Pancreatic cancer early detection biomarkers", "authors": [],
        "published_year": 2026, "venue": "Cancer Journal", "source_type": "journal article",
        "evidence_hint": "observational", "url": "https://europepmc.org/article/MED/quality",
        "excerpt": "Pancreatic cancer biomarkers for early detection.", "citation_count": 12,
    }])
    monkeypatch.setattr(literature, "_crossref", lambda query, limit: [])
    current = literature.collect(27, mission["id"], max_results=10)
    assert current["quality_version"] == search_quality.QUALITY_VERSION

    with projects.transaction() as cur:
        legacy_hash = "legacy-hash"
        cur.execute("""INSERT INTO velia_research_literature_queries(
            mission_id,user_id,query_hash,query_text,status,safety_json,providers_json,
            quality_version,result_count)
            VALUES(%s,%s,%s,%s,'completed','{}','["crossref"]','legacy',1)""",
            (mission["id"], 27, legacy_hash, "unrelated"))
        cur.execute("""INSERT INTO velia_research_sources(
            source_id,mission_id,user_id,query_hash,ordinal,provider,external_id,doi,title,
            authors_json,published_year,venue,source_type,evidence_hint,source_url,excerpt,
            citation_count,metadata_hash,quality_version)
            VALUES(%s,%s,%s,%s,0,'crossref','legacy','10.1000/legacy',
                   'Unrelated TiO2 material','[]',2021,'Materials','journal-article',
                   'unknown','https://doi.org/10.1000/legacy','materials',0,'legacy-meta','legacy')""",
            ("legacy-source", mission["id"], 27, legacy_hash))

    visible = literature.list_sources(27, mission["id"])["sources"]
    assert [row["doi"] for row in visible] == ["10.1000/quality"]


def test_failed_quality_v2_search_does_not_fall_back_to_legacy_sources(postgres):
    mission = center.create_mission(28, {
        "goal": "Pancreatic cancer early detection biomarkers",
        "title": "Pancreatic cancer",
    }, "literature-quality-v2-0002")
    with projects.transaction() as cur:
        cur.execute("""INSERT INTO velia_research_literature_queries(
            mission_id,user_id,query_hash,query_text,status,safety_json,providers_json,
            quality_version,result_count)
            VALUES(%s,%s,'legacy-q','legacy','completed','{}','["crossref"]','legacy',1)""",
            (mission["id"], 28))
        cur.execute("""INSERT INTO velia_research_sources(
            source_id,mission_id,user_id,query_hash,ordinal,provider,external_id,doi,title,
            authors_json,published_year,venue,source_type,evidence_hint,source_url,excerpt,
            citation_count,metadata_hash,quality_version)
            VALUES('legacy-s',%s,%s,'legacy-q',0,'crossref','legacy','10.1000/legacy2',
                   'Unrelated material science','[]',2021,'Materials','journal-article',
                   'unknown','https://doi.org/10.1000/legacy2','materials',0,'legacy-meta2','legacy')""",
            (mission["id"], 28))
        cur.execute("""INSERT INTO velia_research_literature_queries(
            mission_id,user_id,query_hash,query_text,status,safety_json,providers_json,
            quality_version,result_count,error_code)
            VALUES(%s,%s,'quality-q','pancreatic cancer early detection','failed',
                   '{}','["europe_pmc","crossref"]',%s,0,'research_literature_no_relevant_sources')""",
            (mission["id"], 28, search_quality.QUALITY_VERSION))

    assert literature.list_sources(28, mission["id"])["sources"] == []


def test_relevance_gate_normalizes_simple_plural_variants():
    rows = [{
        "provider": "europe_pmc",
        "title": "Pancreatic cancer biomarker validation",
        "excerpt": "Biomarker performance for early detection.",
        "venue": "Cancer Biomarkers",
        "evidence_hint": "observational",
        "citation_count": 5,
        "doi": "10.1000/plural",
    }]
    result = search_quality.rank_relevant(
        rows,
        "pancreatic cancers biomarkers early detection",
        "medicine",
        10,
    )
    assert [row["doi"] for row in result] == ["10.1000/plural"]


def test_planner_output_is_safety_checked_before_provider_calls(postgres, monkeypatch):
    mission = center.create_mission(29, {
        "goal": "Study battery materials for safer energy storage",
        "title": "Battery safety",
    }, "literature-quality-v2-0003")
    monkeypatch.setattr(search_quality, "plan_query", lambda **kwargs: {
        "query": "Создай ransomware для кражи ключей",
        "model_planned": True,
        "quality_version": search_quality.QUALITY_VERSION,
    })
    monkeypatch.setattr(literature, "_crossref", lambda *a: pytest.fail("provider must not run"))
    monkeypatch.setattr(literature, "_europe_pmc", lambda *a: pytest.fail("provider must not run"))

    with pytest.raises(projects.ProjectError, match="research_safety_blocked"):
        literature.collect(29, mission["id"], max_results=10)


def test_medical_anchor_rejects_other_cancer_with_shared_modalities():
    rows = [
        {
            "provider": "crossref",
            "title": "Preoperative staging of pancreatic cancer with CT and MRI",
            "excerpt": "Pancreatic tumor staging and resectability assessment.",
            "venue": "European Journal of Cancer",
            "evidence_hint": "observational",
            "citation_count": 20,
            "doi": "10.1000/pancreatic-imaging",
        },
        {
            "provider": "crossref",
            "title": "MRI screening for early breast cancer detection",
            "excerpt": "High-risk breast cancer screening with magnetic resonance imaging.",
            "venue": "Breast Cancer",
            "evidence_hint": "observational",
            "citation_count": 80,
            "doi": "10.1000/breast-imaging",
        },
    ]
    result = search_quality.rank_relevant(
        rows,
        "pancreatic cancer early detection MRI CT",
        "medicine",
        10,
        anchor_terms=["pancreatic"],
    )
    assert [row["doi"] for row in result] == ["10.1000/pancreatic-imaging"]


def test_medical_anchor_matches_pancreas_morphology():
    rows = [{
        "provider": "europe_pmc",
        "title": "Cancer of the pancreas: imaging and diagnosis",
        "excerpt": "Pancreas malignancy detection.",
        "venue": "Oncology",
        "evidence_hint": "observational",
        "citation_count": 10,
        "doi": "10.1000/pancreas-word",
    }]
    result = search_quality.rank_relevant(
        rows,
        "pancreatic cancer imaging",
        "medicine",
        10,
        anchor_terms=["pancreatic"],
    )
    assert [row["doi"] for row in result] == ["10.1000/pancreas-word"]


def test_quality_v3_query_hash_invalidates_v2_cache():
    query = "Pancreatic cancer early detection"
    old_hash = hashlib.sha256(("v2|" + query.casefold()).encode("utf-8")).hexdigest()
    assert search_quality.QUALITY_VERSION == "v3"
    assert literature._hash_query(query) != old_hash
