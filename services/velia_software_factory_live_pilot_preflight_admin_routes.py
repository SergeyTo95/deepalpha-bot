from __future__ import annotations

import asyncio
import html
from typing import Any, Awaitable, Callable, Dict, Optional

from aiohttp import web

from services import velia_software_factory_live_pilot_preflight_service as preflight
from services.velia_software_factory_core_service import SoftwareFactoryError


GuardFn = Callable[[web.Request], Awaitable[Optional[web.StreamResponse]]]
LayoutFn = Callable[[str, str, str, str, str], str]
KeyFn = Callable[[web.Request], str]


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _admin_id(request: web.Request) -> int:
    session = request.get("velia_admin_session") or {}
    try:
        return int(session.get("admin_user_id") or 0)
    except (TypeError, ValueError):
        return 0


def _badge(value: bool, yes: str, no: str) -> str:
    cls = "good-text" if value else "bad-text"
    return f"<b class='{cls}'>{_e(yes if value else no)}</b>"


def _items(values: list[str], empty: str) -> str:
    if not values:
        return f"<div class='good-text'>{_e(empty)}</div>"
    return "<ul>" + "".join(f"<li><code>{_e(value)}</code></li>" for value in values) + "</ul>"


def _render(result: Optional[Dict[str, Any]], run_id: str, repository: str, error: str) -> str:
    form = f"""
<div class='card full'>
  <h2>Read-only candidate inspection</h2>
  <form class='inline' method='get' action='/admin/factory-pilot/preflight'>
    <label>Run ID<input name='run_id' value='{_e(run_id)}' autocomplete='off' required></label>
    <label>Repository owner/name<input name='repository' value='{_e(repository)}' autocomplete='off' required></label>
    <button class='primary' type='submit'>Run preflight</button>
  </form>
  <div class='hint'>GET only · no grant read/issue · no dispatch · no environment mutation.</div>
</div>"""
    if error:
        return f"<div class='grid'>{form}<div class='card full'><h2>Preflight error</h2><div class='bad-text'>{_e(error)}</div></div></div>"
    if not result:
        return f"<div class='grid'>{form}<div class='card full'><div class='muted'>Enter the exact Factory run and repository to inspect the candidate while execution gates remain closed.</div></div></div>"

    candidate = dict(result.get("candidate") or {})
    runtime = dict(result.get("runtime") or {})
    candidate_blockers = [str(x) for x in (result.get("candidate_blockers") or [])]
    runtime_blockers = [str(x) for x in (result.get("runtime_blockers") or [])]
    missing_flags = [str(x) for x in (runtime.get("missing_build_review_flags") or [])]
    allowed_paths = [str(x) for x in (candidate.get("allowed_paths") or [])]
    dispatched = [str(x) for x in (candidate.get("dispatched_external_refs") or [])]

    candidate_safe = bool(result.get("candidate_safe_to_arm_when_runtime_ready"))
    runtime_ready = bool(result.get("runtime_ready_now"))
    ready_now = bool(result.get("pilot_candidate_ready_now"))

    details = f"""
<div class='card'><div class='label'>Candidate safety</div><div class='value'>{_badge(candidate_safe, 'Safe', 'Blocked')}</div><div class='hint'>Intrinsic run/repository/spec checks only.</div></div>
<div class='card'><div class='label'>Runtime readiness</div><div class='value'>{_badge(runtime_ready, 'Ready', 'Closed')}</div><div class='hint'>Current production gates; this page cannot change them.</div></div>
<div class='card'><div class='label'>Ready now</div><div class='value'>{_badge(ready_now, 'Yes', 'No')}</div><div class='hint'>Requires both candidate safety and runtime readiness.</div></div>
<div class='card'><div class='label'>Max dispatches</div><div class='value'>{_e(runtime.get('max_dispatches_per_run') or 1)}</div><div class='hint'>One-shot boundary remains authoritative.</div></div>

<div class='card wide'><h2>Exact candidate</h2><div class='action-box'>
<div><span class='label'>Run</span><br><code>{_e(candidate.get('run_id') or 'Unavailable')}</code></div>
<div><span class='label'>Project</span><br><code>{_e(candidate.get('project_id') or 'Unavailable')}</code></div>
<div><span class='label'>Repository</span><br><code>{_e(candidate.get('repository_full_name') or 'Unavailable')}</code></div>
<div><span class='label'>Repository match</span><br>{_badge(bool(candidate.get('repository_matches')), 'Exact', 'Mismatch')}</div>
<div><span class='label'>State</span><br><code>{_e(candidate.get('state') or 'Unavailable')}</code></div>
<div><span class='label'>Spec fingerprint</span><br><code>{_e(candidate.get('spec_fingerprint') or 'Unavailable')}</code></div>
</div></div>

<div class='card wide'><h2>Current runtime</h2><div class='action-box'>
<div><span class='label'>Control</span><br>{_badge(bool(runtime.get('control_enabled')), 'Enabled', 'Disabled')}</div>
<div><span class='label'>One-shot guard</span><br>{_badge(bool(runtime.get('guard_enabled')), 'Enabled', 'Disabled')}</div>
<div><span class='label'>Rollout mode</span><br><code>{_e(runtime.get('rollout_mode') or 'off')}</code></div>
<div><span class='label'>Eligibility</span><br><code>{_e(runtime.get('eligibility_source') or 'none')}</code></div>
<div><span class='label'>Build/review</span><br>{_badge(bool(runtime.get('build_review_ready')), 'Ready', 'Not ready')}</div>
</div></div>

<div class='card wide'><h2>Allowed write scope</h2>{_items(allowed_paths, 'No allowed paths')}</div>
<div class='card wide'><h2>Existing dispatched refs</h2>{_items(dispatched, 'No external dispatch detected')}</div>
<div class='card wide'><h2>Candidate blockers</h2>{_items(candidate_blockers, 'No intrinsic candidate blockers')}</div>
<div class='card wide'><h2>Runtime blockers</h2>{_items(runtime_blockers, 'No runtime blockers')}</div>
<div class='card full'><h2>Missing build/review flags</h2>{_items(missing_flags, 'None')}</div>
<div class='card full'><div class='muted'>This page is diagnostic only. It has no POST routes, does not inspect the pilot grant table, cannot arm or dispatch a task, and cannot change Railway or Software Factory rollout settings.</div></div>
"""
    return f"<div class='grid'>{form}{details}</div>"


