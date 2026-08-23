import pytest

from services import velia_software_factory_deployment_status_github_service as service


def test_commit_status_snapshot_reads_exact_sha_with_get(monkeypatch):
    exact = "a" * 40
    calls = []
    monkeypatch.setattr(service, "_access", lambda project: ("Acme", "repo", "token"))

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "state": "success",
            "total_count": 3,
            "statuses": [
                {
                    "context": "melodious-radiance - deepalpha-bot",
                    "state": "success",
                    "description": "deployed",
                    "target_url": "https://railway.com/project/p/service/s?id=d",
                    "updated_at": "2026-08-23T16:00:00Z",
                    "creator": {"login": "railway-app"},
                },
                {
                    "context": "melodious-radiance - deepalpha-bot",
                    "state": "pending",
                    "target_url": "https://railway.com/project/p/service/s?id=old",
                    "updated_at": "2026-08-23T15:59:00Z",
                },
                {
                    "context": "ci/unit",
                    "state": "success",
                    "target_url": "https://github.com/Acme/repo/actions/runs/1",
                },
            ],
        }

    monkeypatch.setattr(service, "_request", request)
    result = service.commit_status_snapshot({}, exact)
    assert calls == [
        (
            "GET",
            f"/repos/Acme/repo/commits/{exact}/status",
            {"token": "token", "params": {"per_page": 100}},
        )
    ]
    assert result["commit_sha"] == exact
    assert result["contexts"]["melodious-radiance - deepalpha-bot"] == "success"
    assert len([x for x in result["statuses"] if x["context"] == "melodious-radiance - deepalpha-bot"]) == 1


def test_invalid_commit_sha_is_rejected_before_request(monkeypatch):
    monkeypatch.setattr(service, "_access", lambda project: (_ for _ in ()).throw(AssertionError("no access")))
    with pytest.raises(service.DeploymentStatusGithubError) as exc:
        service.commit_status_snapshot({}, "not-a-sha")
    assert exc.value.code == "velia_factory_deployment_status_sha_invalid"


def test_railway_candidates_require_railway_target_host():
    snapshot = {
        "statuses": [
            {
                "context": "railway-api",
                "state": "success",
                "target_url": "https://railway.com/project/p/service/s",
            },
            {
                "context": "ci/unit",
                "state": "success",
                "target_url": "https://github.com/Acme/repo/actions/runs/1",
            },
            {
                "context": "fake-railway",
                "state": "success",
                "target_url": "https://railway.com.evil.example/path",
            },
        ]
    }
    result = service.railway_context_candidates(snapshot)
    assert [item["context"] for item in result] == ["railway-api"]
