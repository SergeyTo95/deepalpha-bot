from __future__ import annotations

import asyncio
import html
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import quote_plus

from aiohttp import web

from services import velia_software_factory_admin_acceptance_service as acceptance
from services.velia_admin_security_service import record_admin_audit
from services.velia_software_factory_core_service import SoftwareFactoryError

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


def _badge(value: bool, yes: str = "Ready", no: str = "Blocked") -> str:
    return f"<b class='{'good-text' if value else 'bad-text'}'>{_e(yes if value else no)}</b>"


def _items(values: list[str], empty: str) -> str:
    if not values:
        return f"<div class='good-text'>{_e(empty)}</div>"
    return "<ul>" + "".join(f"<li><code>{_e(item)}</code></li>" for item in values) + "</ul>"


def _field(name: str, label: str, value: str = "") -> str:
    return f"<label>{_e(label)}<input name='{_e(name)}' value='{_e(value)}' autocomplete='off' required></label>"


def _redirect(run_id: str, repository: str, message: str) -> web.HTTPFound:
    return web.HTTPFound(
        "/admin/factory-pilot/acceptance?run_id="
        + quote_plus(run_id)
        + "&repository="
        + quote_plus(repository)
        + "&msg="
        + quote_plus(message)
    )


def _render(status: Dict[str, Any], inspection: Optional[Dict[str, Any]], run_id: str, repository: str, error: str) -> str:
    blockers = [str(item) for item in (status.get("blockers") or [])]
    remediation_state = dict(status.get("remediation") or {})
    header = f"""
<div class='grid'>
<div class='card'><div class='label'>Stage 6.7</div><div class='value'>{_badge(bool(status.get('enabled')), 'Enabled', 'Disabled')}</div><div class='hint'>External flag only; this page cannot change rollout settings.</div></div>
<div class='card'><div class='label'>Acceptance readiness</div><div class='value'>{_badge(bool(status.get('ready_now')))}</div><div class='hint'>All live-pilot, Reviewer and remediation prerequisites must already be ready.</div></div>
<div class='card'><div class='label'>Reviewer remediation</div><div class='value'>{_badge(bool(remediation_state.get('ready')))}</div><div class='hint'>At least one remediation attempt must be observed for a passing certificate.</div></div>
<div class='card'><div class='label'>Write budget</div><div class='value'>1 dispatch</div><div class='hint'>The Stage 6.2 persistent one-shot grant remains authoritative.</div></div>
<div class='card wide'><h2>Readiness blockers</h2>{_items(blockers, 'No blockers')}</div>
<div class='card wide'><h2>Safety boundary</h2><div class='muted'>Admin only · CSRF protected · exact run/repository/spec/grant binding · explicit typed confirmations · no automatic rollout change · no merge · no deployment. The persistent one-shot grant is the acceptance session ID.</div></div>
<div class='card full'><h2>Inspect acceptance session</h2>
<form class='inline' method='get' action='/admin/factory-pilot/acceptance'>
{_field('run_id', 'Factory run ID', run_id)}
{_field('repository', 'Repository owner/name', repository)}
<button class='primary' type='submit'>Inspect</button>
</form></div>
"""
    if error:
        return header + f"<div class='card full'><div class='bad-text'>{_e(error)}</div></div></div>"
    if not inspection:
        return header + "<div class='card full'><div class='muted'>Inspect an exact Factory run before arming acceptance.</div></div></div>"

    session = dict(inspection.get("acceptance") or {})
    evidence = dict(inspection.get("evidence") or {})
    certificate = dict(inspection.get("certificate") or {})
    grant = dict(inspection.get("grant") or {})
    acceptance_id = str(session.get("acceptance_id") or "")
    expected_arm = str(inspection.get("expected_arm_confirmation") or "")
    expected_dispatch = str(inspection.get("expected_dispatch_confirmation") or "")
    foreign = bool(inspection.get("foreign_grant_present"))

    body = f"""
<div class='card wide'><h2>Acceptance session</h2><div class='action-box'>
<div><span class='label'>Armed</span><br>{_badge(bool(session.get('armed')), 'Yes', 'No')}</div>
<div><span class='label'>Acceptance ID</span><br><code>{_e(acceptance_id or '—')}</code></div>
<div><span class='label'>Grant status</span><br><code>{_e(session.get('grant_status') or '—')}</code></div>
<div><span class='label'>Outcome</span><br><code>{_e(session.get('outcome') or '—')}</code></div>
<div><span class='label'>Terminal</span><br>{_badge(bool(session.get('terminal')), 'Yes', 'No')}</div>
<div><span class='label'>Passed</span><br>{_badge(bool(session.get('acceptance_passed')), 'Yes', 'No')}</div>
</div>{"<div class='bad-text'>A non-acceptance pilot grant already owns this run.</div>" if foreign else ""}</div>

<div class='card wide'><h2>Observed chain</h2><div class='action-box'>
<div><span class='label'>Autopilot task</span><br><code>{_e(evidence.get('autopilot_task_id') or '—')}</code></div>
<div><span class='label'>Autopilot run</span><br><code>{_e(evidence.get('autopilot_run_id') or '—')}</code></div>
<div><span class='label'>Run status</span><br><code>{_e(evidence.get('run_status') or '—')}</code></div>
<div><span class='label'>Reviewer</span><br><code>{_e(evidence.get('reviewer_status') or '—')}</code></div>
<div><span class='label'>Remediation phase</span><br><code>{_e(evidence.get('remediation_phase') or '—')}</code></div>
<div><span class='label'>Remediation attempts</span><br>{_e(evidence.get('remediation_attempt_count') or 0)}</div>
<div><span class='label'>Final reviewed head</span><br><code>{_e(evidence.get('reviewed_head_sha') or '—')}</code></div>
</div></div>

<div class='card full'><h2>Read-only acceptance certificate</h2><div class='action-box'>
<div><span class='label'>Issued</span><br>{_badge(bool(certificate.get('issued')), 'Yes', 'No')}</div>
<div><span class='label'>Certificate ID</span><br><code>{_e(certificate.get('certificate_id') or '—')}</code></div>
<div><span class='label'>Outcome</span><br><code>{_e(certificate.get('outcome') or '—')}</code></div>
<div><span class='label'>Merge authority</span><br>{_badge(bool(certificate.get('merge_authority')), 'Yes', 'No')}</div>
<div><span class='label'>Deployment authority</span><br>{_badge(bool(certificate.get('deployment_authority')), 'Yes', 'No')}</div>
</div></div>

<div class='card wide'><h2>1. Arm acceptance</h2><form method='post' action='/admin/factory-pilot/acceptance/actions/arm'>
{_field('run_id', 'Run ID', run_id)}{_field('repository', 'Exact repository', repository)}
<div class='hint'>Type exactly: <code>{_e(expected_arm)}</code></div>{_field('confirmation', 'Type exact acceptance confirmation')}
<label class='confirm'><input type='checkbox' name='confirmed' value='yes' required> I confirm this exact Stage 6.7 acceptance session</label>
<button class='primary' type='submit' data-confirm='Arm one controlled acceptance session?'>Arm acceptance</button></form></div>

<div class='card wide'><h2>2. Revoke pending acceptance</h2><form method='post' action='/admin/factory-pilot/acceptance/actions/revoke'>
{_field('run_id', 'Run ID', run_id)}{_field('repository', 'Exact repository', repository)}
<label class='confirm'><input type='checkbox' name='confirmed' value='yes' required> I confirm revoke</label>
<button class='danger' type='submit' data-confirm='Revoke this pending acceptance grant?'>Revoke acceptance</button></form>
<div class='hint'>Revocation remains available after acceptance/live flags close.</div></div>

<div class='card full'><h2>3. Dispatch exactly one acceptance task</h2><form method='post' action='/admin/factory-pilot/acceptance/actions/dispatch'>
{_field('run_id', 'Run ID', run_id)}{_field('repository', 'Exact repository', repository)}{_field('grant_id', 'Acceptance / grant ID', acceptance_id)}
<div class='hint'>Type exactly: <code>{_e(expected_dispatch or 'Arm acceptance first')}</code></div>{_field('confirmation', 'Type exact acceptance dispatch confirmation')}
<label class='confirm'><input type='checkbox' name='confirmed' value='yes' required> I confirm one live Coding Autopilot dispatch for controlled acceptance</label>
<button class='danger' type='submit' data-confirm='Dispatch exactly one controlled acceptance task?'>Dispatch acceptance</button></form></div>
"""
    return header + body + "</div>"


