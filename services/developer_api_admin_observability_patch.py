from html import escape
from typing import Any, Dict, List, Optional

import developer_api_admin_routes as admin_routes
from services.developer_api_observability_service import (
    get_api_runtime_health,
    list_admin_api_jobs,
)


def _int_query(request, name: str) -> Optional[int]:
    value = str(request.query.get(name, "") or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _duration(value: Any) -> str:
    try:
        seconds = max(0, int(float(value or 0)))
    except Exception:
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remainder}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _status_pill(status: str) -> str:
    clean = str(status or "unknown").lower()
    tone = {
        "success": "success",
        "error": "danger",
        "running": "",
        "queued": "",
        "refund_pending": "danger",
        "idle": "success",
        "starting": "",
        "degraded": "danger",
        "stopped": "danger",
    }.get(clean, "")
    return f"<span class='pill {tone}'>{escape(clean)}</span>"


def _warning_text(code: str) -> str:
    return {
        "no_fresh_api_worker": "No fresh API worker heartbeat.",
        "api_queue_size_high": "Quick Analysis queue size is above the warning threshold.",
        "api_queue_wait_high": "Oldest queued analysis has waited too long.",
        "stale_running_jobs": "One or more running jobs have an expired lease.",
        "refunds_pending": "One or more jobs are waiting for a credit refund.",
    }.get(str(code), str(code).replace("_", " "))


def _job_row(job: Dict[str, Any]) -> str:
    market_url = escape(str(job.get("market_url") or ""))
    market = f"<a href='{market_url}' target='_blank' rel='noreferrer'>market</a>" if market_url else "—"
    decision = " ".join(
        part for part in [str(job.get("decision") or ""), str(job.get("side") or "")] if part
    ) or "—"
    error = escape(str(job.get("error") or ""))
    reservation = escape(str(job.get("reservation_status") or "—"))
    return (
        "<tr>"
        f"<td><code>{escape(str(job.get('job_id') or ''))}</code></td>"
        f"<td>#{int(job.get('client_id') or 0)} {escape(str(job.get('client_name') or ''))}</td>"
        f"<td>{_status_pill(str(job.get('status') or ''))}<br><span class='muted'>{int(job.get('progress') or 0)}%</span></td>"
        f"<td>{market}<br><span class='muted'>{escape(str(job.get('language') or ''))}</span></td>"
        f"<td>{escape(decision)}</td>"
        f"<td>{int(job.get('units_reserved') or 0)} / {int(job.get('units_charged') or 0)}<br><span class='muted'>{reservation}</span></td>"
        f"<td>{int(job.get('attempt_count') or 0)}</td>"
        f"<td>{_duration(job.get('duration_seconds'))}</td>"
        f"<td>{escape(str(job.get('created_at') or ''))}</td>"
        f"<td class='truncate-4'>{error or '—'}</td>"
        "</tr>"
    )


def _worker_row(worker: Dict[str, Any]) -> str:
    fresh = bool(worker.get("fresh"))
    heartbeat = _duration(worker.get("heartbeat_age_seconds"))
    return (
        "<tr>"
        f"<td><code>{escape(str(worker.get('worker_id') or ''))}</code></td>"
        f"<td>{_status_pill(str(worker.get('status') or ''))}</td>"
        f"<td>{'fresh' if fresh else '<span class=\"danger\">stale</span>'}</td>"
        f"<td>{heartbeat} ago</td>"
        f"<td><code>{escape(str(worker.get('current_job_id') or '—'))}</code></td>"
        f"<td>{escape(str(worker.get('started_at') or ''))}</td>"
        "</tr>"
    )


