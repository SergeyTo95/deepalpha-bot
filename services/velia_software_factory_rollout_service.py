from __future__ import annotations

import os
from typing import Set

from services.velia_admin_security_service import configured_admin_id, is_admin_user


ROLLOUT_OFF = "off"
ROLLOUT_DRY_RUN = "dry_run"
ROLLOUT_LIVE = "live"
_VALID_MODES = {ROLLOUT_OFF, ROLLOUT_DRY_RUN, ROLLOUT_LIVE}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def rollout_mode() -> str:
    raw = str(os.getenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", ROLLOUT_OFF) or ROLLOUT_OFF).strip().lower()
    return raw if raw in _VALID_MODES else ROLLOUT_OFF


def admin_pilot_enabled() -> bool:
    """Extra pilot eligibility source. The rollout mode still gates all behavior."""
    return _env_bool("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", False)


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


def explicit_user_allowed(user_id: int) -> bool:
    allowlist = allowed_user_ids()
    return bool(allowlist) and int(user_id) in allowlist


def admin_pilot_user_allowed(user_id: int) -> bool:
    if not admin_pilot_enabled():
        return False
    try:
        candidate = int(user_id)
    except (TypeError, ValueError):
        return False
    return candidate > 0 and is_admin_user(candidate)


def user_allowed(user_id: int) -> bool:
    """Fail closed: neither an empty allowlist nor a disabled admin pilot means everyone."""
    return explicit_user_allowed(int(user_id)) or admin_pilot_user_allowed(int(user_id))


def intake_allowed(user_id: int) -> bool:
    return rollout_mode() in {ROLLOUT_DRY_RUN, ROLLOUT_LIVE} and user_allowed(int(user_id))


def dry_run_enabled(user_id: int) -> bool:
    return rollout_mode() == ROLLOUT_DRY_RUN and user_allowed(int(user_id))


def live_execution_allowed(user_id: int) -> bool:
    return rollout_mode() == ROLLOUT_LIVE and user_allowed(int(user_id))


def _admin_pilot_configured() -> bool:
    return admin_pilot_enabled() and configured_admin_id() > 0


def supervisor_allowed() -> bool:
    """Supervisor can exist only in live mode and only with an explicit eligibility source."""
    eligible_source_configured = bool(allowed_user_ids()) or _admin_pilot_configured()
    return rollout_mode() == ROLLOUT_LIVE and eligible_source_configured


def eligibility_source(user_id: int) -> str:
    if explicit_user_allowed(int(user_id)):
        return "explicit_allowlist"
    if admin_pilot_user_allowed(int(user_id)):
        return "admin_pilot"
    return "none"


def public_status(user_id: int) -> dict:
    mode = rollout_mode()
    eligible = user_allowed(int(user_id))
    return {
        "mode": mode,
        "eligible": eligible,
        "eligibility_source": eligibility_source(int(user_id)),
        "admin_pilot_enabled": admin_pilot_enabled(),
        "dry_run": bool(mode == ROLLOUT_DRY_RUN and eligible),
        "live_execution": bool(mode == ROLLOUT_LIVE and eligible),
        "supervisor_allowed": bool(supervisor_allowed()),
    }
