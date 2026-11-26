"""Open public repositories in Cloud Shell and launch their opted-in GitHub tests."""

from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import re
from urllib.parse import quote, urlencode
from uuid import UUID

from psycopg2.extras import RealDictCursor

from db.database import get_connection
from services import velia_developer_github_service as github
from services import velia_developer_project_service as projects


WORKFLOW = "velia-tests.yml"
WORKFLOW_PATH = f".github/workflows/{WORKFLOW}"
GITHUB_STATES = {"requested", "waiting", "pending", "queued", "in_progress", "completed"}
SHA = re.compile(r"[a-f0-9]{40}")


def _error(code, status):
    return projects.DeveloperProjectError(code, status=status)


def cloud_shell_url(repository, branch, workspace=None):
    if repository.get("private") is not False or repository.get("visibility") != "public":
        return None
    owner, name = github._validate_full_name(repository["full_name"])
    if {owner, name} & {".", ".."}:
        return None
    params = {
        "cloudshell_git_repo": f"https://github.com/{owner}/{name}",
        "cloudshell_git_branch": github.validate_branch(branch),
        "show": "ide",
    }
    if workspace:
        params["cloudshell_workspace"] = github.validate_path(workspace)
    return "https://shell.cloud.google.com/cloudshell/editor?" + urlencode(params)


def metadata(project):
    return github.repository_metadata(
        project["installation_id"], project["repository_id"], project["repository_full_name"],
    )


def _repository_request(project):
    owner, name = github._validate_full_name(project["repository_full_name"])
    if {owner, name} & {".", ".."}:
        raise _error("invalid_repository", 400)
    return f"/repos/{owner}/{name}", github._installation_token(project["installation_id"], [project["repository_id"]])


def _configured_workflow(project, repository, installation):
    if repository.get("archived"):
        return "project_tests_archived"
    path, token = _repository_request(project)
    branch = github.validate_branch(repository["default_branch"])
    try:
        file = github._request("GET", f"{path}/contents/{WORKFLOW_PATH}", token=token, params={"ref": branch})
    except github.DeveloperGithubError as exc:
        if exc.code == "github_not_found":
            return "project_tests_workflow_missing"
        raise
    if not isinstance(file, dict) or file.get("type") != "file" or file.get("path") != WORKFLOW_PATH:
        return "project_tests_workflow_missing"
    permissions = github.installation_details(project["installation_id"])
    if permissions.get("actions_permission") != "write":
        return "project_tests_actions_permission"
    login = installation.get("github_user_login") or ""
    actor_id = installation.get("github_user_id")
    if not actor_id or not re.fullmatch(r"[a-zA-Z0-9-]{1,39}", login):
        return "project_tests_reconnect"
    try:
        membership = github._request("GET", f"{path}/collaborators/{quote(login)}/permission", token=token)
    except github.DeveloperGithubError as exc:
        if exc.code in {"github_not_found", "github_forbidden"}:
            return "project_tests_write_required"
        raise
    actor = membership.get("user") if isinstance(membership, dict) else None
    if not isinstance(actor, dict) or actor.get("id") != actor_id or membership.get("permission") not in {"write", "admin"}:
        return "project_tests_write_required"
    return None


@contextmanager
def _cursor():
    with closing(get_connection()) as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cursor:
        yield cursor


def _public(run):
    if run is None:
        return None
    status = run["status"]
    if status == "dispatching" and run["created_at"] < datetime.now(timezone.utc) - timedelta(minutes=2):
        status = "unknown"
    return {
        "branch": run["branch"],
        "status": status,
        "id": run["github_run_id"],
        "conclusion": run["conclusion"],
        "head_sha": run["head_sha"],
    }


def _current(project, request_id=None):
    sql = "SELECT * FROM velia_project_test_launches WHERE project_id=%s"
    if request_id is not None:
        sql += " AND request_id=%s"
    with _cursor() as cursor:
        cursor.execute(sql + " ORDER BY created_at DESC LIMIT 1",
                       (project["id"], request_id) if request_id is not None else (project["id"],))
        return cursor.fetchone()


