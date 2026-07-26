import json
from html import escape
from typing import Any, Dict, List

import developer_api_admin_routes as admin_routes
from db.database import get_connection
from services.developer_api_opportunity_service import (
    get_opportunity_runtime_health,
)


def _row_to_dict(cursor, row) -> Dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    columns = [item[0] for item in (cursor.description or [])]
    return dict(zip(columns, row))


def _parse_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _jobs() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT j.job_id, j.client_id, c.name AS client_name, j.status,
                   j.progress, j.attempt_count, j.worker_id,
                   j.units_reserved, j.units_charged, j.error,
                   j.created_at, j.started_at, j.finished_at,
                   j.request_json, j.result_json,
                   r.status AS reservation_status,
                   EXTRACT(EPOCH FROM (
                       COALESCE(j.finished_at, NOW()) - COALESCE(j.started_at, j.created_at)
                   )) AS duration_seconds
            FROM api_jobs j
            JOIN api_clients c ON c.id=j.client_id
            LEFT JOIN api_credit_reservations r ON r.job_id=j.job_id
            WHERE j.job_type='opportunity_scan'
            ORDER BY j.created_at DESC
            LIMIT 100
            """
        )
        rows: List[Dict[str, Any]] = []
        for raw in cursor.fetchall() or []:
            item = _row_to_dict(cursor, raw)
            request_payload = _parse_json(item.pop("request_json", "{}"))
            result_payload = _parse_json(item.pop("result_json", "{}"))
            item["category"] = request_payload.get("category") or "All"
            item["result_limit"] = request_payload.get("result_limit") or 0
            item["min_score"] = request_payload.get("min_score") or 0
            item["candidate_count"] = result_payload.get("candidate_count") or 0
            rows.append(item)
        return rows
    finally:
        cursor.close()
        conn.close()


def _pill(value: Any) -> str:
    status = str(value or "unknown")
    css = "success" if status in {"success", "idle"} else "danger" if status in {"error", "refund_pending", "degraded", "stopped"} else ""
    return f"<span class='pill {css}'>{escape(status)}</span>"


def _duration(value: Any) -> str:
    try:
        seconds = max(0, int(float(value or 0)))
    except Exception:
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds}s"


def _dashboard() -> str:
    runtime = get_opportunity_runtime_health(include_workers=True)
    queue = runtime.get("queue") or {}
    recent = runtime.get("recent") or {}
    warnings = runtime.get("warnings") or []
    warning_html = "".join(
        f"<div class='card danger'><b>Opportunity warning:</b> {escape(str(code).replace('_', ' '))}</div>"
        for code in warnings
    )
    worker_rows = "".join(
        "<tr>"
        f"<td><code>{escape(str(item.get('worker_id') or ''))}</code></td>"
        f"<td>{_pill(item.get('status'))}</td>"
        f"<td>{'fresh' if item.get('fresh') else 'stale'}</td>"
        f"<td><code>{escape(str(item.get('current_job_id') or '—'))}</code></td>"
        f"<td>{escape(str(item.get('last_seen_at') or ''))}</td>"
        "</tr>"
        for item in runtime.get("workers") or []
    )
    job_rows = "".join(
        "<tr>"
        f"<td><code>{escape(str(item.get('job_id') or ''))}</code></td>"
        f"<td>#{int(item.get('client_id') or 0)} {escape(str(item.get('client_name') or ''))}</td>"
        f"<td>{_pill(item.get('status'))}<br><span class='muted'>{int(item.get('progress') or 0)}%</span></td>"
        f"<td>{escape(str(item.get('category') or 'All'))}<br><span class='muted'>limit {int(item.get('result_limit') or 0)}, score ≥ {int(item.get('min_score') or 0)}</span></td>"
        f"<td>{int(item.get('candidate_count') or 0)}</td>"
        f"<td>{int(item.get('units_reserved') or 0)} / {int(item.get('units_charged') or 0)}<br><span class='muted'>{escape(str(item.get('reservation_status') or '—'))}</span></td>"
        f"<td>{int(item.get('attempt_count') or 0)}</td>"
        f"<td>{_duration(item.get('duration_seconds'))}</td>"
        f"<td>{escape(str(item.get('created_at') or ''))}</td>"
        f"<td class='truncate-4'>{escape(str(item.get('error') or '—'))}</td>"
        "</tr>"
        for item in _jobs()
    )
    return f"""
    <div class='card'>
      <h3>Opportunity Scan API runtime</h3>
      <div class='grid'>
        <div>Status <b>{escape(str(runtime.get('status') or 'unknown'))}</b></div>
        <div>Fresh workers <b>{int(runtime.get('fresh_workers') or 0)}</b></div>
        <div>Queued <b>{int(queue.get('queued') or 0)}</b></div>
        <div>Running <b>{int(queue.get('running') or 0)}</b></div>
        <div>Stale <b>{int(queue.get('stale_running') or 0)}</b></div>
        <div>Refund pending <b>{int(queue.get('refund_pending') or 0)}</b></div>
        <div>Success 24h <b>{int(recent.get('success_24h') or 0)}</b></div>
        <div>Errors 24h <b>{int(recent.get('error_24h') or 0)}</b></div>
        <div>Avg duration <b>{_duration(recent.get('avg_duration_seconds_24h'))}</b></div>
      </div>
    </div>
    {warning_html}
    <div class='card'><h3>Opportunity workers</h3><div class='table-scroll'><table>
      <tr><th>Worker</th><th>Status</th><th>Heartbeat</th><th>Current job</th><th>Last seen</th></tr>
      {worker_rows or '<tr><td colspan=5 class=muted>No Opportunity worker heartbeat.</td></tr>'}
    </table></div></div>
    <div class='card'><h3>Opportunity Scan jobs</h3><div class='table-scroll'><table>
      <tr><th>Job</th><th>Client</th><th>Status</th><th>Filters</th><th>Candidates</th><th>Credits R/C</th><th>Attempts</th><th>Duration</th><th>Created</th><th>Error</th></tr>
      {job_rows or '<tr><td colspan=10 class=muted>No Opportunity Scan API jobs.</td></tr>'}
    </table></div></div>
    """


def install() -> None:
    original = admin_routes.admin_developer_api
    if getattr(original, "_deepalpha_opportunity_admin", False):
        return

    async def admin_api_with_opportunity(request):
        response = await original(request)
        if response.status != 200 or not str(response.content_type or "").startswith("text/html"):
            return response
        try:
            dashboard = _dashboard()
        except Exception as exc:
            dashboard = f"<div class='card danger'><b>Opportunity dashboard unavailable:</b> {escape(type(exc).__name__)}</div>"
        text = response.text or ""
        marker = "</div></body></html>"
        response.text = text.replace(marker, dashboard + marker, 1) if marker in text else text + dashboard
        return response

    admin_api_with_opportunity._deepalpha_opportunity_admin = True
    admin_api_with_opportunity._deepalpha_original = original
    admin_routes.admin_developer_api = admin_api_with_opportunity
