from __future__ import annotations

import os
import re
from typing import Dict, Iterable, Set

from services.velia_admin_security_service import configured_admin_id


ROLLOUT_OFF = "off"
ROLLOUT_DRY_RUN = "dry_run"
ROLLOUT_LIVE = "live"
_VALID_MODES = {ROLLOUT_OFF, ROLLOUT_DRY_RUN, ROLLOUT_LIVE}
_ID_SPLIT_RE = re.compile(r"[\s,;]+")
_ADMIN_PILOT_SOURCE_ENV = {
    "live_owner": "LIVE_OWNER_USER_IDS",
    "jarvis_founder": "JARVIS_FOUNDER_IDS",
    "chat_beta": "VELIA_CHAT_BETA_USER_IDS",
    "mobile_debug": "VELIA_MOBILE_DEBUG_USER_IDS",
}

_PLAN_FLAGS = (
    "VELIA_DEVELOPER_ENABLED",
    "VELIA_SOFTWARE_FACTORY_ENABLED",
    "VELIA_SOFTWARE_FACTORY_TEAM_ENABLED",
    "VELIA_SOFTWARE_FACTORY_AUTONOMY_ENABLED",
)
_MULTI_REPO_PLAN_FLAGS = _PLAN_FLAGS + (
    "VELIA_SOFTWARE_FACTORY_WORKSPACE_CHAT_ENABLED",
)
_BUILD_REVIEW_FLAGS = _MULTI_REPO_PLAN_FLAGS + (
    "VELIA_SOFTWARE_FACTORY_SUPERVISOR_ENABLED",
    "VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_INTEGRATION_VALIDATOR_ENABLED",
    "VELIA_DEVELOPER_AUTOPILOT_ENABLED",
    "VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED",
    "VELIA_DEVELOPER_WRITE_ENABLED",
)
_RELEASE_FLAGS = _BUILD_REVIEW_FLAGS + (
    "VELIA_DEVELOPER_AUTOPILOT_MERGE_POLICY_ENABLED",
    "VELIA_SOFTWARE_FACTORY_DELIVERY_GATE_ENABLED",
    "VELIA_SOFTWARE_FACTORY_DELIVERY_APPROVAL_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_PREFLIGHT_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_VERIFICATION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_DEPLOYMENT_OBSERVER_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_COMPLETION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_PASSPORT_ENABLED",
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _parse_ids(raw: object) -> Set[int]:
    result: Set[int] = set()
    for part in _ID_SPLIT_RE.split(str(raw or "").strip()):
        if not part:
            continue
        try:
            value = int(part)
        except (TypeError, ValueError):
            continue
        if value > 0:
            result.add(value)
        if len(result) >= 64:
            break
    return result


def rollout_mode() -> str:
    raw = str(os.getenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", ROLLOUT_OFF) or ROLLOUT_OFF).strip().lower()
    return raw if raw in _VALID_MODES else ROLLOUT_OFF


def admin_pilot_enabled() -> bool:
    """Extra pilot eligibility source. The rollout mode still gates all behavior."""
    return _env_bool("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", False)


def admin_pilot_id_source() -> str:
    raw = str(os.getenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE", "admin_id") or "admin_id").strip().lower()
    if raw == "admin_id" or raw in _ADMIN_PILOT_SOURCE_ENV:
        return raw
    return "invalid"


def admin_pilot_user_ids() -> Set[int]:
    source = admin_pilot_id_source()
    if source == "admin_id":
        value = configured_admin_id()
        return {value} if value > 0 else set()
    env_name = _ADMIN_PILOT_SOURCE_ENV.get(source)
    return _parse_ids(os.getenv(env_name, "")) if env_name else set()


def allowed_user_ids() -> Set[int]:
    """Parse the Factory allowlist using the same comma-separated ID convention as VELIA chat beta rollout."""
    return _parse_ids(os.getenv("VELIA_SOFTWARE_FACTORY_USER_IDS", ""))


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
    return candidate > 0 and candidate in admin_pilot_user_ids()


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
    return admin_pilot_enabled() and bool(admin_pilot_user_ids())


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


def _stage_readiness(
    user_id: int,
    required_flags: Iterable[str],
    *,
    require_live: bool,
) -> Dict[str, object]:
    flags = tuple(dict.fromkeys(str(name) for name in required_flags if str(name)))
    missing = [name for name in flags if not _env_bool(name, False)]
    mode = rollout_mode()
    mode_ok = mode == ROLLOUT_LIVE if require_live else mode in {ROLLOUT_DRY_RUN, ROLLOUT_LIVE}
    eligible = user_allowed(int(user_id))
    return {
        "ready": not missing and mode_ok and eligible,
        "missing_flags": missing,
        "required_rollout_mode": "live" if require_live else "dry_run_or_live",
        "rollout_mode_ok": mode_ok,
        "user_eligible": eligible,
    }


def pilot_readiness(user_id: int) -> Dict[str, object]:
    return {
        "plan": _stage_readiness(int(user_id), _PLAN_FLAGS, require_live=False),
        "multi_repo_plan": _stage_readiness(
            int(user_id), _MULTI_REPO_PLAN_FLAGS, require_live=False
        ),
        "build_review": _stage_readiness(
            int(user_id), _BUILD_REVIEW_FLAGS, require_live=True
        ),
        "release": _stage_readiness(int(user_id), _RELEASE_FLAGS, require_live=True),
        "greenfield_bootstrap_enabled": _env_bool(
            "VELIA_SOFTWARE_FACTORY_GREENFIELD_BOOTSTRAP_ENABLED", False
        ),
        "integration_repair_enabled": _env_bool(
            "VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_ENABLED", False
        ),
    }


def public_status(user_id: int) -> dict:
    mode = rollout_mode()
    eligible = user_allowed(int(user_id))
    return {
        "mode": mode,
        "eligible": eligible,
        "eligibility_source": eligibility_source(int(user_id)),
        "admin_pilot_enabled": admin_pilot_enabled(),
        "admin_pilot_id_source": admin_pilot_id_source(),
        "admin_pilot_actor_count": len(admin_pilot_user_ids()) if admin_pilot_enabled() else 0,
        "dry_run": bool(mode == ROLLOUT_DRY_RUN and eligible),
        "live_execution": bool(mode == ROLLOUT_LIVE and eligible),
        "supervisor_allowed": bool(supervisor_allowed()),
        "pilot_readiness": pilot_readiness(int(user_id)),
    }