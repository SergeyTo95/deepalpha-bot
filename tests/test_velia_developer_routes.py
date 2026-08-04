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
