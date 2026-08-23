from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

from services import velia_agent_coding_autopilot_review_service as review_service
from services import velia_agent_coding_autopilot_review_store as review_store
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_coding_service as coding_service

logger = logging.getLogger(__name__)
_PATCH_INSTALLED = False


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def max_polls_per_tick() -> int:
    return _env_int(
        "VELIA_DEVELOPER_AUTOPILOT_REVIEW_MAX_POLLS_PER_TICK",
        3,
        1,
        10,
    )


def _is_no_action_result(result: Dict[str, Any]) -> bool:
    return (
        str(result.get("status") or "") == "ready_for_review"
        and "review_events_observed" in result
        and not result.get("review_poll_error")
    )


def _run_once_with_review_fairness(
    original_run_once: Callable[[], List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    if not autopilot.worker_enabled() or not coding_service.coding_enabled():
        return []
    if not review_service.review_loop_enabled():
        return original_run_once()

    scanned = 0
    for _ in range(max_polls_per_tick()):
        processed: Optional[Dict[str, Any]] = review_service.process_review_once()
        if processed is None:
            break
        if processed.get("review_poll_error"):
            logger.warning(
                "VELIA_AUTOPILOT_REVIEW_FAIRNESS_FALLTHROUGH polls=%s reason=github_deferred code=%s",
                scanned + 1,
                str(processed.get("review_poll_error") or "")[:120],
            )
            break
        if _is_no_action_result(processed):
            scanned += 1
            continue
        return [processed]

    if scanned:
        logger.info(
            "VELIA_AUTOPILOT_REVIEW_FAIRNESS_FALLTHROUGH polls=%s max_polls=%s reason=no_action",
            scanned,
            max_polls_per_tick(),
        )
    return original_run_once()


def install_review_loop() -> None:
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    review_store.ensure_review_tables()
    original_run_once = autopilot.run_autopilot_once

    def run_once_with_review_fairness() -> List[Dict[str, Any]]:
        return _run_once_with_review_fairness(original_run_once)

    autopilot.run_autopilot_once = run_once_with_review_fairness
    _PATCH_INSTALLED = True
    logger.info(
        "VELIA_AUTOPILOT_REVIEW_FAIRNESS_INSTALLED enabled=%s max_polls_per_tick=%s",
        review_service.review_loop_enabled(),
        max_polls_per_tick(),
    )
