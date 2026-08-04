import base64
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


_GITHUB_API = "https://api.github.com"
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_TOKEN_CACHE: Dict[Tuple[int, Tuple[int, ...]], Tuple[str, float]] = {}
HTTP = requests


class DeveloperGithubError(RuntimeError):
    def __init__(self, code: str, *, status: int = 502, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)
        self.detail = str(detail or "")[:300]


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 100000) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _private_key_pem() -> bytes:
    value = str(os.getenv("VELIA_GITHUB_APP_PRIVATE_KEY", "") or "").strip()
    if "\\n" in value and "\n" not in value:
        value = value.replace("\\n", "\n")
    if not value:
        raise DeveloperGithubError("github_app_not_configured", status=503)
    return value.encode("utf-8")


def github_app_id() -> str:
    return str(os.getenv("VELIA_GITHUB_APP_ID", "") or "").strip()


def github_app_slug() -> str:
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


def _app_jwt(*, now: Optional[int] = None) -> str:
    app_id = github_app_id()
    if not app_id:
        raise DeveloperGithubError("github_app_not_configured", status=503)
    current = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {"iat": current - 60, "exp": current + 540, "iss": app_id}
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    try:
        key = serialization.load_pem_private_key(_private_key_pem(), password=None)
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    except DeveloperGithubError:
        raise
    except Exception as exc:
        raise DeveloperGithubError("github_private_key_invalid", status=503, detail=exc.__class__.__name__) from exc
    return f"{encoded_header}.{encoded_payload}.{_b64url(signature)}"


def _api_version() -> str:
    return str(os.getenv("VELIA_GITHUB_API_VERSION", "2022-11-28") or "2022-11-28").strip()


def _headers(token: str, *, text_matches: bool = False) -> Dict[str, str]:
    accept = "application/vnd.github.text-match+json, application/vnd.github+json" if text_matches else "application/vnd.github+json"
    return {
        "Accept": accept,
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": _api_version(),
        "User-Agent": "VELIA-Developer/1.0",
    }


def _json_response(response: Any) -> Any:
    try:
        return response.json()
    except Exception as exc:
        raise DeveloperGithubError("github_invalid_response", detail=exc.__class__.__name__) from exc


def _request(
    method: str,
    path: str,
    *,
    token: str,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    expected: Iterable[int] = (200,),
    text_matches: bool = False,
) -> Any:
    timeout = _env_int("VELIA_DEVELOPER_GITHUB_TIMEOUT_SECONDS", 20, 3, 60)
    try:
        response = HTTP.request(
            method,
            f"{_GITHUB_API}{path}",
            headers=_headers(token, text_matches=text_matches),
            params=params,
            json=body,
            timeout=timeout,
        )
    except Exception as exc:
        raise DeveloperGithubError("github_unavailable", detail=exc.__class__.__name__) from exc
    status = int(getattr(response, "status_code", 0) or 0)
    if status not in set(int(value) for value in expected):
        data: Any = {}
        try:
            data = response.json()
        except Exception:
            pass
        message = str(data.get("message") or "") if isinstance(data, dict) else ""
        if status in {401, 403}:
            code = "github_forbidden"
        elif status == 404:
            code = "github_not_found"
        elif status == 422:
            code = "github_invalid_request"
        elif status == 429:
            code = "github_rate_limited"
        else:
            code = "github_api_error"
        raise DeveloperGithubError(code, status=503 if status >= 500 else status, detail=message)
    if status == 204:
        return None
    return _json_response(response)


