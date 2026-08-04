from pathlib import Path

GITHUB_SERVICE = Path("services/velia_developer_github_service.py")
ROUTES = Path("services/velia_developer_routes.py")
TESTS = Path("tests/test_velia_developer_github_service.py")

old_authorize = '''def authorize_user_installation(code: str, installation_id: int) -> Dict[str, Any]:
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
'''

new_authorize = '''def authorize_user_installations(code: str, installation_id: int = 0) -> List[Dict[str, Any]]:
    target = int(installation_id or 0)
    user_token = _exchange_user_code(code)
    accessible: List[Dict[str, Any]] = []
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
        accessible.extend(item for item in installations if isinstance(item, dict))
        if len(installations) < 100:
            break

    selected = [
        item for item in accessible
        if target <= 0 or int(item.get("id") or 0) == target
    ]
    if not selected:
        code = "github_installation_not_authorized" if target > 0 else "github_installation_not_found"
        raise DeveloperGithubError(code, status=403 if target > 0 else 404)

    user = _request("GET", "/user", token=user_token)
    user_id = int(user.get("id") or 0) if isinstance(user, dict) else 0
    user_login = str(user.get("login") or "") if isinstance(user, dict) else ""
    details_list: List[Dict[str, Any]] = []
    seen = set()
    for item in selected:
        current_id = int(item.get("id") or 0)
        if current_id <= 0 or current_id in seen:
            continue
        seen.add(current_id)
        details = installation_details(current_id)
        details["authorized_user_id"] = user_id
        details["authorized_user_login"] = user_login
        details_list.append(details)
    if not details_list:
        raise DeveloperGithubError("github_installation_not_found", status=404)
    return details_list


def authorize_user_installation(code: str, installation_id: int) -> Dict[str, Any]:
    return authorize_user_installations(code, installation_id)[0]
'''

old_callback = '''            payload = project_service.verify_install_state(state)
            if installation_id <= 0:
                raise project_service.DeveloperProjectError("invalid_installation", status=400)
            details = await asyncio.to_thread(
                github_service.authorize_user_installation,
                code,
                installation_id,
            )
            await asyncio.to_thread(project_service.record_installation, int(payload["user_id"]), details)
            separator = "&" if "?" in _safe_redirect_url() else "?"
            location = _safe_redirect_url() + separator + urlencode({"connected": "true", "installation_id": installation_id})
'''

new_callback = '''            payload = project_service.verify_install_state(state)
            details_list = await asyncio.to_thread(
                github_service.authorize_user_installations,
                code,
                installation_id,
            )
            for details in details_list:
                await asyncio.to_thread(
                    project_service.record_installation,
                    int(payload["user_id"]),
                    details,
                )
            primary_installation_id = int(details_list[0]["installation_id"])
            separator = "&" if "?" in _safe_redirect_url() else "?"
            location = _safe_redirect_url() + separator + urlencode(
                {
                    "connected": "true",
                    "installation_id": primary_installation_id,
                    "installation_count": len(details_list),
                }
            )
'''

service_text = GITHUB_SERVICE.read_text(encoding="utf-8")
if old_authorize not in service_text:
    raise SystemExit("authorize block not found")
GITHUB_SERVICE.write_text(service_text.replace(old_authorize, new_authorize, 1), encoding="utf-8")

routes_text = ROUTES.read_text(encoding="utf-8")
if old_callback not in routes_text:
    raise SystemExit("callback block not found")
ROUTES.write_text(routes_text.replace(old_callback, new_callback, 1), encoding="utf-8")

tests_text = TESTS.read_text(encoding="utf-8")
addition = '''\n\ndef test_user_oauth_callback_discovers_installations_without_redirect_id(monkeypatch):
    monkeypatch.setattr(github, "_exchange_user_code", lambda code: "user-token")

    def fake_request(method, path, **kwargs):
        if path == "/user/installations":
            return {
                "installations": [{"id": 123}, {"id": 456}, {"id": 123}],
                "total_count": 3,
            }
        if path == "/user":
            return {"id": 77, "login": "octocat"}
        raise AssertionError(path)

    monkeypatch.setattr(github, "_request", fake_request)
    monkeypatch.setattr(
        github,
        "installation_details",
        lambda installation_id: {
            "installation_id": installation_id,
            "account_login": f"account-{installation_id}",
            "contents_permission": "read",
        },
    )

    result = github.authorize_user_installations("oauth-code", 0)

    assert [item["installation_id"] for item in result] == [123, 456]
    assert all(item["authorized_user_id"] == 77 for item in result)
    assert all(item["authorized_user_login"] == "octocat" for item in result)


def test_callback_contract_does_not_require_installation_id():
    source = Path("services/velia_developer_routes.py").read_text(encoding="utf-8")
    callback = source.split("async def github_callback", 1)[1].split("async def installations", 1)[0]
    assert "authorize_user_installations" in callback
    assert "if installation_id <= 0" not in callback
'''
if "test_user_oauth_callback_discovers_installations_without_redirect_id" not in tests_text:
    if "from pathlib import Path" not in tests_text:
        tests_text = tests_text.replace("import json\n", "import json\nfrom pathlib import Path\n", 1)
    TESTS.write_text(tests_text + addition, encoding="utf-8")
