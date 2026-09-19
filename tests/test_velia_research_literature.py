import os
import uuid

import pytest

from services import velia_chat_service as chat
from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_literature_service as literature


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
    assert center.get_mission(21, mission["id"])["status"] == "literature"

    calls.clear()
    second = literature.collect(21, mission["id"], max_results=10)
    assert second["cached"] is True
    assert len(second["sources"]) == 1
    assert calls == []


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
    assert result["query"].startswith("Исследуй раннее выявление рака поджелудочной железы")
    assert len(result["query"]) <= literature.MAX_PROVIDER_QUERY_CHARS
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
