from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Mapping, Optional

from db.database import get_connection
from services import velia_agent_coding_autopilot_ci_service as ci_service
from services import velia_agent_coding_autopilot_review_github_service as review_github
from services import velia_agent_coding_autopilot_review_store as review_store
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_coding_service as coding_service
from services import velia_developer_fast_path_service as cost_service
from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service

logger = logging.getLogger(__name__)
_PATCH_INSTALLED = False
_TRANSIENT_GITHUB_CODES = {
    "github_unavailable",
    "github_rate_limited",
    "github_api_error",
    "github_invalid_response",
}


class CodingAutopilotReviewError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "", status: int = 400) -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]
        self.status = int(status)


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


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def review_loop_enabled() -> bool:
    return ci_service.ci_watch_enabled() and _env_bool(
        "VELIA_DEVELOPER_AUTOPILOT_REVIEW_ENABLED", False
    )


def review_status() -> Dict[str, Any]:
    return {
        "enabled": review_loop_enabled(),
        "max_actions": _env_int("VELIA_DEVELOPER_AUTOPILOT_REVIEW_MAX_ACTIONS", 2, 0, 2),
        "request_changes_only": True,
        "ordinary_comments_change_code": False,
        "auto_resolve_threads": False,
        "auto_merge": False,
        "deployment": False,
        "approved_plan_files_only": True,
    }