def _parse_expiry(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        return time.time() + 3000
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except Exception:
        return time.time() + 3000


def _installation_token(installation_id: int, repository_ids: Optional[Iterable[int]] = None) -> str:
    installation = int(installation_id)
    repo_ids = tuple(sorted({int(value) for value in (repository_ids or []) if int(value) > 0}))
    key = (installation, repo_ids)
    cached = _TOKEN_CACHE.get(key)
    if cached and cached[1] - 90 > time.time():
        return cached[0]
    body: Dict[str, Any] = {}
    if repo_ids:
        body["repository_ids"] = list(repo_ids)
    data = _request(
        "POST",
        f"/app/installations/{installation}/access_tokens",
        token=_app_jwt(),
        body=body,
        expected=(201,),
    )
    token = str(data.get("token") or "") if isinstance(data, dict) else ""
    if not token:
        raise DeveloperGithubError("github_token_missing")
    _TOKEN_CACHE[key] = (token, _parse_expiry(data.get("expires_at")))
    return token


def installation_details(installation_id: int) -> Dict[str, Any]:
    data = _request(
        "GET",
        f"/app/installations/{int(installation_id)}",
        token=_app_jwt(),
    )
    account = data.get("account") if isinstance(data, dict) else {}
    permissions = data.get("permissions") if isinstance(data, dict) else {}
    if not isinstance(account, dict):
        account = {}
    if not isinstance(permissions, dict):
        permissions = {}
    contents_permission = str(permissions.get("contents") or "").lower()
    if contents_permission not in {"read", "write"}:
        raise DeveloperGithubError("github_contents_permission_required", status=403)
    return {
        "installation_id": int(data.get("id") or installation_id),
        "account_login": str(account.get("login") or ""),
        "account_type": str(account.get("type") or ""),
        "repository_selection": str(data.get("repository_selection") or ""),
        "contents_permission": contents_permission,
    }


def _exchange_user_code(code: str) -> str:
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


def authorize_user_installations(code: str, installation_id: int = 0) -> List[Dict[str, Any]]:
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


def list_installation_repositories(installation_id: int) -> List[Dict[str, Any]]:
    token = _installation_token(installation_id)
    repositories: List[Dict[str, Any]] = []
    for page in range(1, 11):
        data = _request(
            "GET",
            "/installation/repositories",
            token=token,
            params={"per_page": 100, "page": page},
        )
        items = data.get("repositories") if isinstance(data, dict) else []
        if not isinstance(items, list):
            items = []
        for repo in items:
            if not isinstance(repo, dict):
                continue
            permissions = repo.get("permissions") or {}
            repositories.append(
                {
                    "id": int(repo.get("id") or 0),
                    "full_name": str(repo.get("full_name") or ""),
                    "name": str(repo.get("name") or ""),
                    "owner": str((repo.get("owner") or {}).get("login") or ""),
                    "private": bool(repo.get("private")),
                    "default_branch": str(repo.get("default_branch") or "main"),
                    "archived": bool(repo.get("archived")),
                    "contents_read": bool(permissions.get("pull") or permissions.get("push") or permissions.get("admin")),
                }
            )
        if len(items) < 100:
            break
    return [repo for repo in repositories if repo["id"] and repo["full_name"]]


def _validate_full_name(full_name: str) -> Tuple[str, str]:
    normalized = str(full_name or "").strip()
    parts = normalized.split("/")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", part) for part in parts):
        raise DeveloperGithubError("invalid_repository", status=400)
    return parts[0], parts[1]


def validate_branch(branch: str) -> str:
    normalized = str(branch or "").strip()
    if not _BRANCH_RE.fullmatch(normalized) or ".." in normalized or normalized.startswith("/") or normalized.endswith("/") or "//" in normalized:
        raise DeveloperGithubError("invalid_branch", status=400)
    return normalized


def validate_path(path: str, *, allow_empty: bool = False) -> str:
    normalized = str(path or "").replace("\\", "/").strip().strip("/")
    if not normalized and allow_empty:
        return ""
    if not normalized or len(normalized) > 500:
        raise DeveloperGithubError("invalid_path", status=400)
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts) or any("\x00" in part for part in parts):
        raise DeveloperGithubError("invalid_path", status=400)
    return normalized


def repository_metadata(installation_id: int, repository_id: int, full_name: str) -> Dict[str, Any]:
    owner, name = _validate_full_name(full_name)
    token = _installation_token(installation_id, [repository_id])
    data = _request("GET", f"/repos/{quote(owner)}/{quote(name)}", token=token)
    if int(data.get("id") or 0) != int(repository_id):
        raise DeveloperGithubError("repository_identity_mismatch", status=409)
    return {
        "id": int(data.get("id") or 0),
        "full_name": str(data.get("full_name") or full_name),
        "owner": owner,
        "name": name,
        "private": bool(data.get("private")),
        "archived": bool(data.get("archived")),
        "default_branch": str(data.get("default_branch") or "main"),
    }


def list_branches(installation_id: int, repository_id: int, full_name: str) -> List[Dict[str, Any]]:
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


