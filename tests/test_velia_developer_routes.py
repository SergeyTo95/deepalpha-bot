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


def test_browser_return_is_configured_and_signed_without_changing_mobile_return(monkeypatch):
    from services import velia_developer_project_service as projects

    monkeypatch.setenv("VELIA_DEVELOPER_STATE_SECRET", "x" * 48)
    monkeypatch.setenv("VELIA_DEVELOPER_WEB_CALLBACK_REDIRECT", "https://velia.example/?connection=github")
    state = projects.create_install_state(7, web_return=True)
    payload = projects.verify_install_state(state)
    assert payload["web_return"] is True
    assert routes._safe_redirect_url(payload["web_return"]) == "https://velia.example/?connection=github"
    assert routes._safe_redirect_url().startswith("velia://")
    assert "web_return" not in projects.verify_install_state(projects.create_install_state(7))
    monkeypatch.setenv("VELIA_DEVELOPER_WEB_CALLBACK_REDIRECT", "javascript:alert(1)")
    assert routes._web_redirect_url() == ""


def test_browser_install_and_callback_use_the_existing_github_authorization(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from aiohttp import ClientSession, web
    from aiohttp.test_utils import TestServer
    from services import velia_developer_github_service as github
    from services import velia_developer_project_service as projects

    async def scenario():
        monkeypatch.setenv("VELIA_DEVELOPER_STATE_SECRET", "s" * 48)
        monkeypatch.setenv("VELIA_GITHUB_APP_CLIENT_ID", "public-client")
        monkeypatch.setenv("VELIA_DEVELOPER_WEB_CALLBACK_REDIRECT", "https://velia.example/?connection=github")
        monkeypatch.setattr(projects, "developer_enabled", lambda: True)
        monkeypatch.setattr(projects, "ensure_developer_tables", lambda: None)
        monkeypatch.setattr(github, "github_app_configured", lambda: True)
        monkeypatch.setattr(github, "validate_github_app_configuration", lambda: {})
        monkeypatch.setattr(github, "authorize_user_installations", lambda code, installation: [{"installation_id": 123}])
        recorded = []
        monkeypatch.setattr(projects, "record_installation", lambda user, details: recorded.append((user, details)))
        module = SimpleNamespace(_mobile_api_available=lambda: True, _require_mobile_auth=lambda request: {"user_id": 7},
            _json_response=lambda data, status=200: web.json_response(data, status=status))
        app = web.Application()
        routes.setup_velia_developer_routes(app, module)
        async with TestServer(app) as server, ClientSession() as client:
            response = await client.post(server.make_url("/mobile-api/v1/developer/github/web-install-url"), json={})
            assert response.status == 200
            query = parse_qs(urlparse((await response.json())["url"]).query)
            assert projects.verify_install_state(query["state"][0])["web_return"] is True
            response = await client.get(server.make_url("/mobile-api/v1/developer/github/callback"),
                params={"state": query["state"][0], "code": "fixture-code"}, allow_redirects=False)
            assert response.status == 302
            assert response.headers["Location"].startswith("https://velia.example/?connection=github&connected=true")
            assert recorded == [(7, {"installation_id": 123})]
    asyncio.run(scenario())