def _action_evidence(action: Mapping[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    key = str(action.get("review_key") or "")
    for event in events:
        if str(event.get("review_key") or "") == key:
            return dict(event)
    return {
        "review_key": key,
        "review_id": int(action.get("review_id") or 0),
        "kind": str(action.get("kind") or "review"),
        "state": str(action.get("state") or ""),
        "author_login": str(action.get("author_login") or ""),
        "body": str(action.get("body") or ""),
        "comments": list(action.get("comments") or []),
    }


def _validate_review_scope(evidence: Mapping[str, Any], allowed_files: List[str]) -> None:
    allowed = set(allowed_files)
    comments = evidence.get("comments") if isinstance(evidence.get("comments"), list) else []
    for item in comments:
        if not isinstance(item, Mapping):
            continue
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        try:
            path = github_service.validate_path(raw_path)
        except Exception as exc:
            raise CodingAutopilotReviewError(
                "velia_coding_autopilot_review_path_invalid", detail=raw_path, status=409
            ) from exc
        if path not in allowed:
            raise CodingAutopilotReviewError(
                "velia_coding_autopilot_review_outside_approved_scope",
                detail=path,
                status=409,
            )


def _review_prompt(
    *,
    project: Mapping[str, Any],
    run: Mapping[str, Any],
    job: Mapping[str, Any],
    evidence: Mapping[str, Any],
    files: List[str],
    context: str,
    action_number: int,
) -> str:
    return f"""You are the bounded review repair stage of VELIA Coding Autopilot.
Repository: {project.get('repository_full_name')}
Base branch: {job.get('base_branch')}
Existing work branch: {run.get('work_branch')}
Existing draft PR: #{run.get('pull_request_number')}
Original goal: {job.get('goal')}
Review action: {action_number}/2
Allowed files from the original approved plan: {json.dumps(files, ensure_ascii=False)}

Explicit GitHub REQUEST_CHANGES evidence:
{ci_service._json(evidence, 18000)}

Current source excerpts from the existing work branch:
{context[:28000]}

Return ONLY one compact JSON object:
{{
  "summary": "specific requested change and implementation",
  "operations": [
    {{"op":"replace","path":"allowed/path.py","old":"exact unique current snippet","new":"replacement"}},
    {{"op":"create","path":"allowed/new_file.py","content":"complete file"}},
    {{"op":"delete","path":"allowed/obsolete.py"}}
  ],
  "checks": ["checks expected to pass after this change"]
}}
Rules:
- Modify only listed files from the original approved plan.
- Address only the supplied REQUEST_CHANGES evidence.
- Do not reinterpret ordinary comments or questions as permission to write.
- Do not change workflows, secrets, credentials, auth, billing, migrations, infrastructure or deployment configuration.
- Do not create a new branch or PR. Do not merge, deploy, approve or resolve review threads.
- Use exact unique replacements for existing files.
- If evidence is insufficient, return {{"summary":"insufficient evidence","operations":[],"checks":[]}}.
- No markdown outside JSON.
"""


def _add_run_cost(run_id: str, amount: float) -> None:
    value = max(0.0, float(amount or 0.0))
    if value <= 0:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_developer_autopilot_runs "
            "SET estimated_cost_usd=estimated_cost_usd+%s,updated_at=NOW() WHERE run_id=%s",
            (value, str(run_id)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _clear_finished_at(run_id: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_developer_autopilot_runs SET finished_at=NULL WHERE run_id=%s",
            (str(run_id),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _append_review_result(run: Mapping[str, Any], **values: Any) -> Dict[str, Any]:
    result = ci_service._run_result(run)
    review = result.get("review") if isinstance(result.get("review"), dict) else {}
    result["review"] = {**review, **values}
    return result


def _execute_review_repair(
    run: Mapping[str, Any],
    action: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> Dict[str, Any]:
    project, mission = ci_service._project_and_mission(run)
    job = ci_service._coding_job(run)
    allowed_files = ci_service._allowed_repair_files(job, mission)
    if not allowed_files:
        raise CodingAutopilotReviewError("velia_coding_autopilot_review_files_empty", status=409)
    _validate_review_scope(evidence, allowed_files)

    current_attempt = ci_service._current_attempt(str(run.get("run_id") or ""))
    if not current_attempt or str(current_attempt.get("status") or "") != "success":
        raise CodingAutopilotReviewError(
            "velia_coding_autopilot_review_requires_green_ci", status=409
        )
    max_repairs = ci_service._env_int(
        "VELIA_DEVELOPER_AUTOPILOT_CI_MAX_REPAIRS", 2, 0, 2
    )
    next_attempt_number = int(current_attempt.get("attempt_number") or 0) + 1
    if next_attempt_number > max_repairs:
        raise CodingAutopilotReviewError(
            "velia_coding_autopilot_review_repairs_exhausted", status=409
        )
    max_actions = _env_int("VELIA_DEVELOPER_AUTOPILOT_REVIEW_MAX_ACTIONS", 2, 0, 2)
    action_number = review_store.addressed_count(str(run.get("run_id") or "")) + 1
    if action_number > max_actions:
        raise CodingAutopilotReviewError(
            "velia_coding_autopilot_review_actions_exhausted", status=409
        )

    branch = str(run.get("work_branch") or "")
    before = write_service.branch_head(project, branch)
    if str(before.get("sha") or "") != str(current_attempt.get("head_sha") or ""):
        raise CodingAutopilotReviewError(
            "velia_coding_autopilot_branch_head_changed", status=409
        )
    synthetic_step = {
        "files": allowed_files,
        "objective": "Address only the explicit GitHub REQUEST_CHANGES review.",
        "checks": ["Run exact-head CI after the review repair commit."],
    }
    context, states = coding_service._step_context(
        project,
        branch,
        synthetic_step,
        f"{job.get('goal')}\nReview changes: {ci_service._json(evidence, 16000)}",
    )
    prompt = _review_prompt(
        project=project,
        run=run,
        job=job,
        evidence=evidence,
        files=allowed_files,
        context=context,
        action_number=action_number,
    )
    max_tokens = _env_int(
        "VELIA_DEVELOPER_AUTOPILOT_REVIEW_OUTPUT_TOKENS", 2200, 800, 2800
    )
    budget = _env_float(
        "VELIA_DEVELOPER_AUTOPILOT_REVIEW_MAX_COST_USD", 0.06, 0.01, 0.12
    )
    if cost_service._estimate_cost(prompt, max_tokens) > budget:
        raise CodingAutopilotReviewError(
            "velia_coding_autopilot_review_cost_limit", status=402
        )

    review_store.set_review_action(action, "repairing")
    total_cost = 0.0
    raw_response = ""
    payload: Dict[str, Any] = {}
    operations: List[Dict[str, Any]] = []
    current_prompt = prompt
    for model_attempt in range(2):
        result = coding_service._model_call(
            prompt=current_prompt,
            feature="velia_developer_autopilot_review_repair",
            request_id=(
                f"autopilot-review:{run.get('run_id')}:"
                f"{action.get('review_key')}:{model_attempt + 1}"
            ),
            user_id=int(run.get("user_id") or 0),
            max_tokens=max_tokens if model_attempt == 0 else 1200,
            timeout=ci_service._env_int(
                "VELIA_DEVELOPER_CODING_MODEL_TIMEOUT_SECONDS", 100, 20, 120
            ),
        )
        total_cost += float(result.get("estimated_cost_usd") or 0.0)
        if total_cost > budget:
            raise CodingAutopilotReviewError(
                "velia_coding_autopilot_review_cost_limit", status=402
            )
        raw_response = str(result.get("text") or "")
        try:
            payload = coding_service._extract_json(raw_response)
            raw_operations = payload.get("operations") if isinstance(payload, dict) else []
            if not isinstance(raw_operations, list) or not raw_operations:
                raise CodingAutopilotReviewError(
                    "velia_coding_autopilot_review_evidence_insufficient", status=409
                )
            operations, _ = coding_service._apply_patch_payload(
                payload,
                allowed_files=allowed_files,
                states=states,
            )
            break
        except (coding_service.DeveloperCodingError, CodingAutopilotReviewError) as exc:
            if model_attempt >= 1:
                raise
            current_prompt = coding_service._repair_prompt(
                prompt,
                raw_response,
                coding_service.DeveloperCodingError(
                    str(getattr(exc, "code", "velia_coding_autopilot_review_patch_invalid")),
                    status=int(getattr(exc, "status", 409)),
                    detail=str(getattr(exc, "detail", "")),
                ),
            )

    current = write_service.branch_head(project, branch)
    if str(current.get("sha") or "") != str(before.get("sha") or ""):
        raise CodingAutopilotReviewError(
            "velia_coding_autopilot_branch_head_changed", status=409
        )
    committed = write_service.commit_operations(
        project,
        branch=branch,
        operations=operations,
        message=(
            f"VELIA review repair {action_number}: "
            f"{str(action.get('author_login') or 'requested changes')[:100]}"
        ),
    )
    commit_sha = str(committed.get("commit_sha") or "")
    repair = {
        "summary": str(payload.get("summary") or "Address requested changes.")[:2000],
        "checks": [str(item)[:300] for item in (payload.get("checks") or [])][:12],
        "files": list(committed.get("files") or []),
        "commit_sha": commit_sha,
        "estimated_cost_usd": total_cost,
    }
    review_store.set_review_action(
        action,
        "addressed",
        repair=repair,
        commit_sha=commit_sha,
    )
    _add_run_cost(str(run.get("run_id") or ""), total_cost)
    ci_service._create_attempt(run, commit_sha, next_attempt_number)
    result_payload = _append_review_result(
        run,
        last_review_key=str(action.get("review_key") or ""),
        last_review_commit=commit_sha,
        addressed_count=action_number,
        awaiting_ci=True,
    )
    ci_service._set_run_state(run, "waiting_ci", result=result_payload)
    _clear_finished_at(str(run.get("run_id") or ""))
    reply = review_github.reply_after_commit(
        project,
        int(run.get("pull_request_number") or 0),
        evidence,
        commit_sha,
    )
    autopilot._record_event(
        run,
        "review_changes_addressed",
        {
            "review_key": str(action.get("review_key") or ""),
            "commit_sha": commit_sha,
            "files": repair["files"],
            "reply": reply,
        },
    )
    return {
        **dict(run),
        "status": "waiting_ci",
        "result": result_payload,
        "review_action": {**dict(action), "status": "addressed", "repair": repair},
        "reply": reply,
    }


def _block_run(
    run: Mapping[str, Any],
    action: Optional[Mapping[str, Any]],
    code: str,
    detail: str = "",
) -> Dict[str, Any]:
    if action:
        review_store.set_review_action(action, "blocked", error_code=code)
    result = _append_review_result(
        run,
        blocked_review_key=str((action or {}).get("review_key") or ""),
        error_code=code,
        detail=str(detail or "")[:500],
    )
    ci_service._set_run_state(run, "blocked", result=result, error_code=code, finished=True)
    autopilot._record_event(
        run,
        "review_loop_blocked",
        {"error_code": code, "detail": str(detail or "")[:500]},
    )
    return {**dict(run), "status": "blocked", "error_code": code, "result": result}


def process_review_once() -> Optional[Dict[str, Any]]:
    if not review_loop_enabled():
        return None
    run = review_store.claim_ready_run()
    if not run:
        return None
    action: Optional[Dict[str, Any]] = None
    try:
        project, _mission = ci_service._project_and_mission(run)
        events = review_github.list_review_evidence(
            project,
            int(run.get("pull_request_number") or 0),
        )
        review_store.observe_review_events(run, events)
        action = review_store.next_actionable(str(run.get("run_id") or ""))
        if not action:
            review_store.defer_review_poll(str(run.get("run_id") or ""))
            return {
                **dict(run),
                "status": "ready_for_review",
                "review_events_observed": len(events),
            }
        evidence = _action_evidence(action, events)
        if not str(evidence.get("body") or "").strip() and not list(evidence.get("comments") or []):
            raise CodingAutopilotReviewError(
                "velia_coding_autopilot_review_evidence_insufficient", status=409
            )
        return _execute_review_repair(run, action, evidence)
    except review_github.CodingAutopilotReviewGithubError as exc:
        if exc.code in _TRANSIENT_GITHUB_CODES:
            review_store.defer_review_poll(str(run.get("run_id") or ""))
            logger.warning(
                "VELIA_AUTOPILOT_REVIEW_POLL_DEFERRED run_id=%s code=%s",
                run.get("run_id"),
                exc.code,
            )
            return {**dict(run), "status": "ready_for_review", "review_poll_error": exc.code}
        return _block_run(run, action, exc.code, exc.detail)
    except Exception as exc:
        code = str(getattr(exc, "code", "velia_coding_autopilot_review_failed"))[:120]
        detail = str(getattr(exc, "detail", exc.__class__.__name__))[:500]
        logger.exception("VELIA_AUTOPILOT_REVIEW_FAILED run_id=%s code=%s", run.get("run_id"), code)
        return _block_run(run, action, code, detail)


def install_review_loop() -> None:
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    review_store.ensure_review_tables()
    original_run_once = autopilot.run_autopilot_once

    def run_once_with_review() -> List[Dict[str, Any]]:
        if not autopilot.worker_enabled() or not coding_service.coding_enabled():
            return []
        if review_loop_enabled():
            processed = process_review_once()
            if processed is not None:
                return [processed]
        return original_run_once()

    autopilot.run_autopilot_once = run_once_with_review
    _PATCH_INSTALLED = True
