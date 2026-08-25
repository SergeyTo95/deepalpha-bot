from __future__ import annotations

import asyncio
import html
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import quote_plus

from aiohttp import web

from services import velia_software_factory_live_pilot_control_service as control
from services.velia_admin_security_service import record_admin_audit
from services.velia_software_factory_admin_acceptance_admin_routes import (
    setup_factory_pilot_acceptance_admin_routes,
)
from services.velia_software_factory_core_service import SoftwareFactoryError
from services.velia_software_factory_live_pilot_preflight_admin_routes import (
    setup_factory_pilot_preflight_admin_routes,
)


GuardFn = Callable[[web.Request], Awaitable[Optional[web.StreamResponse]]]
LayoutFn = Callable[[str, str, str, str, str], str]
KeyFn = Callable[[web.Request], str]
RequestIdFn = Callable[[web.Request], str]


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _admin_id(request: web.Request) -> int:
    session = request.get("velia_admin_session") or {}
    try:
        return int(session.get("admin_user_id") or 0)
    except (TypeError, ValueError):
        return 0


def _audit_metadata(request: web.Request, request_id: str) -> Dict[str, Any]:
    return {
        "admin_user_id": _admin_id(request) or None,
        "request_id": request_id,
        "source": "web",
        "ip": request.remote or "",
        "user_agent": request.headers.get("User-Agent", ""),
    }


def _status_badge(enabled: bool, yes: str = "Enabled", no: str = "Disabled") -> str:
    cls = "good-text" if enabled else "bad-text"
    label = yes if enabled else no
    return f"<b class='{cls}'>{_e(label)}</b>"


def _field(name: str, label: str, value: str = "", *, required: bool = True) -> str:
    req = " required" if required else ""
    return (
        f"<label>{_e(label)}"
        f"<input name='{_e(name)}' value='{_e(value)}' autocomplete='off'{req}></label>"
    )


def _redirect(run_id: str = "", repository: str = "", message: str = "") -> web.HTTPFound:
    params = []
    if run_id:
        params.append(f"run_id={quote_plus(run_id)}")
    if repository:
        params.append(f"repository={quote_plus(repository)}")
    if message:
        params.append(f"msg={quote_plus(message)}")
    suffix = "?" + "&".join(params) if params else ""
    return web.HTTPFound(f"/admin/factory-pilot{suffix}")


