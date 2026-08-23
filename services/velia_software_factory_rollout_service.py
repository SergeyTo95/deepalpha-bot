from __future__ import annotations

import os
from typing import Set


ROLLOUT_OFF = "off"
ROLLOUT_DRY_RUN = "dry_run"
ROLLOUT_LIVE = "live"
_VALID_MODES = {ROLLOUT_OFF, ROLLOUT_DRY_RUN, ROLLOUT_LIVE}


def rollout_mode() -> str:
    raw = str(os.getenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", ROLLOUT_OFF) or ROLLOUT_OFF).strip().lower()
    return raw if raw in _VALID_MODES else ROLLOUT_OFF


def allowed_user_ids() -> Set[int]:
    """Parse the Factory allowlist using the same comma-separated ID convention as VELIA chat beta rollout."""
    result: Set[int] = set()
    for part in str(os.getenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "") or "").split(","):
        try:
            value = int(part.strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            result.add(value)
    return result


def user_allowed(user_id: int) -> bool:
    """Fail closed: an empty allowlist never means everyone for Software Factory."""
    allowlist = allowed_user_ids()
    return bool(allowlist) and int(user_id) in allowlist


def intake_allowed(user_id: int) -> bool:
    return rollout_mode() in {ROLLOUT_DRY_RUN, ROLLOUT_LIVE} and user_allowed(int(user_id))


def dry_run_enabled(user_id: int) -> bool:
    return rollout_mode() == ROLLOUT_DRY_RUN and user_allowed(int(user_id))


def live_execution_allowed(user_id: int) -> bool:
    return rollout_mode() == ROLLOUT_LIVE and user_allowed(int(user_id))


def supervisor_allowed() -> bool:
    """Supervisor can exist only in live mode; dry-run can never dispatch repository work."""
    return rollout_mode() == ROLLOUT_LIVE and bool(allowed_user_ids())


def public_status(user_id: int) -> dict:
    mode = rollout_mode()
    eligible = user_allowed(int(user_id))
    return {
        "mode": mode,
        "eligible": eligible,
        "dry_run": bool(mode == ROLLOUT_DRY_RUN and eligible),
        "live_execution": bool(mode == ROLLOUT_LIVE and eligible),
        "supervisor_allowed": bool(supervisor_allowed()),
    }