def _dashboard_html(request) -> str:
    status_filter = str(request.query.get("job_status", "") or "").strip().lower()
    if status_filter not in {"queued", "running", "success", "error", "refund_pending"}:
        status_filter = ""
    client_filter = _int_query(request, "client_id")
    runtime = get_api_runtime_health(include_workers=True)
    jobs = list_admin_api_jobs(limit=100, status=status_filter or None, client_id=client_filter)
    queue = runtime.get("queue") or {}
    recent = runtime.get("recent") or {}
    workers: List[Dict[str, Any]] = list(runtime.get("workers") or [])
    warnings = list(runtime.get("warnings") or [])

    warning_html = "".join(
        f"<div class='card danger'><b>Warning:</b> {escape(_warning_text(code))}</div>"
        for code in warnings
    )
    filters = ["", "queued", "running", "success", "error", "refund_pending"]
    filter_links = " ".join(
        f"<a class='pill' href='/admin/api?job_status={escape(value)}'>{escape(value or 'all')}</a>"
        for value in filters
    )
    job_rows = "".join(_job_row(job) for job in jobs)
    worker_rows = "".join(_worker_row(worker) for worker in workers)

    return f"""
    <div class='card'>
      <h3 style='margin-top:0'>Quick Analysis runtime</h3>
      <div class='grid'>
        <div>Status <b>{escape(str(runtime.get('status') or 'unknown'))}</b></div>
        <div>Fresh workers <b>{int(runtime.get('fresh_workers') or 0)}</b></div>
        <div>Queued <b>{int(queue.get('queued') or 0)}</b></div>
        <div>Running <b>{int(queue.get('running') or 0)}</b></div>
        <div>Stale running <b>{int(queue.get('stale_running') or 0)}</b></div>
        <div>Refund pending <b>{int(queue.get('refund_pending') or 0)}</b></div>
        <div>Oldest queue <b>{_duration(queue.get('oldest_queued_age_seconds'))}</b></div>
        <div>Avg duration 24h <b>{_duration(recent.get('avg_duration_seconds_24h'))}</b></div>
        <div>Success 24h <b>{int(recent.get('success_24h') or 0)}</b></div>
        <div>Errors 24h <b>{int(recent.get('error_24h') or 0)}</b></div>
      </div>
      <p class='muted'>Checked {escape(str(runtime.get('checked_at') or ''))}</p>
    </div>
    {warning_html}
    <div class='card'>
      <h3>API workers</h3>
      <div class='table-scroll'><table>
        <tr><th>Worker</th><th>Status</th><th>Heartbeat</th><th>Age</th><th>Current job</th><th>Started</th></tr>
        {worker_rows or '<tr><td colspan=6 class=muted>No worker heartbeat has been recorded.</td></tr>'}
      </table></div>
    </div>
    <div class='card'>
      <h3>Quick Analysis jobs</h3>
      <div style='margin-bottom:8px'>Filter: {filter_links}</div>
      <form method='get' action='/admin/api' class='field-row'>
        <input name='client_id' value='{escape(str(client_filter or ''))}' placeholder='Client ID'>
        <select name='job_status'>
          <option value=''>all statuses</option>
          {''.join(f"<option value='{value}' {'selected' if value == status_filter else ''}>{value}</option>" for value in filters if value)}
        </select>
        <button>Apply</button>
        <a class='pill' href='/admin/api'>Reset</a>
      </form>
      <div class='table-scroll'><table>
        <tr><th>Job</th><th>Client</th><th>Status</th><th>Market</th><th>Decision</th><th>Credits R/C</th><th>Attempts</th><th>Duration</th><th>Created</th><th>Error</th></tr>
        {job_rows or '<tr><td colspan=10 class=muted>No jobs match this filter.</td></tr>'}
      </table></div>
    </div>
    """


def install() -> None:
    original = admin_routes.admin_developer_api
    if getattr(original, "_deepalpha_api_observability", False):
        return

    async def admin_api_with_observability(request):
        response = await original(request)
        if response.status != 200 or not str(response.content_type or "").startswith("text/html"):
            return response
        try:
            dashboard = _dashboard_html(request)
        except Exception as exc:
            dashboard = (
                "<div class='card danger'><b>API observability unavailable:</b> "
                f"{escape(type(exc).__name__)}</div>"
            )
        text = response.text or ""
        text = text.replace(
            "Public analysis endpoints remain disabled until the execution worker is connected.",
            "Quick Analysis API is active. Jobs reserve credits, execute in the persistent worker, then charge or refund automatically.",
        )
        marker = "</div></body></html>"
        if marker in text:
            text = text.replace(marker, dashboard + marker, 1)
        else:
            text += dashboard
        response.text = text
        return response

    admin_api_with_observability._deepalpha_api_observability = True
    admin_api_with_observability._deepalpha_original = original
    admin_routes.admin_developer_api = admin_api_with_observability
