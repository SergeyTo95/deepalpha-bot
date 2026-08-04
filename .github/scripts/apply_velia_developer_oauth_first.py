from pathlib import Path

routes_path = Path('services/velia_developer_routes.py')
tests_path = Path('tests/test_velia_developer_routes.py')

routes = routes_path.read_text(encoding='utf-8')

anchor = '''def _safe_redirect_url() -> str:\n    value = str(\n        os.getenv("VELIA_DEVELOPER_CALLBACK_REDIRECT", "velia://developer/github-connected")\n        or "velia://developer/github-connected"\n    ).strip()\n    if value.startswith("velia://") or value.startswith("https://"):\n        return value\n    return "velia://developer/github-connected"\n'''
replacement = anchor + '''\n\ndef _github_oauth_url(state: str) -> str:\n    return "https://github.com/login/oauth/authorize?" + urlencode(\n        {\n            "client_id": github_service.github_client_id(),\n            "state": str(state),\n            "prompt": "select_account",\n        }\n    )\n\n\ndef _github_install_url(state: str) -> str:\n    return (\n        f"https://github.com/apps/{quote(github_service.github_app_slug(), safe='')}"\n        f"/installations/new?{urlencode({'state': str(state)})}"\n    )\n'''
if anchor not in routes:
    raise SystemExit('safe redirect anchor not found')
routes = routes.replace(anchor, replacement, 1)

old_install = '''        try:\n            state = project_service.create_install_state(int(auth["user_id"]))\n            url = f"https://github.com/apps/{quote(github_service.github_app_slug(), safe='')}/installations/new?{urlencode({'state': state})}"\n            return routes_module._json_response({"ok": True, "url": url, "expires_in": 600})\n'''
new_install = '''        try:\n            state = project_service.create_install_state(int(auth["user_id"]))\n            return routes_module._json_response(\n                {\n                    "ok": True,\n                    "url": _github_oauth_url(state),\n                    "expires_in": 600,\n                    "flow": "oauth_first",\n                }\n            )\n'''
if old_install not in routes:
    raise SystemExit('install URL block not found')
routes = routes.replace(old_install, new_install, 1)

old_except = '''        except web.HTTPException:\n            raise\n        except Exception as exc:\n            code = getattr(exc, "code", "github_connection_failed")\n            redirect = _safe_redirect_url()\n'''
new_except = '''        except web.HTTPException:\n            raise\n        except github_service.DeveloperGithubError as exc:\n            if exc.code == "github_installation_not_found":\n                raise web.HTTPFound(location=_github_install_url(state))\n            code = exc.code\n            redirect = _safe_redirect_url()\n            separator = "&" if "?" in redirect else "?"\n            location = redirect + separator + urlencode({"connected": "false", "error": str(code)})\n            raise web.HTTPFound(location=location)\n        except Exception as exc:\n            code = getattr(exc, "code", "github_connection_failed")\n            redirect = _safe_redirect_url()\n'''
if old_except not in routes:
    raise SystemExit('callback except block not found')
routes = routes.replace(old_except, new_except, 1)

routes_path.write_text(routes, encoding='utf-8')

if tests_path.exists():
    tests = tests_path.read_text(encoding='utf-8')
else:
    tests = '''from urllib.parse import parse_qs, urlparse\n\nfrom services import velia_developer_routes as routes\n\n'''

if 'from urllib.parse import parse_qs, urlparse' not in tests:
    tests = 'from urllib.parse import parse_qs, urlparse\n' + tests
if 'from services import velia_developer_routes as routes' not in tests:
    tests += '\nfrom services import velia_developer_routes as routes\n'

addition = '''\n\ndef test_github_connect_starts_with_oauth_for_existing_installation(monkeypatch):\n    monkeypatch.setenv("VELIA_GITHUB_APP_CLIENT_ID", "Iv1.test-client")\n    url = routes._github_oauth_url("signed-state")\n    parsed = urlparse(url)\n    query = parse_qs(parsed.query)\n\n    assert parsed.scheme == "https"\n    assert parsed.netloc == "github.com"\n    assert parsed.path == "/login/oauth/authorize"\n    assert query == {\n        "client_id": ["Iv1.test-client"],\n        "state": ["signed-state"],\n        "prompt": ["select_account"],\n    }\n\n\ndef test_github_install_fallback_preserves_signed_state(monkeypatch):\n    monkeypatch.setenv("VELIA_GITHUB_APP_SLUG", "velia-developer-beta")\n    url = routes._github_install_url("signed-state")\n    parsed = urlparse(url)\n\n    assert parsed.scheme == "https"\n    assert parsed.netloc == "github.com"\n    assert parsed.path == "/apps/velia-developer-beta/installations/new"\n    assert parse_qs(parsed.query) == {"state": ["signed-state"]}\n'''
if 'test_github_connect_starts_with_oauth_for_existing_installation' not in tests:
    tests += addition

tests_path.write_text(tests, encoding='utf-8')
