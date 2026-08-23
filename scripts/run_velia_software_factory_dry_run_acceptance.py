from __future__ import annotations

import json
import sys

from services import velia_software_factory_dry_run_acceptance_service as acceptance


def _safe_summary(result: dict) -> dict:
    return {
        "status": str(result.get("status") or "failed"),
        "passed": bool(result.get("passed")),
        "repository_full_name": str(result.get("repository_full_name") or acceptance.acceptance_repository())[:240],
        "code_ref": str(result.get("code_ref") or acceptance.code_ref())[:40],
        "run_id": str(result.get("run_id") or "")[:80],
        "initial_state": str(result.get("initial_state") or ""),
        "initial_clarification_reasons": list(result.get("initial_clarification_reasons") or [])[:10],
        "safe_scope_auto_approved": bool(result.get("safe_scope_auto_approved")),
        "final_state": str(result.get("final_state") or ""),
        "dry_run": bool(result.get("dry_run")),
        "execution_blocked": bool(result.get("execution_blocked")),
        "team_plan_task_count": int(result.get("team_plan_task_count") or 0),
        "team_roles": [str(item) for item in result.get("team_roles") or []][:20],
        "autopilot_missions_unchanged": bool(result.get("autopilot_missions_unchanged")),
        "repository_write_performed": bool(result.get("repository_write_performed")),
        "autopilot_task_dispatched": bool(result.get("autopilot_task_dispatched")),
        "merge_performed": bool(result.get("merge_performed")),
        "deployment_triggered": bool(result.get("deployment_triggered")),
        "blocker_code": str(result.get("blocker_code") or "")[:160],
        "failure_reasons": [str(item) for item in result.get("failure_reasons") or []][:20],
        "reused": bool(result.get("reused")),
    }


def main() -> int:
    try:
        result = acceptance.run_acceptance()
        summary = _safe_summary(dict(result))
        print("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_RESULT " + json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["status"] == "passed" and summary["passed"] else 2
    except Exception as exc:
        summary = {
            "status": "failed",
            "passed": False,
            "repository_full_name": acceptance.acceptance_repository()[:240],
            "code_ref": acceptance.code_ref()[:40],
            "error": str(getattr(exc, "code", exc.__class__.__name__))[:160],
        }
        print("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_RESULT " + json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