def _render_page(
    status: Dict[str, Any],
    run_id: str,
    repository: str,
    grant_view: Optional[Dict[str, Any]],
    lookup_error: str,
) -> str:
    guard_state = dict(status.get("guard") or {})
    rollout_state = dict(status.get("rollout") or {})
    readiness = dict(rollout_state.get("pilot_readiness") or {})
    build_review = dict(readiness.get("build_review") or {})

    control_enabled = bool(status.get("enabled"))
    one_shot_enabled = bool(guard_state.get("enabled"))
    live_allowed = bool(rollout_state.get("live_execution_allowed"))
    build_ready = bool(build_review.get("ready"))

    grant = dict((grant_view or {}).get("grant") or {})
    grant_id = str(grant.get("grant_id") or "")
    grant_status = str(grant.get("status") or "")
    expected_arm = str((grant_view or {}).get("expected_arm_confirmation") or "")
    expected_dispatch = str((grant_view or {}).get("expected_dispatch_confirmation") or "")

    lookup_note = ""
    if lookup_error:
        lookup_note = f"<div class='card full'><h2>Run lookup</h2><div class='bad-text'>{_e(lookup_error)}</div></div>"
    elif grant_view:
        lookup_note = f"""
<div class='card full'><h2>Bound grant</h2>
<div class='action-box'>
<div><span class='label'>Run</span><br><code>{_e(run_id)}</code></div>
<div><span class='label'>Repository</span><br><code>{_e(repository)}</code></div>
<div><span class='label'>Grant ID</span><br><code>{_e(grant_id or 'Unavailable')}</code></div>
<div><span class='label'>Status</span><br>{_e(grant_status or 'Unavailable')}</div>
<div><span class='label'>Expires</span><br>{_e(grant.get('expires_at') or 'Unavailable')}</div>
<div><span class='label'>Autopilot task</span><br><code>{_e(grant.get('autopilot_task_id') or '—')}</code></div>
</div></div>"""

    arm_confirmation = expected_arm or (f"arm:{run_id}:{repository}" if run_id and repository else "")
    dispatch_confirmation = expected_dispatch or (
        f"dispatch:{run_id}:{repository}:{grant_id}" if run_id and repository and grant_id else ""
    )

    return f"""
<div class='grid'>
  <div class='card'><div class='label'>Control gate</div><div class='value'>{_status_badge(control_enabled)}</div><div class='hint'>Independent Stage 6.3 gate. This page cannot change it.</div></div>
  <div class='card'><div class='label'>One-shot guard</div><div class='value'>{_status_badge(one_shot_enabled)}</div><div class='hint'>Maximum one Coding Autopilot task per run.</div></div>
  <div class='card'><div class='label'>Live execution</div><div class='value'>{_status_badge(live_allowed, 'Allowed', 'Blocked')}</div><div class='hint'>Controlled rollout must already be enabled externally.</div></div>
  <div class='card'><div class='label'>Build / review readiness</div><div class='value'>{_status_badge(build_ready, 'Ready', 'Not ready')}</div><div class='hint'>Write/review prerequisites are checked by the control core.</div></div>

  <div class='card full'><h2>Safety boundary</h2>
    <div class='muted'>Owner-only · CSRF protected · exact run/repository/grant binding · no automatic grant · no automatic dispatch · no merge or deployment controls. Production rollout flags are intentionally not editable from this screen.</div>
    <div class='hint'><a href='/admin/factory-pilot/acceptance'>Stage 6.7 controlled acceptance</a> is a stricter one-shot envelope that requires Reviewer remediation evidence.</div>
  </div>

  <div class='card full'><h2>Inspect exact Factory run</h2>
    <form class='inline' method='get' action='/admin/factory-pilot'>
      {_field('run_id', 'Run ID', run_id)}
      {_field('repository', 'Repository owner/name', repository)}
      <button class='primary' type='submit'>Inspect</button>
    </form>
  </div>
  {lookup_note}

  <div class='card wide'><h2>1. Arm one-shot grant</h2>
    <form method='post' action='/admin/factory-pilot/actions/arm'>
      <div class='action-box'>
        {_field('run_id', 'Run ID', run_id)}
        {_field('repository', 'Exact repository', repository)}
        <div class='hint'>Type exactly: <code>{_e(arm_confirmation or 'Inspect a run first')}</code></div>
        {_field('confirmation', 'Type exact confirmation')}
        <label class='confirm'><input type='checkbox' name='confirmed' value='yes' required> I confirm this exact one-shot grant</label>
        <button class='primary' type='submit' data-confirm='Arm one one-shot Factory pilot grant?'>Arm grant</button>
      </div>
    </form>
  </div>

  <div class='card wide'><h2>2. Revoke pending grant</h2>
    <form method='post' action='/admin/factory-pilot/actions/revoke'>
      <div class='action-box'>
        {_field('run_id', 'Run ID', run_id)}
        {_field('repository', 'Exact repository', repository)}
        <label class='confirm'><input type='checkbox' name='confirmed' value='yes' required> I confirm revoke</label>
        <button class='danger' type='submit' data-confirm='Revoke the pending Factory pilot grant?'>Revoke grant</button>
      </div>
    </form>
    <div class='hint'>Revocation remains available even after live/control gates are closed.</div>
  </div>

  <div class='card full'><h2>3. Dispatch exactly one Factory task</h2>
    <form method='post' action='/admin/factory-pilot/actions/dispatch'>
      <div class='action-box'>
        <div class='action-row'>
          {_field('run_id', 'Run ID', run_id)}
          {_field('repository', 'Exact repository', repository)}
          {_field('grant_id', 'Exact grant ID', grant_id)}
        </div>
        <div class='hint'>Type exactly: <code>{_e(dispatch_confirmation or 'Inspect an armed grant first')}</code></div>
        {_field('confirmation', 'Type exact dispatch confirmation')}
        <label class='confirm'><input type='checkbox' name='confirmed' value='yes' required> I confirm one live Coding Autopilot dispatch</label>
        <button class='danger' type='submit' data-confirm='Dispatch exactly one live Coding Autopilot task for this Factory run?'>Dispatch one task</button>
      </div>
    </form>
    <div class='hint'>The Stage 6.2 grant-first runtime guard is authoritative. A consumed grant cannot dispatch a second task.</div>
  </div>
</div>"""