def list_tree(
    installation_id: int,
    repository_id: int,
    full_name: str,
    branch: str,
    *,
    prefix: str = "",
) -> Dict[str, Any]:
    owner, name = _validate_full_name(full_name)
    selected_branch = validate_branch(branch)
    selected_prefix = validate_path(prefix, allow_empty=True)
    token = _installation_token(installation_id, [repository_id])
    data = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/git/trees/{quote(selected_branch, safe='')}",
        token=token,
        params={"recursive": "1"},
    )
    max_entries = _env_int("VELIA_DEVELOPER_MAX_TREE_ENTRIES", 5000, 100, 20000)
    entries = []
    for item in data.get("tree", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if selected_prefix and not (path == selected_prefix or path.startswith(selected_prefix + "/")):
            continue
        entries.append(
            {
                "path": path,
                "type": str(item.get("type") or ""),
                "size": int(item.get("size") or 0),
                "sha": str(item.get("sha") or ""),
            }
        )
        if len(entries) >= max_entries:
            break
    return {"entries": entries, "truncated": bool(data.get("truncated")) or len(entries) >= max_entries}


def read_file(
    installation_id: int,
    repository_id: int,
    full_name: str,
    branch: str,
    path: str,
    *,
    start_line: int = 1,
    end_line: int = 240,
) -> Dict[str, Any]:
    owner, name = _validate_full_name(full_name)
    selected_branch = validate_branch(branch)
    selected_path = validate_path(path)
    start = max(1, int(start_line or 1))
    maximum_lines = _env_int("VELIA_DEVELOPER_MAX_READ_LINES", 400, 20, 1000)
    end = max(start, min(int(end_line or start + maximum_lines - 1), start + maximum_lines - 1))
    token = _installation_token(installation_id, [repository_id])
    data = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/contents/{quote(selected_path, safe='/')}",
        token=token,
        params={"ref": selected_branch},
    )
    if not isinstance(data, dict) or str(data.get("type") or "") != "file":
        raise DeveloperGithubError("github_path_not_file", status=400)
    encoded = str(data.get("content") or "").replace("\n", "")
    try:
        raw = base64.b64decode(encoded, validate=False)
    except Exception as exc:
        raise DeveloperGithubError("github_file_decode_failed") from exc
    max_bytes = _env_int("VELIA_DEVELOPER_MAX_FILE_BYTES", 524288, 1024, 2_000_000)
    if len(raw) > max_bytes:
        raise DeveloperGithubError("github_file_too_large", status=413)
    if b"\x00" in raw[:8192]:
        raise DeveloperGithubError("github_binary_file", status=415)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DeveloperGithubError("github_non_utf8_file", status=415) from exc
    lines = text.splitlines()
    selected = lines[start - 1 : end]
    numbered = "\n".join(f"{index}: {value}" for index, value in enumerate(selected, start=start))
    return {
        "path": selected_path,
        "sha": str(data.get("sha") or ""),
        "size": len(raw),
        "start_line": start,
        "end_line": start + max(0, len(selected) - 1),
        "total_lines": len(lines),
        "content": numbered,
    }


_SEARCHABLE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".dart", ".go", ".gradle", ".graphql",
    ".h", ".hpp", ".html", ".java", ".js", ".json", ".jsx", ".kt", ".kts",
    ".md", ".php", ".proto", ".py", ".rb", ".rs", ".scss", ".sh", ".sql",
    ".swift", ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}
_SEARCH_SKIP_PARTS = {
    ".git", ".gradle", ".idea", ".next", ".venv", "build", "coverage", "dist",
    "generated", "node_modules", "target", "vendor", "venv",
}


def _searchable_branch_path(path: str, size: int, max_bytes: int) -> bool:
    normalized = str(path or "").replace("\\", "/")
    parts = {part.lower() for part in normalized.split("/")}
    if parts & _SEARCH_SKIP_PARTS or size <= 0 or size > max_bytes:
        return False
    lowered = normalized.lower()
    if lowered.endswith((".lock", ".min.js", ".min.css", ".map")):
        return False
    name = lowered.rsplit("/", 1)[-1]
    return name in {"dockerfile", "makefile", "gradle.properties"} or any(
        lowered.endswith(suffix) for suffix in _SEARCHABLE_SUFFIXES
    )


def _decode_search_blob(data: Any, max_bytes: int) -> Optional[str]:
    if not isinstance(data, dict) or str(data.get("encoding") or "").lower() != "base64":
        return None
    try:
        raw = base64.b64decode(str(data.get("content") or "").replace("\n", ""), validate=False)
    except Exception:
        return None
    if not raw or len(raw) > max_bytes or b"\x00" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _branch_search_fragments(text: str, terms: List[str]) -> List[str]:
    lines = text.splitlines()
    matches = [
        index
        for index, line in enumerate(lines)
        if any(term in line.casefold() for term in terms)
    ]
    fragments: List[str] = []
    used: set[Tuple[int, int]] = set()
    for index in matches:
        start = max(0, index - 1)
        end = min(len(lines), index + 2)
        key = (start, end)
        if key in used:
            continue
        used.add(key)
        fragments.append("\n".join(f"{line_number + 1}: {lines[line_number]}" for line_number in range(start, end))[:1200])
        if len(fragments) >= 3:
            break
    return fragments


