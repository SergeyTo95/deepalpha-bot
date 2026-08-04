import base64
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from services import velia_developer_github_service as github_service


class DeveloperWriteError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)
        self.detail = str(detail or "")[:300]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def write_enabled() -> bool:
    return _env_bool("VELIA_DEVELOPER_WRITE_ENABLED", False)


def _project_values(project: Dict[str, Any]) -> Tuple[int, int, str, str]:
    installation_id = int(project.get("installation_id") or 0)
    repository_id = int(project.get("repository_id") or 0)
    full_name = str(project.get("repository_full_name") or "").strip()
    base_branch = github_service.validate_branch(str(project.get("selected_branch") or ""))
    if installation_id <= 0 or repository_id <= 0 or "/" not in full_name:
        raise DeveloperWriteError("developer_project_invalid", status=400)
    return installation_id, repository_id, full_name, base_branch


def _owner_name(full_name: str) -> Tuple[str, str]:
    try:
        return github_service._validate_full_name(full_name)
    except github_service.DeveloperGithubError as exc:
        raise DeveloperWriteError(exc.code, status=exc.status, detail=exc.detail) from exc


def _token(project: Dict[str, Any]) -> str:
    installation_id, repository_id, _, _ = _project_values(project)
    try:
        return github_service._installation_token(installation_id, [repository_id])
    except github_service.DeveloperGithubError as exc:
        raise DeveloperWriteError(exc.code, status=exc.status, detail=exc.detail) from exc


def require_write_permissions(project: Dict[str, Any]) -> Dict[str, str]:
    if not write_enabled():
        raise DeveloperWriteError("developer_write_disabled", status=403)
    installation_id, _, _, _ = _project_values(project)
    try:
        data = github_service._request(
            "GET",
            f"/app/installations/{installation_id}",
            token=github_service._app_jwt(),
        )
    except github_service.DeveloperGithubError as exc:
        raise DeveloperWriteError(exc.code, status=exc.status, detail=exc.detail) from exc
    permissions = data.get("permissions") if isinstance(data, dict) else {}
    if not isinstance(permissions, dict):
        permissions = {}
    contents = str(permissions.get("contents") or "").lower()
    pull_requests = str(permissions.get("pull_requests") or "").lower()
    workflows = str(permissions.get("workflows") or "").lower()
    if contents != "write":
        raise DeveloperWriteError("github_contents_write_permission_required", status=403)
    if pull_requests != "write":
        raise DeveloperWriteError("github_pull_requests_write_permission_required", status=403)
    return {
        "contents": contents,
        "pull_requests": pull_requests,
        "workflows": workflows,
    }


def _validate_work_branch(branch: str, base_branch: str) -> str:
    selected = github_service.validate_branch(branch)
    prefix = str(os.getenv("VELIA_DEVELOPER_WORK_BRANCH_PREFIX", "velia/") or "velia/").strip()
    if not prefix.endswith("/"):
        prefix += "/"
    if selected == base_branch or not selected.startswith(prefix):
        raise DeveloperWriteError("developer_unsafe_write_branch", status=400)
    return selected


def _protected_path(path: str) -> bool:
    normalized = github_service.validate_path(path)
    lowered = normalized.casefold()
    blocked_names = {
        ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
        "credentials.json", "service-account.json", "secrets.json",
    }
    name = lowered.rsplit("/", 1)[-1]
    if name in blocked_names or "secret" in name or "private_key" in name:
        return True
    if lowered.startswith(".github/workflows/") and not _env_bool(
        "VELIA_DEVELOPER_WORKFLOW_WRITE_ENABLED", False
    ):
        return True
    return False


def _request(*args: Any, **kwargs: Any) -> Any:
    try:
        return github_service._request(*args, **kwargs)
    except github_service.DeveloperGithubError as exc:
        raise DeveloperWriteError(exc.code, status=exc.status, detail=exc.detail) from exc


def branch_head(project: Dict[str, Any], branch: str) -> Dict[str, str]:
    _, _, full_name, _ = _project_values(project)
    owner, name = _owner_name(full_name)
    selected = github_service.validate_branch(branch)
    data = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/git/ref/heads/{quote(selected, safe='')}",
        token=_token(project),
    )
    obj = data.get("object") if isinstance(data, dict) else {}
    sha = str((obj or {}).get("sha") or "")
    if not sha:
        raise DeveloperWriteError("github_branch_head_missing", status=502)
    return {"branch": selected, "sha": sha}


