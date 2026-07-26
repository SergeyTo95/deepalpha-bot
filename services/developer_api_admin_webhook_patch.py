from html import escape
from typing import Any, Dict, List

import developer_api_admin_routes as admin_routes
from db.database import get_connection
from services.developer_api_webhook_service import (
    ensure_api_webhook_tables,
    get_webhook_runtime_health,
)


def _row_to_dict(cursor, row) -> Dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    columns = [item[0] for item in (cursor.description or [])]
    return dict(zip(columns, row))


def _recent_data() -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    ensure_api_webhook_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT w.webhook_id, w.client_id, c.name AS client_name, w.name, w.url,
                   w.events, w.status, w.consecutive_failures,
                   w.last_success_at, w.last_failure_at, w.created_at
            FROM api_webhooks w
            JOIN api_clients c ON c.id=w.client_id
            ORDER BY w.id DESC LIMIT 100
            """
        )
        webhooks = [_row_to_dict(cursor, row) for row in cursor.fetchall() or []]
        cursor.execute(
            """
            SELECT d.delivery_id, d.client_id, c.name AS client_name,
                   w.webhook_id, d.job_id, d.event, d.status, d.attempt_count,
                   d.manual_retry_count, d.response_status, d.last_error,
                   d.created_at, d.delivered_at, d.next_attempt_at
            FROM api_webhook_deliveries d
            JOIN api_webhooks w ON w.id=d.webhook_id
            JOIN api_clients c ON c.id=d.client_id
            ORDER BY d.created_at DESC LIMIT 100
            """
        )
        deliveries = [_row_to_dict(cursor, row) for row in cursor.fetchall() or []]
        return webhooks, deliveries
    finally:
        cursor.close()
        conn.close()


def _pill(value: Any) -> str:
    status = str(value or "unknown")
    css = "success" if status in {"active", "succeeded", "idle"} else "danger" if status in {"disabled", "failed", "degraded"} else ""
    return f"<span class='pill {css}'>{escape(status)}</span>"


def _dashboard() -> str:
    runtime = get_webhook_runtime_health(include_workers=True)
    webhooks, deliveries = _recent_data()
    queue = runtime.get("queue") or {}
    recent = runtime.get("recent") or {}
    warning_html = "".join(
        f"<div class='card danger'><b>Webhook warning:</b> {escape(str(item).replace('_', ' '))}</div>"
        for item in runtime.get("warnings") or []
    )
    worker_rows = "".join(
        "<tr>"
        f"<td><code>{escape(str(item.get('worker_id') or ''))}</code></td>"
        f"<td>{_pill(item.get('status'))}</td>"
        f"<td>{'fresh' if item.get('fresh') else 'stale'}</td>"
        f"<td>{escape(str(item.get('current_delivery_id') or '—'))}</td>"
        f"<td>{escape(str(item.get('last_seen_at') or ''))}</td>"
        "</tr>"
        for item in runtime.get("workers") or []
    )
    webhook_rows = "".join(
        "<tr>"
        f"<td><code>{escape(str(item.get('webhook_id') or ''))}</code></td>"
        f"<td>#{int(item.get('client_id') or 0)} {escape(str(item.get('client_name') or ''))}</td>"
        f"<td>{escape(str(item.get('name') or ''))}</td>"
        f"<td class='truncate-4'>{escape(str(item.get('url') or ''))}</td>"
        f"<td>{escape(str(item.get('events') or ''))}</td>"
        f"<td>{_pill(item.get('status'))}</td>"
        f"<td>{int(item.get('consecutive_failures') or 0)}</td>"
        f"<td>{escape(str(item.get('last_success_at') or '—'))}</td>"
        "</tr>"
        for item in webhooks
    )
    delivery_rows = "".join(
        "<tr>"
        f"<td><code>{escape(str(item.get('delivery_id') or ''))}</code></td>"
        f"<td><code>{escape(str(item.get('webhook_id') or ''))}</code></td>"
        f"<td><code>{escape(str(item.get('job_id') or ''))}</code></td>"
        f"<td>{escape(str(item.get('event') or ''))}</td>"
        f"<td>{_pill(item.get('status'))}</td>"
        f"<td>{int(item.get('attempt_count') or 0)} + {int(item.get('manual_retry_count') or 0)} manual</td>"
        f"<td>{escape(str(item.get('response_status') or '—'))}</td>"
        f"<td class='truncate-4'>{escape(str(item.get('last_error') or '—'))}</td>"
        f"<td>{escape(str(item.get('created_at') or ''))}</td>"
        "</tr>"
        for item in deliveries
    )
    return f"""
    <div class='card'>
      <h3>Signed Webhooks runtime</h3>
      <div class='grid'>
        <div>Status <b>{escape(str(runtime.get('status') or 'unknown'))}</b></div>
        <div>Active endpoints <b>{int(runtime.get('active_webhooks') or 0)}</b></div>
        <div>Fresh workers <b>{int(runtime.get('fresh_workers') or 0)}</b></div>
        <div>Queued <b>{int(queue.get('queued') or 0)}</b></div>
        <div>Delivering <b>{int(queue.get('delivering') or 0)}</b></div>
        <div>Success 24h <b>{int(recent.get('succeeded_24h') or 0)}</b></div>
        <div>Failed 24h <b>{int(recent.get('failed_24h') or 0)}</b></div>
      </div>
    </div>
    {warning_html}
    <div class='card'><h3>Webhook workers</h3><div class='table-scroll'><table>
      <tr><th>Worker</th><th>Status</th><th>Heartbeat</th><th>Current delivery</th><th>Last seen</th></tr>
      {worker_rows or '<tr><td colspan=5 class=muted>No webhook worker heartbeat.</td></tr>'}
    </table></div></div>
    <div class='card'><h3>Webhook endpoints</h3><div class='table-scroll'><table>
      <tr><th>Webhook</th><th>Client</th><th>Name</th><th>URL</th><th>Events</th><th>Status</th><th>Failures</th><th>Last success</th></tr>
      {webhook_rows or '<tr><td colspan=8 class=muted>No webhook endpoints.</td></tr>'}
    </table></div></div>
    <div class='card'><h3>Webhook deliveries</h3><div class='table-scroll'><table>
      <tr><th>Delivery</th><th>Webhook</th><th>Job</th><th>Event</th><th>Status</th><th>Attempts</th><th>HTTP</th><th>Error</th><th>Created</th></tr>
      {delivery_rows or '<tr><td colspan=9 class=muted>No webhook deliveries.</td></tr>'}
    </table></div></div>
    """


def install() -> None:
    original = admin_routes.admin_developer_api
    if getattr(original, "_deepalpha_webhook_admin", False):
        return

    async def admin_api_with_webhooks(request):
        response = await original(request)
        if response.status != 200 or not str(response.content_type or "").startswith("text/html"):
            return response
        try:
            dashboard = _dashboard()
        except Exception as exc:
            dashboard = f"<div class='card danger'><b>Webhook dashboard unavailable:</b> {escape(type(exc).__name__)}</div>"
        text = response.text or ""
        marker = "</div></body></html>"
        response.text = text.replace(marker, dashboard + marker, 1) if marker in text else text + dashboard
        return response

    admin_api_with_webhooks._deepalpha_webhook_admin = True
    admin_api_with_webhooks._deepalpha_original = original
    admin_routes.admin_developer_api = admin_api_with_webhooks
