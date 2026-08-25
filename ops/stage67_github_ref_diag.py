from __future__ import annotations

import json
from urllib.parse import quote

from ops import stage67_live_acceptance_operator as op
from services.velia_admin_security_service import configured_admin_id
from services import velia_developer_project_service as project_service
from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service

BRANCH = "velia/20260825-1914-stage-6-7-reviewer-remediation-canary-unsa-cad35a"
EXPECTED_HEAD = "4e5d36a476c94150a7200baf62e65e72af8e0292"

admin_id = int(configured_admin_id() or 0)
if admin_id <= 0:
    raise SystemExit("admin_id_missing")
project = op.get_admin_project(project_service, admin_id)
permissions = write_service.require_write_permissions(project)
head = write_service.branch_head(project, BRANCH)
print("STAGE67_REF_DIAG preflight=" + json.dumps({
    "branch": BRANCH,
    "expected_head": EXPECTED_HEAD,
    "observed_head": str(head.get("sha") or ""),
    "permissions": permissions,
}, sort_keys=True), flush=True)
if str(head.get("sha") or "").lower() != EXPECTED_HEAD:
    raise SystemExit("unexpected_branch_head")

_, _, full_name, _ = write_service._project_values(project)
owner, name = write_service._owner_name(full_name)
token = write_service._token(project)
try:
    rate = github_service._request("GET", "/rate_limit", token=token)
    core = ((rate or {}).get("resources") or {}).get("core") or {}
    print("STAGE67_REF_DIAG rate=" + json.dumps({
        "limit": core.get("limit"),
        "remaining": core.get("remaining"),
        "reset": core.get("reset"),
        "used": core.get("used"),
    }, sort_keys=True), flush=True)
except github_service.DeveloperGithubError as exc:
    print("STAGE67_REF_DIAG rate_error=" + json.dumps({
        "code": exc.code, "status": exc.status, "detail": exc.detail,
    }, sort_keys=True), flush=True)

try:
    result = github_service._request(
        "PATCH",
        f"/repos/{quote(owner)}/{quote(name)}/git/refs/heads/{quote(BRANCH, safe='')}",
        token=token,
        body={"sha": EXPECTED_HEAD, "force": False},
    )
    obj = (result or {}).get("object") if isinstance(result, dict) else {}
    print("STAGE67_REF_DIAG noop_patch=" + json.dumps({
        "ok": True,
        "returned_sha": str((obj or {}).get("sha") or ""),
    }, sort_keys=True), flush=True)
except github_service.DeveloperGithubError as exc:
    print("STAGE67_REF_DIAG noop_patch=" + json.dumps({
        "ok": False,
        "code": exc.code,
        "status": exc.status,
        "detail": exc.detail,
    }, sort_keys=True), flush=True)