def create_work_branch(project: Dict[str, Any], branch: str) -> Dict[str, str]:
    require_write_permissions(project)
    _, _, full_name, base_branch = _project_values(project)
    selected = _validate_work_branch(branch, base_branch)
    owner, name = _owner_name(full_name)
    base = branch_head(project, base_branch)
    data = _request(
        "POST",
        f"/repos/{quote(owner)}/{quote(name)}/git/refs",
        token=_token(project),
        body={"ref": f"refs/heads/{selected}", "sha": base["sha"]},
        expected=(201,),
    )
    obj = data.get("object") if isinstance(data, dict) else {}
    return {"branch": selected, "sha": str((obj or {}).get("sha") or base["sha"])}


def read_utf8_file(project: Dict[str, Any], branch: str, path: str) -> Dict[str, Any]:
    _, _, full_name, base_branch = _project_values(project)
    selected = _validate_work_branch(branch, base_branch)
    selected_path = github_service.validate_path(path)
    owner, name = _owner_name(full_name)
    data = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/contents/{quote(selected_path, safe='/')}",
        token=_token(project),
        params={"ref": selected},
    )
    if not isinstance(data, dict) or str(data.get("type") or "") != "file":
        raise DeveloperWriteError("github_path_not_file", status=400)
    encoded = str(data.get("content") or "").replace("\n", "")
    try:
        raw = base64.b64decode(encoded, validate=False)
    except Exception as exc:
        raise DeveloperWriteError("github_file_decode_failed", status=502) from exc
    maximum = _env_int("VELIA_DEVELOPER_WRITE_MAX_FILE_BYTES", 300000, 1024, 1000000)
    if len(raw) > maximum:
        raise DeveloperWriteError("developer_write_file_too_large", status=413)
    if b"\x00" in raw[:8192]:
        raise DeveloperWriteError("github_binary_file", status=415)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DeveloperWriteError("github_non_utf8_file", status=415) from exc
    return {"path": selected_path, "sha": str(data.get("sha") or ""), "content": text}


