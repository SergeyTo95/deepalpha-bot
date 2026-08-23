from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_project_service as project_service
from services import velia_software_factory_autonomy_service as autonomy
from services import velia_software_factory_lead_service as factory
from services import velia_software_factory_stage2_runtime_patch as stage2_runtime
from services import velia_software_factory_stage3_hardening_patch as stage3_hardening
from services import velia_software_factory_workspace_execution_service as workspace_execution
from services import velia_software_factory_workspace_hardening_patch as workspace_hardening
from services import velia_software_factory_workspace_service as workspace_service
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_PREFIX = "/mobile-api/v1/developer/factory"


def _auth(routes_module: Any, request: web.Request) -> Optional[Dict[str, Any]]:
    return routes_module._require_mobile_auth(request)


def _json_error(routes_module: Any, exc: Exception) -> web.Response:
    if isinstance(exc, (SoftwareFactoryError, autopilot.CodingAutopilotError, project_service.DeveloperProjectError)):
        return routes_module._json_response(
            {
                "ok": False,
                "error": str(getattr(exc, "code", "velia_factory_failed")),
                "detail": str(getattr(exc, "detail", "")),
            },
            status=int(getattr(exc, "status", 400)),
        )
    logger.exception("VELIA_SOFTWARE_FACTORY_ROUTE_FAILED")
    return routes_module._json_response({"ok": False, "error": "velia_factory_internal_error"}, status=500)


def _require_available(routes_module: Any, *, execution: bool = False) -> Optional[web.Response]:
    if not routes_module._mobile_api_available():
        return routes_module._disabled_response()
    if not project_service.developer_enabled():
        return routes_module._json_response({"ok": False, "error": "velia_developer_disabled"}, status=503)
    if not factory.software_factory_enabled():
        return routes_module._json_response({"ok": False, "error": "velia_software_factory_disabled"}, status=503)
    if execution and not autopilot.autopilot_enabled():
        return routes_module._json_response({"ok": False, "error": "velia_coding_autopilot_disabled"}, status=503)
    return None


async def _body(request: web.Request) -> Dict[str, Any]:
    try:
        value = await request.json()
    except Exception as exc:
        raise SoftwareFactoryError("velia_factory_json_invalid") from exc
    if not isinstance(value, dict):
        raise SoftwareFactoryError("velia_factory_json_invalid")
    return value


def _execution_matches_workspace(execution: Dict[str, Any], workspace_id: str) -> None:
    if str(execution.get("workspace_id") or "") != str(workspace_id):
        raise SoftwareFactoryError("velia_factory_workspace_execution_not_found", status=404)