def setup_factory_pilot_preflight_admin_routes(
    app: web.Application,
    *,
    guard: GuardFn,
    layout: LayoutFn,
    key: KeyFn,
) -> None:
    if app.get("velia_factory_pilot_preflight_admin_routes_installed"):
        return

    async def factory_pilot_preflight_page(request: web.Request) -> web.Response:
        denied = await guard(request)
        if denied is not None:
            return denied

        run_id = str(request.query.get("run_id", "") or "").strip()[:160]
        repository = str(request.query.get("repository", "") or "").strip()[:240]
        result: Optional[Dict[str, Any]] = None
        error = ""
        if run_id or repository:
            if not run_id or not repository:
                error = "Both run ID and repository are required."
            else:
                try:
                    result = await asyncio.to_thread(
                        preflight.preflight_candidate,
                        _admin_id(request),
                        run_id,
                        repository,
                    )
                except SoftwareFactoryError as exc:
                    error = str(exc.code or "velia_factory_live_pilot_preflight_failed")

        body = _render(result, run_id, repository, error)
        return web.Response(
            text=layout(
                "Factory Pilot Preflight",
                "Pilot Preflight",
                key(request),
                body,
                "",
            ),
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    app.router.add_get("/admin/factory-pilot/preflight", factory_pilot_preflight_page)
    app["velia_factory_pilot_preflight_admin_routes_installed"] = True