def _validate_operations(operations: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    maximum_files = _env_int("VELIA_DEVELOPER_WRITE_MAX_FILES_PER_STEP", 6, 1, 20)
    maximum_bytes = _env_int("VELIA_DEVELOPER_WRITE_MAX_TOTAL_BYTES_PER_STEP", 250000, 1024, 1000000)
    validated: List[Dict[str, Any]] = []
    total_bytes = 0
    seen = set()
    for raw in operations:
        if not isinstance(raw, dict):
            raise DeveloperWriteError("developer_patch_invalid", status=400)
        op = str(raw.get("op") or "").strip().lower()
        path = github_service.validate_path(str(raw.get("path") or ""))
        if path in seen:
            raise DeveloperWriteError("developer_duplicate_file_operation", status=400)
        seen.add(path)
        if _protected_path(path):
            raise DeveloperWriteError("developer_protected_path", status=403, detail=path)
        if op not in {"upsert", "delete"}:
            raise DeveloperWriteError("developer_patch_invalid", status=400)
        item: Dict[str, Any] = {"op": op, "path": path}
        if op == "upsert":
            content = str(raw.get("content") if raw.get("content") is not None else "")
            encoded = content.encode("utf-8")
            maximum_file = _env_int("VELIA_DEVELOPER_WRITE_MAX_FILE_BYTES", 300000, 1024, 1000000)
            if len(encoded) > maximum_file:
                raise DeveloperWriteError("developer_write_file_too_large", status=413, detail=path)
            total_bytes += len(encoded)
            item["content"] = content
        validated.append(item)
        if len(validated) > maximum_files or total_bytes > maximum_bytes:
            raise DeveloperWriteError("developer_patch_too_large", status=413)
    if not validated:
        raise DeveloperWriteError("developer_patch_empty", status=400)
    return validated


def commit_operations(
    project: Dict[str, Any],
    *,
    branch: str,
    operations: Iterable[Dict[str, Any]],
    message: str,
) -> Dict[str, Any]:
    require_write_permissions(project)
    _, _, full_name, base_branch = _project_values(project)
    selected = _validate_work_branch(branch, base_branch)
    owner, name = _owner_name(full_name)
    token = _token(project)
    validated = _validate_operations(operations)
    head = branch_head(project, selected)
    commit = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/git/commits/{quote(head['sha'])}",
        token=token,
    )
    base_tree = str(((commit or {}).get("tree") or {}).get("sha") or "")
    if not base_tree:
        raise DeveloperWriteError("github_tree_missing", status=502)
    tree_entries: List[Dict[str, Any]] = []
    for operation in validated:
        if operation["op"] == "delete":
            tree_entries.append(
                {"path": operation["path"], "mode": "100644", "type": "blob", "sha": None}
            )
            continue
        blob = _request(
            "POST",
            f"/repos/{quote(owner)}/{quote(name)}/git/blobs",
            token=token,
            body={"content": operation["content"], "encoding": "utf-8"},
            expected=(201,),
        )
        blob_sha = str((blob or {}).get("sha") or "")
        if not blob_sha:
            raise DeveloperWriteError("github_blob_missing", status=502)
        tree_entries.append(
            {"path": operation["path"], "mode": "100644", "type": "blob", "sha": blob_sha}
        )
    tree = _request(
        "POST",
        f"/repos/{quote(owner)}/{quote(name)}/git/trees",
        token=token,
        body={"base_tree": base_tree, "tree": tree_entries},
        expected=(201,),
    )
    tree_sha = str((tree or {}).get("sha") or "")
    if not tree_sha:
        raise DeveloperWriteError("github_tree_missing", status=502)
    created = _request(
        "POST",
        f"/repos/{quote(owner)}/{quote(name)}/git/commits",
        token=token,
        body={
            "message": str(message or "VELIA Coding Agent update")[:240],
            "tree": tree_sha,
            "parents": [head["sha"]],
        },
        expected=(201,),
    )
    commit_sha = str((created or {}).get("sha") or "")
    if not commit_sha:
        raise DeveloperWriteError("github_commit_missing", status=502)
    _request(
        "PATCH",
        f"/repos/{quote(owner)}/{quote(name)}/git/refs/heads/{quote(selected, safe='')}",
        token=token,
        body={"sha": commit_sha, "force": False},
    )
    return {
        "branch": selected,
        "commit_sha": commit_sha,
        "files": [item["path"] for item in validated],
    }


def create_draft_pull_request(
    project: Dict[str, Any],
    *,
    branch: str,
    title: str,
    body: str,
) -> Dict[str, Any]:
    require_write_permissions(project)
    _, _, full_name, base_branch = _project_values(project)
    selected = _validate_work_branch(branch, base_branch)
    owner, name = _owner_name(full_name)
    data = _request(
        "POST",
        f"/repos/{quote(owner)}/{quote(name)}/pulls",
        token=_token(project),
        body={
            "title": str(title or "VELIA Coding Agent changes")[:240],
            "head": selected,
            "base": base_branch,
            "body": str(body or "")[:60000],
            "draft": True,
        },
        expected=(201,),
    )
    return {
        "number": int((data or {}).get("number") or 0),
        "url": str((data or {}).get("html_url") or ""),
        "state": str((data or {}).get("state") or "open"),
        "draft": bool((data or {}).get("draft", True)),
    }


def commit_status(project: Dict[str, Any], sha: str) -> Dict[str, Any]:
    _, _, full_name, _ = _project_values(project)
    owner, name = _owner_name(full_name)
    data = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/commits/{quote(str(sha or ''))}/check-runs",
        token=_token(project),
    )
    runs = data.get("check_runs") if isinstance(data, dict) else []
    if not isinstance(runs, list):
        runs = []
    return {
        "total": len(runs),
        "checks": [
            {
                "name": str(item.get("name") or ""),
                "status": str(item.get("status") or ""),
                "conclusion": str(item.get("conclusion") or ""),
                "url": str(item.get("html_url") or ""),
            }
            for item in runs[:30]
            if isinstance(item, dict)
        ],
    }
