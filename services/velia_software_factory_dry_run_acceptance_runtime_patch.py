from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from services import velia_software_factory_dry_run_acceptance_service as acceptance

logger = logging.getLogger(__name__)
_INSTALLED = False


def install(app: web.Application) -> bool:
    global _INSTALLED
    if _INSTALLED or app.get("velia_software_factory_dry_run_acceptance_installed"):
        return True
    app["velia_software_factory_dry_run_acceptance_installed"] = True
    _INSTALLED = True

    status = acceptance.public_status()
    enabled = bool(status.get("enabled"))
    logger.info(
        "VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_INSTALLED enabled=%s mode=%s repository_write_supported=false autopilot_execution_supported=false",
        str(enabled).lower(),
        str(status.get("mode") or "startup_dry_run_probe"),
    )
    if not enabled:
        return True

    async def run_probe(_app: web.Application) -> None:
        try:
            result = await asyncio.to_thread(acceptance.run_acceptance)
            logger.info(
                "VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_RESULT status=%s passed=%s repository=%s code_ref=%s run_id=%s dry_run=%s execution_blocked=%s missions_unchanged=%s reused=%s blocker=%s",
                str(result.get("status") or "failed"),
                str(bool(result.get("passed"))).lower(),
                str(result.get("repository_full_name") or acceptance.acceptance_repository())[:240],
                str(result.get("code_ref") or acceptance.code_ref())[:40],
                str(result.get("run_id") or "")[:80],
                str(bool(result.get("dry_run"))).lower(),
                str(bool(result.get("execution_blocked"))).lower(),
                str(bool(result.get("autopilot_missions_unchanged"))).lower(),
                str(bool(result.get("reused"))).lower(),
                str(result.get("blocker_code") or "")[:160],
            )
        except Exception as exc:
            logger.exception(
                "VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_RESULT status=failed passed=false repository=%s code_ref=%s error=%s",
                acceptance.acceptance_repository()[:240],
                acceptance.code_ref()[:40],
                str(getattr(exc, "code", exc.__class__.__name__))[:160],
            )

    app.on_startup.append(run_probe)
    return True