def _search_selected_branch(
    installation_id: int,
    repository_id: int,
    full_name: str,
    branch: str,
    normalized: str,
) -> List[Dict[str, Any]]:
    owner, name = _validate_full_name(full_name)
    selected_branch = validate_branch(branch)
    token = _installation_token(installation_id, [repository_id])
    tree = list_tree(installation_id, repository_id, full_name, selected_branch)
    terms = [part.casefold() for part in re.findall(r"[\w.-]+", normalized) if len(part) >= 2]
    if not terms:
        terms = [normalized.casefold()]
    max_files = _env_int("VELIA_DEVELOPER_BRANCH_SEARCH_MAX_FILES", 60, 5, 300)
    max_bytes = _env_int("VELIA_DEVELOPER_BRANCH_SEARCH_MAX_FILE_BYTES", 262144, 1024, 1048576)
    result_limit = _env_int("VELIA_DEVELOPER_SEARCH_RESULT_LIMIT", 20, 1, 50)
    entries = [
        item for item in (tree.get("entries") or [])
        if isinstance(item, dict)
        and str(item.get("type") or "") == "blob"
        and _searchable_branch_path(str(item.get("path") or ""), int(item.get("size") or 0), max_bytes)
    ]
    entries.sort(
        key=lambda item: (
            0 if any(term in str(item.get("path") or "").casefold() for term in terms) else 1,
            int(item.get("size") or 0),
            str(item.get("path") or ""),
        )
    )
    results: Dict[str, Dict[str, Any]] = {}
    scanned = 0
    for item in entries:
        path = str(item.get("path") or "")
        path_folded = path.casefold()
        path_match = all(term in path_folded for term in terms)
        if path_match:
            results[path] = {
                "path": path,
                "sha": str(item.get("sha") or ""),
                "score": 2.0,
                "fragments": [],
            }
        if scanned >= max_files:
            continue
        scanned += 1
        data = _request(
            "GET",
            f"/repos/{quote(owner)}/{quote(name)}/git/blobs/{quote(str(item.get('sha') or ''), safe='')}",
            token=token,
        )
        text = _decode_search_blob(data, max_bytes)
        if text is None:
            continue
        folded = text.casefold()
        if not all(term in folded for term in terms):
            continue
        existing = results.get(path) or {
            "path": path,
            "sha": str(item.get("sha") or ""),
            "score": 1.0,
            "fragments": [],
        }
        existing["score"] = max(float(existing.get("score") or 0.0), 1.5 if path_match else 1.0)
        existing["fragments"] = _branch_search_fragments(text, terms)
        results[path] = existing
        if len(results) >= result_limit and scanned >= max_files:
            break
    return sorted(results.values(), key=lambda item: (-float(item.get("score") or 0.0), str(item.get("path") or "")))[:result_limit]


def search_code(
    installation_id: int,
    repository_id: int,
    full_name: str,
    query: str,
    *,
    branch: str = "",
    default_branch: str = "",
) -> List[Dict[str, Any]]:
    owner, name = _validate_full_name(full_name)
    normalized = re.sub(r"\s+", " ", str(query or "").strip())[:200]
    if len(normalized) < 2 or re.search(r"\b(repo|org|user):", normalized, flags=re.IGNORECASE):
        raise DeveloperGithubError("invalid_search_query", status=400)
    selected_branch = validate_branch(branch or default_branch or "main")
    repository_default = validate_branch(default_branch or selected_branch)
    if selected_branch != repository_default:
        return _search_selected_branch(
            installation_id,
            repository_id,
            full_name,
            selected_branch,
            normalized,
        )
    token = _installation_token(installation_id, [repository_id])
    data = _request(
        "GET",
        "/search/code",
        token=token,
        params={"q": f"{normalized} repo:{owner}/{name}", "per_page": 20},
        text_matches=True,
    )
    results = []
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        fragments = []
        for match in item.get("text_matches", []) or []:
            if isinstance(match, dict) and str(match.get("fragment") or "").strip():
                fragments.append(str(match.get("fragment") or "")[:1200])
        results.append(
            {
                "path": str(item.get("path") or ""),
                "sha": str(item.get("sha") or ""),
                "score": float(item.get("score") or 0.0),
                "fragments": fragments[:3],
            }
        )
    return results
