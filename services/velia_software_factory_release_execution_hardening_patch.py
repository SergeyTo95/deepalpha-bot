from __future__ import annotations

from typing import Any

_INSTALLED = False


def install(release_execution_module: Any, execution_module: Any) -> None:
    global _INSTALLED
    if getattr(release_execution_module, "_stage53_hardening_installed", False):
        return

    # A project metadata change after an uncertain network outcome does not prove
    # that GitHub did not merge the PR. Keep it in the uncertain/reconcile path.
    confirmed = getattr(release_execution_module, "_CONFIRMED_RECONCILE_FAILURES", set())
    try:
        confirmed.discard("velia_factory_release_repository_identity_changed")
    except Exception:
        pass

    def record_uncertain(execution_id: str, user_id: int, merged_count: int, detail: str):
        release_execution_module._set_execution(
            str(execution_id),
            int(user_id),
            status="running",
            merged_count=int(merged_count),
            blocker_code="velia_factory_release_merge_outcome_uncertain",
            blocker_detail=str(detail or "")[:1000],
            result={
                "reconciliation_required": True,
                "safe_to_retry_execution": True,
                "deployment_started": False,
            },
        )
        release_execution_module._event(
            str(execution_id),
            int(user_id),
            "release_item.merge_outcome_uncertain",
            {"merged_count": int(merged_count), "detail": str(detail or "")[:500]},
        )
        return release_execution_module.get_execution(
            execution_module,
            int(user_id),
            str(execution_id),
        )

    release_execution_module._record_uncertain = record_uncertain
    release_execution_module._stage53_hardening_installed = True
    _INSTALLED = True
