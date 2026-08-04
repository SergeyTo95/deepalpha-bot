from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"Anchor not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, block: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if marker in text:
        return
    target.write_text(text.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8")


def main() -> None:
    github_path = "services/velia_developer_github_service.py"
    replace_once(
        github_path,
        """def github_app_slug() -> str:
    return str(os.getenv("VELIA_GITHUB_APP_SLUG", "") or "").strip()


def github_app_configured() -> bool:
    return bool(github_app_id() and github_app_slug() and os.getenv("VELIA_GITHUB_APP_PRIVATE_KEY"))
""",
        """def github_app_slug() -> str:
    return str(os.getenv("VELIA_GITHUB_APP_SLUG", "") or "").strip()


def github_client_id() -> str:
    return str(os.getenv("VELIA_GITHUB_APP_CLIENT_ID", "") or "").strip()


def _github_client_secret() -> str:
    value = str(os.getenv("VELIA_GITHUB_APP_CLIENT_SECRET", "") or "").strip()
    if not value:
        raise DeveloperGithubError("github_oauth_not_configured", status=503)
    return value


def github_app_configured() -> bool:
    return bool(
        github_app_id()
        and github_app_slug()
        and github_client_id()
        and os.getenv("VELIA_GITHUB_APP_CLIENT_SECRET")
        and os.getenv("VELIA_GITHUB_APP_PRIVATE_KEY")
    )
""",
    )
    replace_once(
        github_path,
        """def list_installation_repositories(installation_id: int) -> List[Dict[str, Any]]:
""",
        """def _exchange_user_code(code: str) -> str:
    normalized = str(code or "").strip()
    if not normalized:
        raise DeveloperGithubError("github_user_authorization_required", status=400)
    client_id = github_client_id()
    if not client_id:
        raise DeveloperGithubError("github_oauth_not_configured", status=503)
    timeout = _env_int("VELIA_DEVELOPER_GITHUB_TIMEOUT_SECONDS", 20, 3, 60)
    try:
        response = HTTP.post(
            "https://github.com/login/oauth/access_token",
            headers={
                "Accept": "application/json",
                "User-Agent": "VELIA-Developer/1.0",
            },
            data={
                "client_id": client_id,
                "client_secret": _github_client_secret(),
                "code": normalized,
            },
            timeout=timeout,
        )
    except Exception as exc:
        raise DeveloperGithubError("github_oauth_unavailable", detail=exc.__class__.__name__) from exc
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise DeveloperGithubError("github_oauth_failed", status=401)
    data = _json_response(response)
    if not isinstance(data, dict):
        raise DeveloperGithubError("github_oauth_failed", status=401)
    error = str(data.get("error") or "").strip()
    token = str(data.get("access_token") or "").strip()
    if error or not token:
        raise DeveloperGithubError("github_oauth_failed", status=401, detail=error)
    return token


def authorize_user_installation(code: str, installation_id: int) -> Dict[str, Any]:
    target = int(installation_id)
    if target <= 0:
        raise DeveloperGithubError("invalid_installation", status=400)
    user_token = _exchange_user_code(code)
    authorized = False
    for page in range(1, 11):
        data = _request(
            "GET",
            "/user/installations",
            token=user_token,
            params={"per_page": 100, "page": page},
        )
        installations = data.get("installations") if isinstance(data, dict) else []
        if not isinstance(installations, list):
            installations = []
        if any(int(item.get("id") or 0) == target for item in installations if isinstance(item, dict)):
            authorized = True
            break
        if len(installations) < 100:
            break
    if not authorized:
        raise DeveloperGithubError("github_installation_not_authorized", status=403)
    user = _request("GET", "/user", token=user_token)
    details = installation_details(target)
    details["authorized_user_id"] = int(user.get("id") or 0) if isinstance(user, dict) else 0
    details["authorized_user_login"] = str(user.get("login") or "") if isinstance(user, dict) else ""
    return details


def list_installation_repositories(installation_id: int) -> List[Dict[str, Any]]:
""",
    )

    route_path = "services/velia_developer_routes.py"
    replace_once(
        route_path,
        """        installation_id = _int(request.query.get("installation_id"))
        try:
            payload = project_service.verify_install_state(state)
            if installation_id <= 0:
                raise project_service.DeveloperProjectError("invalid_installation", status=400)
            details = await asyncio.to_thread(github_service.installation_details, installation_id)
""",
        """        installation_id = _int(request.query.get("installation_id"))
        code = str(request.query.get("code") or "")
        try:
            payload = project_service.verify_install_state(state)
            if installation_id <= 0:
                raise project_service.DeveloperProjectError("invalid_installation", status=400)
            details = await asyncio.to_thread(
                github_service.authorize_user_installation,
                code,
                installation_id,
            )
""",
    )

    append_once(
        "tests/test_velia_developer_github_service.py",
        "test_user_oauth_binding_rejects_unowned_installation",
        r'''
def test_user_oauth_binding_rejects_unowned_installation(monkeypatch):
    monkeypatch.setattr(github, "_exchange_user_code", lambda code: "user-token")
    monkeypatch.setattr(
        github,
        "_request",
        lambda method, path, **kwargs: {
            "installations": [{"id": 999}],
            "total_count": 1,
        },
    )

    try:
        github.authorize_user_installation("oauth-code", 123)
        assert False
    except github.DeveloperGithubError as exc:
        assert exc.code == "github_installation_not_authorized"
        assert exc.status == 403


def test_user_oauth_binding_verifies_installation_and_user(monkeypatch):
    calls = []
    monkeypatch.setattr(github, "_exchange_user_code", lambda code: "user-token")

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("token")))
        if path == "/user/installations":
            return {"installations": [{"id": 123}], "total_count": 1}
        if path == "/user":
            return {"id": 77, "login": "octocat"}
        raise AssertionError(path)

    monkeypatch.setattr(github, "_request", fake_request)
    monkeypatch.setattr(
        github,
        "installation_details",
        lambda installation_id: {
            "installation_id": installation_id,
            "account_login": "owner",
            "contents_permission": "read",
        },
    )

    result = github.authorize_user_installation("oauth-code", 123)

    assert result["installation_id"] == 123
    assert result["authorized_user_id"] == 77
    assert result["authorized_user_login"] == "octocat"
    assert calls == [
        ("GET", "/user/installations", "user-token"),
        ("GET", "/user", "user-token"),
    ]


def test_github_app_configuration_requires_oauth_credentials(monkeypatch):
    monkeypatch.setenv("VELIA_GITHUB_APP_ID", "1")
    monkeypatch.setenv("VELIA_GITHUB_APP_SLUG", "velia-developer")
    monkeypatch.setenv("VELIA_GITHUB_APP_PRIVATE_KEY", "private")
    monkeypatch.delenv("VELIA_GITHUB_APP_CLIENT_ID", raising=False)
    monkeypatch.delenv("VELIA_GITHUB_APP_CLIENT_SECRET", raising=False)
    assert github.github_app_configured() is False

    monkeypatch.setenv("VELIA_GITHUB_APP_CLIENT_ID", "client")
    monkeypatch.setenv("VELIA_GITHUB_APP_CLIENT_SECRET", "secret")
    assert github.github_app_configured() is True
''',
    )
    append_once(
        "tests/test_velia_developer_bootstrap.py",
        "test_github_callback_requires_user_authorization_code",
        r'''
def test_github_callback_requires_user_authorization_code():
    source = Path("services/velia_developer_routes.py").read_text(encoding="utf-8")

    assert 'request.query.get("code")' in source
    assert "github_service.authorize_user_installation" in source
    assert "github_service.installation_details, installation_id" not in source
''',
    )


if __name__ == "__main__":
    main()
