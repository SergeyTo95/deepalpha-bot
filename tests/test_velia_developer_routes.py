from urllib.parse import parse_qs, urlparse

from services import velia_developer_routes as routes


def test_github_connect_starts_with_oauth_for_existing_installation(monkeypatch):
    monkeypatch.setenv("VELIA_GITHUB_APP_CLIENT_ID", "Iv1.test-client")
    url = routes._github_oauth_url("signed-state")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "github.com"
    assert parsed.path == "/login/oauth/authorize"
    assert query == {
        "client_id": ["Iv1.test-client"],
        "state": ["signed-state"],
        "prompt": ["select_account"],
    }


def test_github_install_fallback_preserves_signed_state(monkeypatch):
    monkeypatch.setenv("VELIA_GITHUB_APP_SLUG", "velia-developer-beta")
    url = routes._github_install_url("signed-state")
    parsed = urlparse(url)

    assert parsed.scheme == "https"
    assert parsed.netloc == "github.com"
    assert parsed.path == "/apps/velia-developer-beta/installations/new"
    assert parse_qs(parsed.query) == {"state": ["signed-state"]}


def test_github_connect_urls_never_expose_server_secrets(monkeypatch):
    monkeypatch.setenv("VELIA_GITHUB_APP_CLIENT_ID", "Iv1.public-client")
    monkeypatch.setenv("VELIA_GITHUB_APP_CLIENT_SECRET", "do-not-leak-client-secret")
    monkeypatch.setenv("VELIA_GITHUB_APP_PRIVATE_KEY", "do-not-leak-private-key")
    monkeypatch.setenv("VELIA_GITHUB_APP_SLUG", "velia-developer-beta")

    urls = [
        routes._github_oauth_url("signed-state"),
        routes._github_install_url("signed-state"),
    ]

    for url in urls:
        assert "do-not-leak-client-secret" not in url
        assert "do-not-leak-private-key" not in url


def test_web_github_installation_has_a_signed_web_return(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_STATE_SECRET", "g" * 32)
    state = routes.project_service.create_install_state(42, web=True)
    assert routes.project_service.verify_install_state(state)["web"] is True
    native = routes.project_service.create_install_state(42)
    assert "web" not in routes.project_service.verify_install_state(native)
    assert routes._safe_redirect_url(web_client=True) == "https://velyoncore.ai/?connection=github"
