from __future__ import annotations

import os
import re
from typing import Dict, Iterable, Set

from services import velia_software_factory_live_pilot_guard_service as live_pilot_guard
from services.velia_admin_security_service import configured_admin_id


ROLLOUT_OFF = "off"
ROLLOUT_DRY_RUN = "dry_run"
ROLLOUT_LIMITED_ADMIN = "limited_admin"
ROLLOUT_LIVE = "live"
ROLLOUT_FULL_AUTONOMY = "full_autonomy"
_VALID_MODES = {
    ROLLOUT_OFF,
    ROLLOUT_DRY_RUN,
    ROLLOUT_LIMITED_ADMIN,
    ROLLOUT_LIVE,
    ROLLOUT_FULL_AUTONOMY,
}
_ID_SPLIT_RE = re.compile(r"[\s,;]+")
_ADMIN_PILOT_SOURCE_ENV = {
    "live_owner": "LIVE_OWNER_USER_IDS",
    "jarvis_founder": "JARVIS_FOUNDER_IDS",
    "chat_beta": "VELIA_CHAT_BETA_USER_IDS",
    "mobile_debug": "VELIA_MOBILE_DEBUG_USER_IDS",
}
_LIVE_PILOT_GUARD_FLAG = "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED"

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


def _limited_admin_actor_allowed(user_id: int) -> bool:
    """Stage 7 is bound to the configured administrator, never a shared pilot list.

    Alternative Stage 6 pilot identity sources remain valid in dry-run/live modes,
    but `limited_admin` is a distinct owner-only rollout envelope. Requiring the
    normal admin-pilot enable flag preserves an explicit server-side activation
    gate without allowing live_owner/chat_beta/etc. members into Stage 7.
    """

    if not admin_pilot_enabled():
        return False
    try:
        candidate = int(user_id)
    except (TypeError, ValueError):
        return False
    expected = configured_admin_id()
    return bool(expected > 0 and candidate == expected)


def user_allowed(user_id: int) -> bool:
    """Fail closed: neither an empty allowlist nor a disabled admin pilot means everyone."""
    return explicit_user_allowed(int(user_id)) or admin_pilot_user_allowed(int(user_id))


def _mode_user_allowed(user_id: int) -> bool:
    if rollout_mode() == ROLLOUT_LIMITED_ADMIN:
        return _limited_admin_actor_allowed(int(user_id))
    return user_allowed(int(user_id))


def intake_allowed(user_id: int) -> bool:
    return rollout_mode() in {
        ROLLOUT_DRY_RUN,
        ROLLOUT_LIMITED_ADMIN,
        ROLLOUT_LIVE,
        ROLLOUT_FULL_AUTONOMY,
    } and _mode_user_allowed(int(user_id))


def dry_run_enabled(user_id: int) -> bool:
    return rollout_mode() == ROLLOUT_DRY_RUN and user_allowed(int(user_id))


def _limited_admin_execution_allowed(user_id: int) -> bool:
    from services import velia_software_factory_stage7_limited_admin_rollout_service as stage7

    return bool(stage7.execution_allowed(int(user_id)))


def _limited_admin_status(user_id: int, *, verify_acceptance: bool) -> Dict[str, object]:
    from services import velia_software_factory_stage7_limited_admin_rollout_service as stage7

    return dict(stage7.public_status(int(user_id), verify_acceptance=verify_acceptance))


def _full_autonomy_execution_allowed(user_id: int) -> bool:
    from services import velia_software_factory_stage8_full_autonomy_service as stage8

    eligible = user_allowed(int(user_id))
    return bool(stage8.execution_allowed(int(user_id), user_eligible=eligible))


def _full_autonomy_status(user_id: int) -> Dict[str, object]:
    from services import velia_software_factory_stage8_full_autonomy_service as stage8

    return dict(stage8.public_status(int(user_id), user_eligible=user_allowed(int(user_id))))


def live_execution_allowed(user_id: int) -> bool:
    mode = rollout_mode()
    if mode == ROLLOUT_LIMITED_ADMIN:
        if not _limited_admin_actor_allowed(int(user_id)):
            return False
        if not live_pilot_guard.live_pilot_guard_enabled():
            return False
        return _limited_admin_execution_allowed(int(user_id))
    if mode == ROLLOUT_FULL_AUTONOMY:
        if not user_allowed(int(user_id)):
            return False
        return _full_autonomy_execution_allowed(int(user_id))
    if mode != ROLLOUT_LIVE:
        return False
    if explicit_user_allowed(int(user_id)):
        return True
    if admin_pilot_user_allowed(int(user_id)):
        return live_pilot_guard.live_pilot_guard_enabled()
    return False


