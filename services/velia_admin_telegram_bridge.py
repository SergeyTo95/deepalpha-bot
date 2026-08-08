import logging
from typing import Any

from services.velia_admin_control_service import (
    adjust_user_tokens,
    set_user_banned,
    set_user_token_balance,
    set_user_vip_status,
)
from services.velia_admin_security_service import configured_admin_id


logger = logging.getLogger(__name__)


def _actor() -> int:
    admin_user_id = configured_admin_id()
    if admin_user_id <= 0:
        raise RuntimeError("ADMIN_ID is not configured")
    return admin_user_id


def _require_ok(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "admin_mutation_failed"))
    return result


def _set_user_ban(user_id: int, banned: bool) -> None:
    _require_ok(
        set_user_banned(
            admin_user_id=_actor(),
            user_id=int(user_id),
            banned=bool(banned),
            source="telegram",
        )
    )


def _set_user_vip(user_id: int, vip: bool) -> None:
    _require_ok(
        set_user_vip_status(
            admin_user_id=_actor(),
            user_id=int(user_id),
            vip=bool(vip),
            source="telegram",
        )
    )


def _add_tokens(user_id: int, amount: int) -> int:
    result = _require_ok(
        adjust_user_tokens(
            admin_user_id=_actor(),
            user_id=int(user_id),
            delta=int(amount),
            source="telegram",
        )
    )
    return int((result.get("after") or {}).get("token_balance") or 0)


def _set_tokens(user_id: int, amount: int) -> None:
    _require_ok(
        set_user_token_balance(
            admin_user_id=_actor(),
            user_id=int(user_id),
            amount=int(amount),
            source="telegram",
        )
    )


def install(admin_module: Any) -> None:
    if getattr(admin_module, "_velia_admin_shared_mutations_installed", False):
        return
    # Patch only the Telegram admin module's imported names. Other application
    # call sites keep the original DB functions and are unaffected.
    admin_module.set_user_ban = _set_user_ban
    admin_module.set_user_vip = _set_user_vip
    admin_module.add_tokens = _add_tokens
    admin_module.set_tokens = _set_tokens
    admin_module._velia_admin_shared_mutations_installed = True
    logger.info("VELIA_ADMIN_TELEGRAM_SHARED_MUTATIONS_INSTALLED")