def _reserve(project, branch, request_id):
    with _cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                       (f"velia_project_test:{project['repository_id']}",))
        cursor.execute("SELECT * FROM velia_project_test_launches WHERE project_id=%s AND request_id=%s",
                       (project["id"], request_id))
        previous = cursor.fetchone()
        if previous:
            return previous, False

        cursor.execute("""
            SELECT COUNT(*) AS daily, COUNT(*) FILTER (WHERE created_at > now() - interval '2 minutes') AS recent
            FROM velia_project_test_launches WHERE repository_id=%s AND created_at > now() - interval '1 day'
        """, (project["repository_id"],))
        counts = cursor.fetchone()
        if counts["recent"] or counts["daily"] >= 10:
            raise _error("project_tests_limit", 429)
        cursor.execute("""
            INSERT INTO velia_project_test_launches (project_id, request_id, repository_id, branch, status)
            VALUES (%s, %s, %s, %s, 'dispatching') RETURNING *
        """, (project["id"], request_id, project["repository_id"], branch))
        row = cursor.fetchone()
        cursor.execute("""
            DELETE FROM velia_project_test_launches
            WHERE repository_id=%s AND created_at < now() - interval '30 days'
        """, (project["repository_id"],))
        return row, True


def _save(project, request_id, status, *, run_id=None, conclusion=None, head=None):
    with _cursor() as cursor:
        cursor.execute("""
            UPDATE velia_project_test_launches
            SET status=%s, github_run_id=COALESCE(%s, github_run_id), conclusion=%s, head_sha=%s
            WHERE project_id=%s AND request_id=%s AND status <> 'completed'
            RETURNING *
        """, (status, run_id, conclusion, head, project["id"], request_id))
        return cursor.fetchone()


def _refresh(project, run):
    if not run or run["status"] == "completed":
        return _public(run)
    run_id = run["github_run_id"]
    if not run_id:
        return _public(run)
    path, token = _repository_request(project)
    try:
        result = github._request("GET", f"{path}/actions/runs/{run_id}", token=token)
    except github.DeveloperGithubError as exc:
        if exc.code == "github_not_found":
            return _public(run)
        raise
    if not isinstance(result, dict) or result.get("id") != run_id or result.get("event") != "workflow_dispatch":
        raise _error("project_tests_invalid_github_response", 502)
    status = result.get("status")
    if status not in GITHUB_STATES:
        return _public(run)
    head = result.get("head_sha") or ""
    conclusion = result.get("conclusion")
    latest = _save(project, run["request_id"], status, conclusion=conclusion, head=head if SHA.fullmatch(head) else None)
    return _public(latest or _current(project, run["request_id"]))


def overview(project, installation):
    repository = metadata(project)
    reason = _configured_workflow(project, repository, installation)
    return {
        "branch": repository["default_branch"],
        "reason": reason,
        # Completion lives on GitHub. On reopening the card the UI can refresh the saved run.
        "run": _public(_current(project)),
    }


def latest(project):
    return _refresh(project, _current(project))


def dispatch(project, installation, data):
    if not isinstance(data, dict) or set(data) != {"request_id"} or not isinstance(data["request_id"], str):
        raise _error("project_tests_invalid_request", 400)
    try:
        request_id = str(UUID(data["request_id"], version=4))
    except ValueError:
        raise _error("project_tests_invalid_request", 400) from None
    if request_id != data["request_id"]:
        raise _error("project_tests_invalid_request", 400)
    previous = _current(project, request_id)
    if previous:
        return _refresh(project, previous)
    repository = metadata(project)
    reason = _configured_workflow(project, repository, installation)
    if reason:
        raise _error(reason, 403)
    branch = github.validate_branch(repository["default_branch"])
    path, token = _repository_request(project)
    run, reserved = _reserve(project, branch, request_id)
    if not reserved:
        return _public(run)
    try:
        response = github._request(
            "POST", f"{path}/actions/workflows/{WORKFLOW}/dispatches", token=token,
            body={"ref": branch}, expected=(200, 204), api_version="2026-03-10",
        )
    except github.DeveloperGithubError as exc:
        if exc.code in {"github_forbidden", "github_not_found", "github_invalid_request", "github_rate_limited"}:
            _save(project, request_id, "failed")
            raise
        return _public(_save(project, request_id, "unknown"))
    run_id = response.get("workflow_run_id") if isinstance(response, dict) else None
    if type(run_id) is not int or not 0 < run_id < 2**53:
        return _public(_save(project, request_id, "unknown"))
    return _public(_save(project, request_id, "queued", run_id=run_id))
