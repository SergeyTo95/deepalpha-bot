from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Mapping, Sequence

from services import velia_agent_coding_autopilot_ci_service as ci_service
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_coding_service as coding_service
from services import velia_developer_fast_path_service as cost_service
from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service


class CodingAutopilotIntegrationRepairError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "", status: int = 400) -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]
        self.status = int(status)


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _bounded_roots(values: Sequence[Any]) -> List[str]:
    result: List[str] = []
    for raw in list(values or [])[:20]:
        try:
            path = github_service.validate_path(str(raw or "").strip())
        except Exception as exc:
            raise CodingAutopilotIntegrationRepairError(
                "velia_coding_autopilot_integration_repair_path_invalid",
                detail=str(raw or "")[:500],
                status=409,
            ) from exc
        if path and path not in result:
            result.append(path)
    return result


def _integration_files(
    job: Mapping[str, Any],
    mission: Mapping[str, Any],
    scope_roots: Sequence[Any],
) -> List[str]:
    approved_plan_files = ci_service._allowed_repair_files(job, mission)
    roots = _bounded_roots(scope_roots)
    if not roots:
        raise CodingAutopilotIntegrationRepairError(
            "velia_coding_autopilot_integration_repair_scope_empty", status=409
        )
    result = [
        path
        for path in approved_plan_files
        if any(_within(path, root) for root in roots)
    ]
    if not result:
        raise CodingAutopilotIntegrationRepairError(
            "velia_coding_autopilot_integration_repair_outside_original_plan",
            detail=",".join(roots)[:500],
            status=409,
        )
    return result[:12]


def _prompt(
    *,
    project: Mapping[str, Any],
    run: Mapping[str, Any],
    job: Mapping[str, Any],
    evidence: Mapping[str, Any],
    files: Sequence[str],
    context: str,
    action_number: int,
) -> str:
    return f"""You are the bounded cross-repository integration repair stage of VELIA Coding Autopilot.
Repository: {project.get('repository_full_name')}
Base branch: {job.get('base_branch')}
Existing work branch: {run.get('work_branch')}
Existing draft PR: #{run.get('pull_request_number')}
Original goal: {job.get('goal')}
Shared repair/CI action: {action_number}/2
Allowed files from the original approved Coding Agent plan and failed integration contract: {json.dumps(list(files), ensure_ascii=False)}

Failed cross-repository integration evidence:
{ci_service._json(evidence, 18000)}

Current source excerpts from the existing work branch:
{context[:28000]}

Return ONLY one compact JSON object:
{{
  "summary": "specific compatibility root cause and repair",
  "operations": [
    {{"op":"replace","path":"allowed/path.py","old":"exact unique current snippet","new":"replacement"}},
    {{"op":"create","path":"allowed/new_file.py","content":"complete file"}},
    {{"op":"delete","path":"allowed/obsolete.py"}}
  ],
  "checks": ["checks expected to pass after this integration repair"]
}}
Rules:
- Modify only the listed files. They are the intersection of the original approved plan and the failed integration contract.
- Fix only the supplied cross-repository compatibility failure. Preserve unrelated behavior.
- Never change another repository from this repair action.
- Do not change workflows, secrets, credentials, auth, billing, migrations, infrastructure or deployment configuration.
- Do not create a branch or pull request. Reuse the existing work branch and draft PR only.
- Do not merge, deploy, approve or resolve review threads.
- Use exact unique replacements for existing files.
- If the evidence does not justify a safe change inside the listed files, return {{"summary":"insufficient evidence","operations":[],"checks":[]}}.
- No markdown outside JSON.
"""