def setup_velia_software_factory_routes(app: web.Application, routes_module: Any) -> None:
    stage2_runtime.install(factory)
    stage3_hardening.install(autonomy)
    workspace_hardening.install(workspace_service)
    workspace_execution.install_workspace_execution(app)
    if app.get("velia_software_factory_routes_installed"):
        return

    async def status(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        rollout_status = (
            factory.factory_rollout_status(int(auth["user_id"]))
            if callable(getattr(factory, "factory_rollout_status", None))
            else {"mode": "off", "eligible": False, "dry_run": False, "live_execution": False}
        )
        integration_enabled = bool(
            callable(getattr(workspace_execution, "integration_validator_enabled", None))
            and workspace_execution.integration_validator_enabled()
        )
        repair_status = (
            workspace_execution.integration_repair_status()
            if callable(getattr(workspace_execution, "integration_repair_status", None))
            else {
                "available": True,
                "enabled": False,
                "max_attempts": 2,
                "same_pull_request_only": True,
                "new_branch_allowed": False,
                "new_pull_request_allowed": False,
                "write_owner": "coding_autopilot",
            }
        )
        return routes_module._json_response(
            {
                "ok": True,
                "enabled": factory.software_factory_enabled(),
                "team_enabled": factory.team_runtime_enabled(),
                "autonomy_enabled": autonomy.autonomy_enabled(),
                "supervisor_enabled": autonomy.supervisor_enabled(),
                "developer_enabled": project_service.developer_enabled(),
                "autopilot_enabled": autopilot.autopilot_enabled(),
                "stage": "4.3",
                "rollout": rollout_status,
                "workspace_capabilities": {
                    "multi_repo_registry": True,
                    "workspace_brain": True,
                    "per_repo_scope_approval": True,
                    "cross_repo_plan_validation": True,
                    "multi_repo_execution_available": True,
                    "multi_repo_execution_enabled": workspace_execution.workspace_execution_enabled(),
                    "multi_repo_supervisor_enabled": workspace_execution.workspace_supervisor_enabled(),
                    "integration_validator_available": True,
                    "integration_validator_enabled": integration_enabled,
                    "integration_contract_inference": True,
                    "integration_repair_available": bool(repair_status.get("available", True)),
                    "integration_repair_enabled": bool(repair_status.get("enabled", False)),
                    "integration_repair_same_pull_request_only": bool(repair_status.get("same_pull_request_only", True)),
                    "integration_repair_max_attempts": int(repair_status.get("max_attempts", 2) or 0),
                    "dependency_gate": "ready_for_review",
                    "completion_gate": "cross_repo_integration_validation_after_bounded_repair",
                },
                "pipeline": [
                    "natural_language_intake",
                    "project_spec",
                    "persistent_project_brain",
                    "workspace_registry",
                    "per_repo_write_scope",
                    "state_machine",
                    "event_log",
                    "material_clarifier",
                    "architect",
                    "designer_when_needed",
                    "planner",
                    "cross_repo_task_dag",
                    "integration_contract_inference",
                    "per_repo_autopilot_missions",
                    "workspace_scheduler",
                    "coding_autopilot",
                    "integration_validator",
                    "bounded_same_pr_integration_repair",
                    "autonomous_supervisor",
                ],
                "execution_owner": "coding_autopilot",
                "review_owner": "coding_autopilot",
                "completion_scope": "review_ready",
                "completion_gate": "integration_validation_after_bounded_repair_when_cross_repo",
                "stop_semantics": "pause_then_safe_boundary",
            }
        )

    async def team(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        return routes_module._json_response(
            {
                "ok": True,
                "enabled": factory.team_runtime_enabled(),
                "autonomy_enabled": autonomy.autonomy_enabled(),
                "supervisor_enabled": autonomy.supervisor_enabled(),
                "roles": factory.team_role_catalog(),
                "write_owner": "coding_autopilot",
                "review_owner": "coding_autopilot",
            }
        )

    async def project_brain(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            await asyncio.to_thread(factory.ensure_software_factory_tables)
            try:
                limit = min(300, max(1, int(request.query.get("limit") or 100)))
            except (TypeError, ValueError):
                limit = 100
            items = await asyncio.to_thread(
                factory.list_project_brain,
                int(auth["user_id"]),
                request.match_info["project_id"],
                limit,
            )
            return routes_module._json_response({"ok": True, "brain": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def list_workspaces(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            try:
                limit = min(100, max(1, int(request.query.get("limit") or 50)))
            except (TypeError, ValueError):
                limit = 50
            items = await asyncio.to_thread(workspace_service.list_workspaces, int(auth["user_id"]), limit)
            return routes_module._json_response({"ok": True, "workspaces": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def create_workspace(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                workspace_service.create_workspace,
                int(auth["user_id"]),
                await _body(request),
            )
            return routes_module._json_response({"ok": True, "workspace": item}, status=201)
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def get_workspace(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                workspace_service.get_workspace,
                int(auth["user_id"]),
                request.match_info["workspace_id"],
            )
            return routes_module._json_response({"ok": True, "workspace": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def workspace_brain(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            try:
                limit = min(300, max(1, int(request.query.get("limit") or 100)))
            except (TypeError, ValueError):
                limit = 100
            items = await asyncio.to_thread(
                workspace_service.list_workspace_brain,
                int(auth["user_id"]),
                request.match_info["workspace_id"],
                limit,
            )
            return routes_module._json_response({"ok": True, "brain": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def approve_workspace_scope(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await _body(request)
            item = await asyncio.to_thread(
                workspace_service.approve_workspace_scope,
                int(auth["user_id"]),
                request.match_info["workspace_id"],
                request.match_info["project_id"],
                allowed_paths=payload.get("allowed_paths"),
                blocked_paths=payload.get("blocked_paths"),
            )
            return routes_module._json_response({"ok": True, "workspace": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def revoke_workspace_scope(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                workspace_service.revoke_workspace_scope,
                int(auth["user_id"]),
                request.match_info["workspace_id"],
                request.match_info["project_id"],
            )
            return routes_module._json_response({"ok": True, "workspace": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def validate_workspace_plan(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            workspace = await asyncio.to_thread(
                workspace_service.get_workspace,
                int(auth["user_id"]),
                request.match_info["workspace_id"],
            )
            plan = workspace_service.normalize_workspace_plan(await _body(request), workspace)
            return routes_module._json_response({"ok": True, "plan": plan})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def list_workspace_executions(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            try:
                limit = min(50, max(1, int(request.query.get("limit") or 20)))
            except (TypeError, ValueError):
                limit = 20
            items = await asyncio.to_thread(
                workspace_execution.list_executions,
                int(auth["user_id"]),
                request.match_info["workspace_id"],
                limit,
            )
            return routes_module._json_response({"ok": True, "executions": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def create_workspace_execution(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module, execution=True)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                workspace_execution.create_execution,
                int(auth["user_id"]),
                request.match_info["workspace_id"],
                await _body(request),
            )
            return routes_module._json_response({"ok": True, "execution": item}, status=201)
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def get_workspace_execution(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                workspace_execution.get_execution,
                int(auth["user_id"]),
                request.match_info["execution_id"],
            )
            _execution_matches_workspace(item, request.match_info["workspace_id"])
            return routes_module._json_response({"ok": True, "execution": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def tick_workspace_execution(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module, execution=True)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            current = await asyncio.to_thread(
                workspace_execution.get_execution,
                int(auth["user_id"]),
                request.match_info["execution_id"],
            )
            _execution_matches_workspace(current, request.match_info["workspace_id"])
            item = await asyncio.to_thread(
                workspace_execution.tick_execution,
                int(auth["user_id"]),
                request.match_info["execution_id"],
            )
            return routes_module._json_response({"ok": True, "execution": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def validate_workspace_integration(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            current = await asyncio.to_thread(
                workspace_execution.get_execution,
                int(auth["user_id"]),
                request.match_info["execution_id"],
            )
            _execution_matches_workspace(current, request.match_info["workspace_id"])
            validation = await asyncio.to_thread(
                workspace_execution.validate_integration,
                int(auth["user_id"]),
                request.match_info["execution_id"],
            )
            return routes_module._json_response({"ok": True, "integration_validation": validation})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def resume_workspace_execution(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module, execution=True)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            current = await asyncio.to_thread(
                workspace_execution.get_execution,
                int(auth["user_id"]),
                request.match_info["execution_id"],
            )
            _execution_matches_workspace(current, request.match_info["workspace_id"])
            item = await asyncio.to_thread(
                workspace_execution.resume_execution,
                int(auth["user_id"]),
                request.match_info["execution_id"],
            )
            return routes_module._json_response({"ok": True, "execution": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def stop_workspace_execution(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            current = await asyncio.to_thread(
                workspace_execution.get_execution,
                int(auth["user_id"]),
                request.match_info["execution_id"],
            )
            _execution_matches_workspace(current, request.match_info["workspace_id"])
            item = await asyncio.to_thread(
                workspace_execution.request_stop,
                int(auth["user_id"]),
                request.match_info["execution_id"],
            )
            return routes_module._json_response({"ok": True, "execution": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def workspace_execution_events(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            current = await asyncio.to_thread(
                workspace_execution.get_execution,
                int(auth["user_id"]),
                request.match_info["execution_id"],
            )
            _execution_matches_workspace(current, request.match_info["workspace_id"])
            try:
                limit = min(500, max(1, int(request.query.get("limit") or 200)))
            except (TypeError, ValueError):
                limit = 200
            items = await asyncio.to_thread(
                workspace_execution.list_events,
                int(auth["user_id"]),
                request.match_info["execution_id"],
                limit,
            )
            return routes_module._json_response({"ok": True, "events": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def create(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(factory.create_run, int(auth["user_id"]), await _body(request))
            return routes_module._json_response({"ok": True, "run": item}, status=201)
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def get(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(factory.get_run, int(auth["user_id"]), request.match_info["run_id"])
            return routes_module._json_response({"ok": True, "run": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def events(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            try:
                limit = min(500, max(1, int(request.query.get("limit") or 200)))
            except (TypeError, ValueError):
                limit = 200
            items = await asyncio.to_thread(
                factory.list_events,
                int(auth["user_id"]),
                request.match_info["run_id"],
                limit,
            )
            return routes_module._json_response({"ok": True, "events": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def clarify(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await _body(request)
            answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else payload
            item = await asyncio.to_thread(
                factory.answer_clarifications,
                int(auth["user_id"]),
                request.match_info["run_id"],
                answers,
            )
            return routes_module._json_response({"ok": True, "run": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def advance(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module, execution=True)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(factory.advance_run, int(auth["user_id"]), request.match_info["run_id"])
            return routes_module._json_response({"ok": True, "run": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def stop(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module, execution=True)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        if not autonomy.autonomy_enabled():
            return routes_module._json_response({"ok": False, "error": "velia_software_factory_autonomy_disabled"}, status=503)
        try:
            item = await asyncio.to_thread(autonomy.request_stop, int(auth["user_id"]), request.match_info["run_id"])
            return routes_module._json_response({"ok": True, "stop": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    app.router.add_get(f"{_PREFIX}/status", status)
    app.router.add_get(f"{_PREFIX}/team", team)
    app.router.add_get(f"{_PREFIX}/projects/{{project_id}}/brain", project_brain)
    app.router.add_get(f"{_PREFIX}/workspaces", list_workspaces)
    app.router.add_post(f"{_PREFIX}/workspaces", create_workspace)
    app.router.add_get(f"{_PREFIX}/workspaces/{{workspace_id}}", get_workspace)
    app.router.add_get(f"{_PREFIX}/workspaces/{{workspace_id}}/brain", workspace_brain)
    app.router.add_post(f"{_PREFIX}/workspaces/{{workspace_id}}/repositories/{{project_id}}/scope", approve_workspace_scope)
    app.router.add_delete(f"{_PREFIX}/workspaces/{{workspace_id}}/repositories/{{project_id}}/scope", revoke_workspace_scope)
    app.router.add_post(f"{_PREFIX}/workspaces/{{workspace_id}}/plans/validate", validate_workspace_plan)
    app.router.add_get(f"{_PREFIX}/workspaces/{{workspace_id}}/executions", list_workspace_executions)
    app.router.add_post(f"{_PREFIX}/workspaces/{{workspace_id}}/executions", create_workspace_execution)
    app.router.add_get(f"{_PREFIX}/workspaces/{{workspace_id}}/executions/{{execution_id}}", get_workspace_execution)
    app.router.add_post(f"{_PREFIX}/workspaces/{{workspace_id}}/executions/{{execution_id}}/tick", tick_workspace_execution)
    app.router.add_post(f"{_PREFIX}/workspaces/{{workspace_id}}/executions/{{execution_id}}/validate-integration", validate_workspace_integration)
    app.router.add_post(f"{_PREFIX}/workspaces/{{workspace_id}}/executions/{{execution_id}}/resume", resume_workspace_execution)
    app.router.add_post(f"{_PREFIX}/workspaces/{{workspace_id}}/executions/{{execution_id}}/stop", stop_workspace_execution)
    app.router.add_get(f"{_PREFIX}/workspaces/{{workspace_id}}/executions/{{execution_id}}/events", workspace_execution_events)
    app.router.add_post(f"{_PREFIX}/runs", create)
    app.router.add_get(f"{_PREFIX}/runs/{{run_id}}", get)
    app.router.add_get(f"{_PREFIX}/runs/{{run_id}}/events", events)
    app.router.add_post(f"{_PREFIX}/runs/{{run_id}}/clarifications", clarify)
    app.router.add_post(f"{_PREFIX}/runs/{{run_id}}/advance", advance)
    app.router.add_post(f"{_PREFIX}/runs/{{run_id}}/stop", stop)
    app["velia_software_factory_routes_installed"] = True
    logger.info(
        "VELIA_SOFTWARE_FACTORY_ROUTES_INSTALLED stage=4.3 workspace_execution=%s integration_validator=%s integration_repair=%s",
        str(workspace_execution.workspace_execution_enabled()).lower(),
        str(workspace_execution.integration_validator_enabled()).lower(),
        str(bool(repair_status.get("enabled", False))).lower(),
    )
