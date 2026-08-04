import time

from services import velia_developer_github_service as github
from services import velia_developer_routes as routes


def test_app_validation_checks_id_and_slug(monkeypatch):
    monkeypatch.setattr(github, "github_app_configured", lambda: True)
    monkeypatch.setattr(github, "github_app_id", lambda: "4484249")
    monkeypatch.setattr(github, "github_app_slug", lambda: "velia-developer-beta")
    monkeypatch.setattr(github, "_app_jwt", lambda: "app-jwt")
    monkeypatch.setattr(
        github,
        "_request",
        lambda method, path, **kwargs: {"id": 4484249, "slug": "velia-developer-beta"},
    )

    assert github.validate_github_app_configuration() == {
        "id": "4484249",
        "slug": "velia-developer-beta",
    }


def test_app_validation_rejects_wrong_app_id(monkeypatch):
    monkeypatch.setattr(github, "github_app_configured", lambda: True)
    monkeypatch.setattr(github, "github_app_id", lambda: "4484249")
    monkeypatch.setattr(github, "github_app_slug", lambda: "velia-developer-beta")
    monkeypatch.setattr(github, "_app_jwt", lambda: "app-jwt")
    monkeypatch.setattr(github, "_request", lambda *args, **kwargs: {"id": 7, "slug": "velia-developer-beta"})

    try:
        github.validate_github_app_configuration()
        assert False
    except github.DeveloperGithubError as exc:
        assert exc.code == "github_app_id_mismatch"
        assert exc.status == 503


def test_app_validation_rejects_wrong_slug(monkeypatch):
    monkeypatch.setattr(github, "github_app_configured", lambda: True)
    monkeypatch.setattr(github, "github_app_id", lambda: "4484249")
    monkeypatch.setattr(github, "github_app_slug", lambda: "velia-developer-beta")
    monkeypatch.setattr(github, "_app_jwt", lambda: "app-jwt")
    monkeypatch.setattr(
        github,
        "_request",
        lambda *args, **kwargs: {"id": 4484249, "slug": "different-app"},
    )

    try:
        github.validate_github_app_configuration()
        assert False
    except github.DeveloperGithubError as exc:
        assert exc.code == "github_app_slug_mismatch"
        assert exc.status == 503


def test_recent_callback_error_is_user_scoped_and_expires(monkeypatch):
    routes._CONNECTION_ERRORS.clear()
    monkeypatch.setenv("VELIA_DEVELOPER_CONNECTION_ERROR_TTL_SECONDS", "60")
    routes._remember_connection_error(101, "github_private_key_invalid")
    routes._remember_connection_error(202, "github_oauth_failed")

    assert routes._recent_connection_error(101) == "github_private_key_invalid"
    assert routes._recent_connection_error(202) == "github_oauth_failed"
    assert routes._recent_connection_error(303) == ""

    routes._CONNECTION_ERRORS[101] = ("github_private_key_invalid", time.time() - 120)
    assert routes._recent_connection_error(101) == ""


def test_success_clears_callback_error():
    routes._CONNECTION_ERRORS.clear()
    routes._remember_connection_error(101, "github_forbidden")
    routes._clear_connection_error(101)
    assert routes._recent_connection_error(101) == ""


def test_routes_run_preflight_and_surface_recent_error():
    source = open("services/velia_developer_routes.py", encoding="utf-8").read()
    install_url = source.split("async def install_url", 1)[1].split("async def github_callback", 1)[0]
    installations = source.split("async def installations", 1)[1].split("async def repositories", 1)[0]
    callback = source.split("async def github_callback", 1)[1].split("async def installations", 1)[0]

    assert "validate_github_app_configuration" in install_url
    assert "ensure_developer_tables" in install_url
    assert "_recent_connection_error" in installations
    assert "_remember_connection_error" in callback
    assert "_clear_connection_error" in callback