def repair_existing_run(
    user_id: int,
    run_id: str,
    *,
    evidence: Mapping[str, Any],
    scope_roots: Sequence[Any],
    repair_key: str,
) -> Dict[str, Any]:
    """Commit one bounded repair to the existing Autopilot branch/PR and restart exact-head CI.

    This adapter deliberately owns no branch/PR creation primitive. It may only
    advance a previously green, review-ready Autopilot run by one existing CI
    attempt, so CI repairs, review repairs and integration repairs share the same
    hard maximum of two post-initial commits.
    """
    if not ci_service.ci_watch_enabled() or not ci_service.ci_repair_enabled():
        raise CodingAutopilotIntegrationRepairError(
            "velia_coding_autopilot_integration_repair_ci_disabled", status=503
        )
    run = autopilot.get_run(int(user_id), str(run_id))
    if str(run.get("status") or "") != "ready_for_review":
        raise CodingAutopilotIntegrationRepairError(
            "velia_coding_autopilot_integration_repair_requires_review_ready", status=409
        )

    current_attempt = ci_service._current_attempt(str(run.get("run_id") or ""))
    if not current_attempt or str(current_attempt.get("status") or "") != "success":
        raise CodingAutopilotIntegrationRepairError(
            "velia_coding_autopilot_integration_repair_requires_green_ci", status=409
        )
    max_repairs = ci_service._env_int(
        "VELIA_DEVELOPER_AUTOPILOT_CI_MAX_REPAIRS", 2, 0, 2
    )
    next_attempt_number = int(current_attempt.get("attempt_number") or 0) + 1
    if next_attempt_number > max_repairs:
        raise CodingAutopilotIntegrationRepairError(
            "velia_coding_autopilot_integration_repairs_exhausted", status=409
        )

    project, mission = ci_service._project_and_mission(run)
    job = ci_service._coding_job(run)
    files = _integration_files(job, mission, scope_roots)
    branch = str(run.get("work_branch") or "")
    if not branch or int(run.get("pull_request_number") or 0) <= 0:
        raise CodingAutopilotIntegrationRepairError(
            "velia_coding_autopilot_integration_repair_existing_pr_required", status=409
        )

    before = write_service.branch_head(project, branch)
    if str(before.get("sha") or "") != str(current_attempt.get("head_sha") or ""):
        raise CodingAutopilotIntegrationRepairError(
            "velia_coding_autopilot_branch_head_changed", status=409
        )

    synthetic_step = {
        "files": files,
        "objective": "Repair only the failed cross-repository integration contract.",
        "checks": ["Run exact-head CI and then repeat workspace integration validation."],
    }
    context, states = coding_service._step_context(
        project,
        branch,
        synthetic_step,
        f"{job.get('goal')}\nIntegration repair evidence: {ci_service._json(evidence, 16000)}",
    )
    prompt = _prompt(
        project=project,
        run=run,
        job=job,
        evidence=evidence,
        files=files,
        context=context,
        action_number=next_attempt_number,
    )
    max_tokens = ci_service._env_int(
        "VELIA_DEVELOPER_AUTOPILOT_INTEGRATION_REPAIR_OUTPUT_TOKENS", 2200, 800, 2800
    )
    budget = ci_service._env_float(
        "VELIA_DEVELOPER_AUTOPILOT_INTEGRATION_REPAIR_MAX_COST_USD", 0.06, 0.01, 0.12
    )
    if cost_service._estimate_cost(prompt, max_tokens) > budget:
        raise CodingAutopilotIntegrationRepairError(
            "velia_coding_autopilot_integration_repair_cost_limit", status=402
        )

    key = hashlib.sha256(str(repair_key or run_id).encode("utf-8")).hexdigest()[:20]
    total_cost = 0.0
    payload: Dict[str, Any] = {}
    operations: List[Dict[str, Any]] = []
    raw_response = ""
    current_prompt = prompt
    for model_attempt in range(2):
        result = coding_service._model_call(
            prompt=current_prompt,
            feature="velia_developer_autopilot_integration_repair",
            request_id=f"autopilot-integration:{run.get('run_id')}:{key}:{model_attempt + 1}",
            user_id=int(run.get("user_id") or 0),
            max_tokens=max_tokens if model_attempt == 0 else 1200,
            timeout=ci_service._env_int(
                "VELIA_DEVELOPER_CODING_MODEL_TIMEOUT_SECONDS", 100, 20, 120
            ),
        )
        total_cost += float(result.get("estimated_cost_usd") or 0.0)
        if total_cost > budget:
            raise CodingAutopilotIntegrationRepairError(
                "velia_coding_autopilot_integration_repair_cost_limit", status=402
            )
        raw_response = str(result.get("text") or "")
        try:
            payload = coding_service._extract_json(raw_response)
            raw_operations = payload.get("operations") if isinstance(payload, dict) else []
            if not isinstance(raw_operations, list) or not raw_operations:
                raise CodingAutopilotIntegrationRepairError(
                    "velia_coding_autopilot_integration_repair_evidence_insufficient",
                    status=409,
                )
            operations, _ = coding_service._apply_patch_payload(
                payload,
                allowed_files=files,
                states=states,
            )
            break
        except (coding_service.DeveloperCodingError, CodingAutopilotIntegrationRepairError) as exc:
            if model_attempt >= 1:
                raise
            current_prompt = coding_service._repair_prompt(
                prompt,
                raw_response,
                coding_service.DeveloperCodingError(
                    str(getattr(exc, "code", "velia_coding_autopilot_integration_patch_invalid")),
                    status=int(getattr(exc, "status", 409)),
                    detail=str(getattr(exc, "detail", "")),
                ),
            )

    current = write_service.branch_head(project, branch)
    if str(current.get("sha") or "") != str(before.get("sha") or ""):
        raise CodingAutopilotIntegrationRepairError(
            "velia_coding_autopilot_branch_head_changed", status=409
        )
    committed = write_service.commit_operations(
        project,
        branch=branch,
        operations=operations,
        message=f"VELIA integration repair {next_attempt_number}: compatibility contract",
    )
    commit_sha = str(committed.get("commit_sha") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit_sha):
        raise CodingAutopilotIntegrationRepairError(
            "velia_coding_autopilot_integration_repair_commit_missing", status=502
        )

    next_attempt = ci_service._create_attempt(run, commit_sha, next_attempt_number)
    repair = {
        "repair_key": str(repair_key or "")[:160],
        "summary": str(payload.get("summary") or "Repair cross-repository compatibility.")[:2000],
        "checks": [str(item)[:300] for item in (payload.get("checks") or [])][:12],
        "files": list(committed.get("files") or []),
        "commit_sha": commit_sha,
        "estimated_cost_usd": total_cost,
        "pull_request_number": int(run.get("pull_request_number") or 0),
        "work_branch": branch,
    }
    run_result = ci_service._run_result(run)
    prior = run_result.get("integration_repairs") if isinstance(run_result.get("integration_repairs"), list) else []
    run_result["integration_repairs"] = [*prior, repair][-2:]
    run_result["estimated_cost_usd"] = float(run_result.get("estimated_cost_usd") or 0.0) + total_cost
    run_result = ci_service._append_ci_result(
        {**run, "result": run_result},
        status="pending",
        head_sha=commit_sha,
        attempt_number=int(next_attempt.get("attempt_number") or 0),
        checks=[],
        failure={},
        error_code=None,
    )
    ci_service._set_run_state(run, "waiting_ci", result=run_result)
    autopilot._record_event(
        run,
        "integration_repair_committed",
        {
            "repair_key": repair["repair_key"],
            "commit_sha": commit_sha,
            "files": repair["files"],
            "pull_request_number": repair["pull_request_number"],
        },
    )
    return {
        **dict(run),
        "status": "waiting_ci",
        "result": run_result,
        "repair": repair,
        "ci_attempt": next_attempt,
    }
