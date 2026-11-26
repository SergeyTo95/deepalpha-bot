import os

import pytest

from services import velia_pr_review_engine as engine
from services import velia_pr_review_service as service

HEAD = "a" * 40
BASE = "b" * 40
PROJECT = {"id": "example", "repository_id": 7, "repository_full_name": "owner/example",
           "installation_id": 3, "archived": False}
REVIEW = {"id": "review", "user_id": 42, "project_id": "example", "pull_number": 3,
          "head_sha": HEAD, "base_sha": BASE, "model": "quantum"}
PATCH = "@@ -8,2 +8,2 @@\n def total(values):\n-    return sum(values)\n+    return sum(values) + 1"


def test_review_context_bounds_secrets_and_real_added_lines():
    visible, omitted, anchors = engine.review_context([
        {"filename": "totals.py", "patch": PATCH, "additions": 1},
        {"filename": ".env", "patch": "secret", "additions": 1},
        {"filename": "incomplete.py", "patch": PATCH, "additions": 2},
    ], 1000)
    assert anchors == {"totals.py": {9}}
    assert "+9" in visible[0]["diff"]
    assert [item["reason"] for item in omitted] == ["sensitive_file", "incomplete_diff"]


def test_review_uses_selected_model_and_immutable_diff(monkeypatch):
    requests = []
    monkeypatch.setattr(engine.github, "_installation_token", lambda *_: "synthetic-token")
    monkeypatch.setattr(engine, "available_models", lambda: ["quantum"])
    monkeypatch.setattr(engine, "review_rules", lambda filenames: "rules")
    monkeypatch.setattr(engine.flash, "generate", lambda *_, **__: pytest.fail("Flash used unexpectedly"))
    monkeypatch.setattr(engine.quantum, "generate", lambda prompt, **_: {
        "ok": True, "usage": {"total_tokens": 120},
        "text": '{"findings":[{"path":"totals.py","line":9,"body":"Лишняя единица."},'
                '{"path":"totals.py","line":8,"body":"Модель выбрала строку контекста."}]}',
    })

    def github(method, path, **kwargs):
        requests.append((method, path))
        if path.endswith("/pulls/3"):
            return {"state": "open", "number": 3, "changed_files": 1, "head": {"sha": HEAD},
                    "base": {"sha": BASE, "repo": {"id": 7}}}
        return {"files": [{"filename": "totals.py", "patch": PATCH, "additions": 1}]}

    monkeypatch.setattr(engine.github, "_request", github)
    report = engine.run_review(PROJECT, REVIEW)
    assert report["reviewed_files"] == 1
    assert report["tokens"] == 120
    assert report["invalid_findings"] == 1
    assert report["findings"][0]["line"] == 9
    assert requests[1] == ("GET", f"/repos/owner/example/compare/{BASE}...{HEAD}")
    assert all(method == "GET" for method, _ in requests)


def test_publish_writes_comment_only_and_rejects_stale_revision(monkeypatch):
    monkeypatch.setattr(engine.github, "_installation_token", lambda *_: "synthetic-token")
    review = {**REVIEW, "report": {"reviewed_files": 1, "skipped": [],
                                  "findings": [{"path":"totals.py", "line":9, "body":"@all [текст]"}]}}
    published = []
    head = HEAD

    def github(method, path, **kwargs):
        if method == "POST":
            published.append(kwargs["body"])
            return {"id": 100}
        return {"state":"open","number":3,"head":{"sha":head},
                "base":{"sha":BASE,"repo":{"id":7}}}

    monkeypatch.setattr(engine.github, "_request", github)
    assert engine.publish_review(PROJECT, review) == 100
    assert published[0]["commit_id"] == HEAD and published[0]["event"] == "COMMENT"
    assert published[0]["comments"][0]["line"] == 9 and "@all" not in published[0]["comments"][0]["body"]
    head = "c" * 40
    with pytest.raises(engine.ReviewError, match="pr_review_stale"):
        engine.publish_review(PROJECT, review)
    assert len(published) == 1


def test_postgres_queue_and_publication_are_scoped_and_idempotent(monkeypatch):
    dsn = os.getenv("VELIA_PR_REVIEW_TEST_DSN")
    if not dsn:
        pytest.skip("requires isolated PostgreSQL")
    import psycopg2

    monkeypatch.setattr(service, "get_connection", lambda: psycopg2.connect(dsn))
    monkeypatch.setattr(service, "allowed", lambda user_id: user_id in {42, 84})
    monkeypatch.setattr(service, "enabled", lambda: True)
    monkeypatch.setattr(service, "ready", lambda: True)
    monkeypatch.setattr(service, "project_for", lambda *_: PROJECT)
    monkeypatch.setattr(engine, "available_models", lambda: ["quantum"])
    report = {"findings":[{"path":"totals.py","body":"Лишняя единица.","line":9}],
              "reviewed_files":1,"skipped":[],"tokens":120}
    monkeypatch.setattr(engine, "run_review", lambda *_: report)
    with service._cursor() as cursor:
        cursor.execute("CREATE TABLE velia_developer_projects(project_id TEXT PRIMARY KEY)")
        cursor.execute("INSERT INTO velia_developer_projects VALUES ('example')")
    service.ensure_schema()
    payload = {key:REVIEW[key] for key in ("pull_number", "head_sha", "base_sha", "model")}
    queued = service.enqueue(42, PROJECT, payload)
    assert queued["status"] == "queued"
    assert service.enqueue(42, PROJECT, payload)["id"] == queued["id"]
    with pytest.raises(engine.ReviewError, match="pr_review_not_found"):
        service.fetch_review(84, "example", queued["id"])
    assert service.process_once() is True
    assert service.process_once() is False
    assert service.fetch_review(42, "example", queued["id"])["status"] == "completed"
    writes = []
    monkeypatch.setattr(engine, "publish_review", lambda *_: writes.append("review") or 99)
    assert service.publish(42, PROJECT, queued["id"])["review_id"] == 99
    assert service.publish(42, PROJECT, queued["id"])["review_id"] == 99
    assert writes == ["review"]