def setup_factory_pilot_admin_routes(
    app: web.Application,
    *,
    guard: GuardFn,
    layout: LayoutFn,
    key: KeyFn,
    request_id: RequestIdFn,
) -> None:
    if app.get("velia_factory_pilot_admin_routes_installed"):
        return

    async def factory_pilot_page(request: web.Request) -> web.Response:
        denied = await guard(request)
        if denied is not None:
            return denied
        admin_user_id = _admin_id(request)
        status = await asyncio.to_thread(control.public_status, admin_user_id)
        run_id = str(request.query.get("run_id", "") or "").strip()[:160]
        repository = str(request.query.get("repository", "") or "").strip()[:240]
        grant_view: Optional[Dict[str, Any]] = None
        lookup_error = ""
        if run_id or repository:
            if not run_id or not repository:
                lookup_error = "Both run ID and repository are required for inspection."
            else:
                try:
                    grant_view = await asyncio.to_thread(
                        control.grant_status,
                        admin_user_id,
                        run_id,
                        repository,
                    )
                except SoftwareFactoryError as exc:
                    lookup_error = str(exc.code or "velia_factory_live_pilot_lookup_failed")
        body = _render_page(status, run_id, repository, grant_view, lookup_error)
        return web.Response(
            text=layout(
                "Factory Pilot",
                "Factory Pilot",
                key(request),
                body,
                str(request.query.get("msg", "") or "")[:240],
            ),
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    async def factory_pilot_action(request: web.Request) -> web.StreamResponse:
        denied = await guard(request)
        if denied is not None:
            return denied
        admin_user_id = _admin_id(request)
        form = await request.post()
        action = str(request.match_info.get("action") or "").strip().lower()
        run_id = str(form.get("run_id", "") or "").strip()[:160]
        repository = str(form.get("repository", "") or "").strip()[:240]
        confirmation = str(form.get("confirmation", "") or "").strip()[:800]
        grant_id = str(form.get("grant_id", "") or "").strip()[:160]
        req_id = request_id(request)
        metadata = _audit_metadata(request, req_id)

        if str(form.get("confirmed", "") or "") != "yes":
            await asyncio.to_thread(
                record_admin_audit,
                **metadata,
                action=f"factory_pilot.{action or 'unknown'}",
                target_type="software_factory_run",
                target_id=run_id,
                before={"repository_full_name": repository},
                success=False,
                error_code="explicit_confirmation_required",
            )
            raise web.HTTPBadRequest(text="Explicit confirmation required")

        if action not in {"arm", "revoke", "dispatch"}:
            raise web.HTTPNotFound()
        if not run_id or not repository:
            raise web.HTTPBadRequest(text="Run ID and repository are required")

        try:
            if action == "arm":
                result = await asyncio.to_thread(
                    control.arm_grant,
                    admin_user_id,
                    run_id,
                    repository,
                    confirmation,
                    ttl_seconds=600,
                )
            elif action == "revoke":
                result = await asyncio.to_thread(
                    control.revoke_grant,
                    admin_user_id,
                    run_id,
                    repository,
                )
            else:
                result = await asyncio.to_thread(
                    control.dispatch_once,
                    admin_user_id,
                    run_id,
                    repository,
                    grant_id,
                    confirmation,
                )
            grant = dict((result or {}).get("grant") or {})
            await asyncio.to_thread(
                record_admin_audit,
                **metadata,
                action=f"factory_pilot.{action}",
                target_type="software_factory_run",
                target_id=run_id,
                before={
                    "repository_full_name": repository,
                    "grant_id": grant_id or None,
                },
                after={
                    "repository_full_name": repository,
                    "grant_id": grant.get("grant_id"),
                    "grant_status": grant.get("status"),
                    "autopilot_task_id": grant.get("autopilot_task_id"),
                    "max_dispatches": (result or {}).get("max_dispatches"),
                },
                success=True,
            )
            return _redirect(run_id, repository, f"{action.title()} completed and audited")
        except SoftwareFactoryError as exc:
            code = str(exc.code or "velia_factory_live_pilot_action_blocked")
            await asyncio.to_thread(
                record_admin_audit,
                **metadata,
                action=f"factory_pilot.{action}",
                target_type="software_factory_run",
                target_id=run_id,
                before={
                    "repository_full_name": repository,
                    "grant_id": grant_id or None,
                },
                success=False,
                error_code=code,
            )
            return _redirect(run_id, repository, f"Blocked: {code}")
        except Exception:
            await asyncio.to_thread(
                record_admin_audit,
                **metadata,
                action=f"factory_pilot.{action}",
                target_type="software_factory_run",
                target_id=run_id,
                before={"repository_full_name": repository},
                success=False,
                error_code="factory_pilot_internal_error",
            )
            raise web.HTTPInternalServerError(text="Factory pilot action failed")

    app.router.add_get("/admin/factory-pilot", factory_pilot_page)
    app.router.add_post("/admin/factory-pilot/actions/{action}", factory_pilot_action)
    setup_factory_pilot_preflight_admin_routes(
        app,
        guard=guard,
        layout=layout,
        key=key,
    )
    setup_factory_pilot_acceptance_admin_routes(
        app,
        guard=guard,
        layout=layout,
        key=key,
        request_id=request_id,
    )
    app["velia_factory_pilot_admin_routes_installed"] = True
