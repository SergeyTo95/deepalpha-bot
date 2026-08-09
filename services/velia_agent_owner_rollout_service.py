from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Optional, Set

from aiohttp import web

logger = logging.getLogger(__name__)
_ID_SPLIT_RE = re.compile(r"[\s,;]+")
_AGENT_BUILDER_PREFIX = "/mobile-api/v1/agents"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _parse_ids(raw: Any) -> Set[int]:
    result: Set[int] = set()
    for token in _ID_SPLIT_RE.split(str(raw or "").strip()):
        if not token:
            continue
        try:
            value = int(token)
        except (TypeError, ValueError):
            continue
        if value > 0:
            result.add(value)
        if len(result) >= 64:
            break
    return result


def owner_user_ids() -> Set[int]:
    """Return server-controlled rollout identities.

    ADMIN_ID is the existing production owner identity. An explicit allowlist is
    supported for future controlled pilots without changing code. Invalid or
    empty values fail closed.
    """

    values = set()
    values.update(_parse_ids(os.getenv("VELIA_AGENT_OWNER_USER_IDS", "")))
    values.update(_parse_ids(os.getenv("ADMIN_ID", "")))
    return values


def owner_rollout_enabled() -> bool:
    # Safe by default: this can only become active when a valid owner identity
    # already exists in server configuration.
    return _env_bool("VELIA_AGENT_OWNER_ROLLOUT_ENABLED", True) and bool(owner_user_ids())


def owner_access_enabled(user_id: Any) -> bool:
    try:
        normalized = int(user_id)
    except (TypeError, ValueError):
        return False
    return owner_rollout_enabled() and normalized in owner_user_ids()


def install(app: web.Application, routes_module: Any) -> None:
    """Install an owner-only rollout without turning global feature flags on.

    The underlying Builder/recall infrastructure is considered available when
    either its normal global flag is enabled or a valid owner rollout exists.
    User-specific wrappers and HTTP middleware keep the controlled rollout from
    becoming a global feature.
    """

    if app.get("velia_agent_owner_rollout_installed"):
        return

    from services import velia_admin_agent_memory_recall_patch as admin_recall_patch
    from services import velia_agent_builder_chat_patch as builder_chat_patch
    from services import velia_agent_builder_service as builder
    from services import velia_agent_memory_recall_chat_patch as recall_chat_patch
    from services import velia_agent_memory_recall_runtime_service as recall_runtime
    from services import velia_agent_memory_recall_service as recall_service

    original_builder_enabled: Callable[[], bool] = getattr(
        builder,
        "_velia_owner_rollout_original_builder_enabled",
        builder.builder_enabled,
    )
    original_prompt_context = getattr(
        builder,
        "_velia_owner_rollout_original_prompt_context",
        builder.prompt_context_for_conversation,
    )
    original_recall_enabled: Callable[[], bool] = getattr(
        recall_service,
        "_velia_owner_rollout_original_recall_enabled",
        recall_service.recall_enabled,
    )
    original_recall_context = getattr(
        recall_runtime,
        "_velia_owner_rollout_original_recall_context",
        recall_runtime.recall_context_for_conversation,
    )

    builder._velia_owner_rollout_original_builder_enabled = original_builder_enabled
    builder._velia_owner_rollout_original_prompt_context = original_prompt_context
    recall_service._velia_owner_rollout_original_recall_enabled = original_recall_enabled
    recall_runtime._velia_owner_rollout_original_recall_context = original_recall_context

    def builder_infrastructure_enabled() -> bool:
        return bool(original_builder_enabled()) or owner_rollout_enabled()

    def prompt_context_for_user(user_id: int, conversation_id: str) -> str:
        if not original_builder_enabled() and not owner_access_enabled(user_id):
            return ""
        return original_prompt_context(int(user_id), str(conversation_id))

    def recall_infrastructure_enabled() -> bool:
        return bool(original_recall_enabled()) or owner_rollout_enabled()

    def recall_context_for_user(user_id: int, conversation_id: str) -> str:
        globally_enabled = bool(original_builder_enabled()) and bool(original_recall_enabled())
        if not globally_enabled and not owner_access_enabled(user_id):
            return ""
        return original_recall_context(int(user_id), str(conversation_id))

    # Original prompt/recall implementations call these module functions
    # internally. Infrastructure becomes available, while the user-aware wrappers
    # above enforce who may actually receive Agent context.
    builder.builder_enabled = builder_infrastructure_enabled
    builder.prompt_context_for_conversation = prompt_context_for_user
    recall_service.recall_enabled = recall_infrastructure_enabled
    recall_runtime.recall_enabled = recall_infrastructure_enabled
    recall_runtime.recall_context_for_conversation = recall_context_for_user

    # These modules import function objects by value. Rebind their module globals
    # as well so rollout security does not depend on Python import order.
    builder_chat_patch.prompt_context_for_conversation = prompt_context_for_user
    recall_chat_patch.recall_context_for_conversation = recall_context_for_user
    admin_recall_patch.recall_enabled = recall_infrastructure_enabled

    @web.middleware
    async def owner_rollout_middleware(request: web.Request, handler):
        path = str(getattr(request, "path", "") or "")
        if not (path == _AGENT_BUILDER_PREFIX or path.startswith(_AGENT_BUILDER_PREFIX + "/")):
            return await handler(request)
        if original_builder_enabled():
            return await handler(request)
        auth: Optional[dict]
        try:
            auth = routes_module._require_mobile_auth(request)
        except Exception:
            auth = None
        if auth and owner_access_enabled(auth.get("user_id")):
            return await handler(request)
        # Preserve the pre-rollout public behavior instead of advertising that a
        # private owner pilot exists.
        return routes_module._json_response(
            {"ok": False, "error": "velia_agent_builder_disabled"},
            status=503,
        )

    app.middlewares.append(owner_rollout_middleware)
    app["velia_agent_owner_rollout_installed"] = True
    logger.info(
        "VELIA_AGENT_OWNER_ROLLOUT_INSTALLED active=%s owner_count=%s",
        owner_rollout_enabled(),
        len(owner_user_ids()),
    )