def _admin_pilot_configured() -> bool:
    return admin_pilot_enabled() and bool(admin_pilot_user_ids())


def supervisor_allowed() -> bool:
    """Supervisor can exist only behind a controlled write-capable rollout boundary."""
    mode = rollout_mode()
    if mode == ROLLOUT_LIMITED_ADMIN:
        actor = configured_admin_id()
        if actor <= 0 or not _limited_admin_actor_allowed(actor):
            return False
        if not live_pilot_guard.live_pilot_guard_enabled():
            return False
        return _limited_admin_execution_allowed(actor)
    if mode == ROLLOUT_FULL_AUTONOMY:
        actors = set(allowed_user_ids())
        if admin_pilot_enabled():
            actors.update(admin_pilot_user_ids())
        return any(_full_autonomy_execution_allowed(actor) for actor in sorted(actors))
    if mode != ROLLOUT_LIVE:
        return False
    # Preserve the existing explicit allowlist rollout path. Admin-pilot-only
    # execution is stricter and requires the Stage 6.2 one-shot dispatch guard.
    if bool(allowed_user_ids()):
        return True
    return _admin_pilot_configured() and live_pilot_guard.live_pilot_guard_enabled()


def eligibility_source(user_id: int) -> str:
    # Stage 7 must preserve the admin-pilot classification even when the same
    # administrator also happens to be present in the general Factory allowlist;
    # the one-shot control/preflight deliberately require this source label.
    if rollout_mode() == ROLLOUT_LIMITED_ADMIN:
        return "admin_pilot" if _limited_admin_actor_allowed(int(user_id)) else "none"
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
    allow_limited_admin: bool = False,
) -> Dict[str, object]:
    flags = tuple(dict.fromkeys(str(name) for name in required_flags if str(name)))
    missing = [name for name in flags if not _env_bool(name, False)]
    if (
        require_live
        and eligibility_source(int(user_id)) == "admin_pilot"
        and rollout_mode() != ROLLOUT_FULL_AUTONOMY
        and not live_pilot_guard.live_pilot_guard_enabled()
        and _LIVE_PILOT_GUARD_FLAG not in missing
    ):
        missing.append(_LIVE_PILOT_GUARD_FLAG)
    mode = rollout_mode()
    if require_live:
        mode_ok = mode in {ROLLOUT_LIVE, ROLLOUT_FULL_AUTONOMY} or (
            allow_limited_admin and mode == ROLLOUT_LIMITED_ADMIN
        )
        required_mode = (
            "limited_admin_full_autonomy_or_live"
            if allow_limited_admin
            else "full_autonomy_or_live"
        )
    else:
        mode_ok = mode in {
            ROLLOUT_DRY_RUN,
            ROLLOUT_LIMITED_ADMIN,
            ROLLOUT_LIVE,
            ROLLOUT_FULL_AUTONOMY,
        }
        required_mode = "dry_run_limited_admin_full_autonomy_or_live"
    eligible = _mode_user_allowed(int(user_id))
    return {
        "ready": not missing and mode_ok and eligible,
        "missing_flags": missing,
        "required_rollout_mode": required_mode,
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
            int(user_id), _BUILD_REVIEW_FLAGS, require_live=True, allow_limited_admin=True
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
    eligible = _mode_user_allowed(int(user_id))
    stage7_status = _limited_admin_status(
        int(user_id), verify_acceptance=bool(mode == ROLLOUT_LIMITED_ADMIN)
    )
    stage8_status = _full_autonomy_status(int(user_id))
    return {
        "mode": mode,
        "eligible": eligible,
        "eligibility_source": eligibility_source(int(user_id)),
        "admin_pilot_enabled": admin_pilot_enabled(),
        "admin_pilot_id_source": admin_pilot_id_source(),
        "admin_pilot_actor_count": len(admin_pilot_user_ids()) if admin_pilot_enabled() else 0,
        "live_pilot_guard": live_pilot_guard.public_status(),
        "limited_admin": stage7_status,
        "full_autonomy": stage8_status,
        "dry_run": bool(mode == ROLLOUT_DRY_RUN and eligible),
        "live_execution": bool(live_execution_allowed(int(user_id))),
        "supervisor_allowed": bool(supervisor_allowed()),
        "pilot_readiness": pilot_readiness(int(user_id)),
    }
