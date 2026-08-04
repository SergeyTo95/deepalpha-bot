from pathlib import Path

service_path = Path('services/velia_developer_github_service.py')
source = service_path.read_text(encoding='utf-8')
old = '''def list_branches(installation_id: int, repository_id: int, full_name: str) -> List[Dict[str, Any]]:
    owner, name = _validate_full_name(full_name)
    token = _installation_token(installation_id, [repository_id])
    data = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/branches",
        token=token,
        params={"per_page": 100},
    )
    result = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        branch_name = str(item.get("name") or "")
        commit = item.get("commit") or {}
        if branch_name:
            result.append({"name": branch_name, "sha": str(commit.get("sha") or ""), "protected": bool(item.get("protected"))})
    return result
'''
new = '''def list_branches(installation_id: int, repository_id: int, full_name: str) -> List[Dict[str, Any]]:
    owner, name = _validate_full_name(full_name)
    token = _installation_token(installation_id, [repository_id])
    result: List[Dict[str, Any]] = []
    seen = set()
    for page in range(1, 11):
        data = _request(
            "GET",
            f"/repos/{quote(owner)}/{quote(name)}/branches",
            token=token,
            params={"per_page": 100, "page": page},
        )
        items = data if isinstance(data, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            branch_name = str(item.get("name") or "")
            if not branch_name or branch_name in seen:
                continue
            seen.add(branch_name)
            commit = item.get("commit") or {}
            result.append(
                {
                    "name": branch_name,
                    "sha": str(commit.get("sha") or ""),
                    "protected": bool(item.get("protected")),
                }
            )
        if len(items) < 100:
            break
    return result
'''
if old not in source:
    raise SystemExit('list_branches target not found')
service_path.write_text(source.replace(old, new), encoding='utf-8')

route_path = Path('services/velia_developer_routes.py')
routes = route_path.read_text(encoding='utf-8')
needle = '''    async def repositories(request: web.Request) -> web.Response:
        blocked = _require_feature(routes_module, request)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        installation_id = _int(request.query.get("installation_id"))
        try:
            await asyncio.to_thread(project_service.get_installation, int(auth["user_id"]), installation_id)
            items = await asyncio.to_thread(github_service.list_installation_repositories, installation_id)
            return routes_module._json_response({"ok": True, "repositories": items})
        except Exception as exc:
            return _error_response(routes_module, exc)

'''
insert = needle + '''    async def repository_branches(request: web.Request) -> web.Response:
        blocked = _require_feature(routes_module, request)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        installation_id = _int(request.query.get("installation_id"))
        repository_id = _int(request.match_info.get("repository_id"))
        try:
            await asyncio.to_thread(project_service.get_installation, int(auth["user_id"]), installation_id)
            repositories_list = await asyncio.to_thread(github_service.list_installation_repositories, installation_id)
            repository = next(
                (item for item in repositories_list if int(item.get("id") or 0) == repository_id),
                None,
            )
            if not repository:
                raise project_service.DeveloperProjectError("repository_not_accessible", status=404)
            metadata = await asyncio.to_thread(
                github_service.repository_metadata,
                installation_id,
                repository_id,
                str(repository["full_name"]),
            )
            items = await asyncio.to_thread(
                github_service.list_branches,
                installation_id,
                repository_id,
                str(metadata["full_name"]),
            )
            return routes_module._json_response(
                {
                    "ok": True,
                    "default_branch": str(metadata["default_branch"]),
                    "branches": items,
                }
            )
        except Exception as exc:
            return _error_response(routes_module, exc)

'''
if needle not in routes:
    raise SystemExit('repository route target not found')
routes = routes.replace(needle, insert)
registration = '''    app.router.add_get(f"{_PREFIX}/repositories", repositories)'''
registration_new = registration + '''
    app.router.add_get(
        f"{_PREFIX}/repositories/{repository_id}/branches",
        repository_branches,
    )'''
if registration not in routes:
    raise SystemExit('route registration target not found')
routes = routes.replace(registration, registration_new)
route_path.write_text(routes, encoding='utf-8')

Path('tests/test_velia_developer_branch_selection.py').write_text('''from pathlib import Path\n\nfrom services import velia_developer_github_service as github\n\n\ndef test_list_branches_paginates_until_selected_branch_can_be_found(monkeypatch):\n    calls = []\n    monkeypatch.setattr(github, "_installation_token", lambda *args, **kwargs: "token")\n\n    def fake_request(method, path, **kwargs):\n        page = kwargs["params"]["page"]\n        calls.append(page)\n        if page == 1:\n            return [\n                {"name": f"feature/branch-{index:03d}", "commit": {"sha": str(index)}}\n                for index in range(100)\n            ]\n        if page == 2:\n            return [\n                {"name": "main", "commit": {"sha": "main-sha"}, "protected": True},\n                {"name": "feature/turbo-short-term-btc", "commit": {"sha": "prod-sha"}},\n            ]\n        raise AssertionError(page)\n\n    monkeypatch.setattr(github, "_request", fake_request)\n    branches = github.list_branches(1, 2, "owner/repo")\n\n    assert calls == [1, 2]\n    assert any(item["name"] == "main" for item in branches)\n    assert any(item["name"] == "feature/turbo-short-term-btc" for item in branches)\n\n\ndef test_repository_branch_route_is_registered():\n    source = Path("services/velia_developer_routes.py").read_text(encoding="utf-8")\n    assert '/repositories/{repository_id}/branches' in source\n    assert 'async def repository_branches' in source\n''', encoding='utf-8')