def setup_factory_pilot_acceptance_admin_routes(
    app: web.Application,
    *,
    guard: GuardFn,
    layout: LayoutFn,
    key: KeyFn,
    request_id: RequestIdFn,
) -> None:
    if app.get("velia_factory_pilot_acceptance_routes_installed"):
        return

    async def acceptance_page(request: web.Request) -> web.Response:
        denied = await guard(request)
        if denied is not None:
            return denied
        actor = _admin_id(request)
        status = await asyncio.to_thread(acceptance.public_status, actor)
        run_id = str(request.query.get("run_id", "") or "").strip()[:160]
        repository = str(request.query.get("repository", "") or "").strip()[:240]
        inspection: Optional[Dict[str, Any]] = None
        error = ""
        if run_id or repository:
            if not run_id or not repository:
                error = "Both run ID and repository are required."
            else:
                try:
                    inspection = await asyncio.to_thread(
                        acceptance.inspect_acceptance, actor, run_id, repository
                    )
                except SoftwareFactoryError as exc:
                    error = str(exc.code or "velia_factory_admin_acceptance_inspection_failed")
        return web.Response(
            text=layout(
                "Factory Acceptance",
                "Factory Acceptance",
                key(request),
                _render(status, inspection, run_id, repository, error),
                str(request.query.get("msg", "") or "")[:240],
            ),
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    async def acceptance_action(request: web.Request) -> web.StreamResponse:
        denied = await guard(request)
        if denied is not None:
            return denied
        actor = _admin_id(request)
        form = await request.post()
        action = str(request.match_info.get("action") or "").strip().lower()
        if action not in {"arm", "revoke", "dispatch"}:
            raise web.HTTPNotFound()
        run_id = str(form.get("run_id", "") or "").strip()[:160]
        repository = str(form.get("repository", "") or "").strip()[:240]
        grant_id = str(form.get("grant_id", "") or "").strip()[:160]
        confirmation = str(form.get("confirmation", "") or "").strip()[:800]
        if not run_id or not repository:
            raise web.HTTPBadRequest(text="Run ID and repository are required")
        metadata = {
            "admin_user_id": actor or None,
            "request_id": request_id(request),
            "source": "web",
            "ip": request.remote or "",
            "user_agent": request.headers.get("User-Agent", ""),
        }
        if str(form.get("confirmed", "") or "") != "yes":
            await asyncio.to_thread(
                record_admin_audit,
                **metadata,
                action=f"factory_acceptance.{action}",
                target_type="software_factory_run",
                target_id=run_id,
                before={"repository_full_name": repository, "grant_id": grant_id or None},
                success=False,
                error_code="explicit_confirmation_required",
            )
            raise web.HTTPBadRequest(text="Explicit confirmation required")
        try:
            if action == "arm":
                result = await asyncio.to_thread(
                    acceptance.arm_acceptance, actor, run_id, repository, confirmation
                )
            elif action == "revoke":
                result = await asyncio.to_thread(
                    acceptance.revoke_acceptance, actor, run_id, repository
                )
            else:
                result = await asyncio.to_thread(
                    acceptance.dispatch_acceptance,
                    actor,
                    run_id,
                    repository,
                    grant_id,
                    confirmation,
                )
            session = dict((result or {}).get("acceptance") or {})
            grant = dict((result or {}).get("grant") or {})
            await asyncio.to_thread(
                record_admin_audit,
                **metadata,
                action=f"factory_acceptance.{action}",
                target_type="software_factory_run",
                target_id=run_id,
                before={"repository_full_name": repository, "grant_id": grant_id or None},
                after={
                    "repository_full_name": repository,
                    "acceptance_id": session.get("acceptance_id") or grant.get("grant_id"),
                    "grant_status": grant.get("status") or session.get("status"),
                    "autopilot_task_id": grant.get("autopilot_task_id"),
                },
                success=True,
            )
            return _redirect(run_id, repository, f"{action.title()} acceptance action completed and audited")
        except SoftwareFactoryError as exc:
            code = str(exc.code or "velia_factory_admin_acceptance_action_blocked")
            await asyncio.to_thread(
                record_admin_audit,
                **metadata,
                action=f"factory_acceptance.{action}",
                target_type="software_factory_run",
                target_id=run_id,
                before={"repository_full_name": repository, "grant_id": grant_id or None},
                success=False,
                error_code=code,
            )
            return _redirect(run_id, repository, f"Blocked: {code}")

    app.router.add_get("/admin/factory-pilot/acceptance", acceptance_page)
    app.router.add_post("/admin/factory-pilot/acceptance/actions/{action}", acceptance_action)
    app["velia_factory_pilot_acceptance_routes_installed"] = True
