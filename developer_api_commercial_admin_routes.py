from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any, Dict

from aiohttp import web

import developer_api_admin_routes as admin_routes
from admin_routes import _guard
from services.developer_api_commercial_service import (
    ApiCommercialError,
    get_commercial_runtime_health,
    list_all_credit_invoices,
    list_credit_packages,
    list_live_access_requests,
    refresh_owned_invoice,
    review_live_access,
    scan_commercial_payments_once,
    upsert_credit_package,
)


def _ton_to_nano(value: Any) -> int:
    try:
        amount = Decimal(str(value or "0").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ApiCommercialError("invalid_package_price") from exc
    nano = int((amount * Decimal(1_000_000_000)).to_integral_value())
    if nano <= 0:
        raise ApiCommercialError("invalid_package_price")
    return nano


def _pill(value: Any) -> str:
    status = str(value or "unknown")
    css = "success" if status in {"paid", "approved", "operational", "active"} else "danger" if status in {"rejected", "expired", "cancelled", "degraded"} else ""
    return f"<span class='pill {css}'>{escape(status)}</span>"


def _package_row(item: Dict[str, Any]) -> str:
    checked = "checked" if bool(item.get("enabled")) else ""
    price = Decimal(int(item.get("price_nano") or 0)) / Decimal(1_000_000_000)
    code = escape(str(item.get("package_code") or ""))
    return f"""
    <tr><td><code>{code}</code></td><td colspan='6'>
      <form method='post' action='/admin/api/commercial/packages/{code}' class='grid'>
        <label>Name<br><input name='display_name' value='{escape(str(item.get('display_name') or ''))}' maxlength='120' required></label>
        <label>Credits<br><input name='credits' value='{int(item.get('credits') or 0)}' inputmode='numeric' required></label>
        <label>Price TON<br><input name='price_ton' value='{price}' inputmode='decimal' required></label>
        <label>Sort<br><input name='sort_order' value='{int(item.get('sort_order') or 0)}' inputmode='numeric'></label>
        <label><br><input type='checkbox' name='enabled' {checked}> enabled</label>
        <div><br><button>Save package</button></div>
      </form>
    </td><td>{escape(str(item.get('updated_at') or ''))}</td></tr>
    """


def _request_row(item: Dict[str, Any]) -> str:
    client_id = int(item.get("client_id") or 0)
    actions = "—"
    if str(item.get("status") or "") == "pending":
        actions = f"""
        <div class='button-row'>
          <form method='post' action='/admin/api/commercial/live/{client_id}/approve'>
            <input name='note' value='Approved for live API access' maxlength='1000'>
            <button>Approve</button>
          </form>
          <form method='post' action='/admin/api/commercial/live/{client_id}/reject'>
            <input name='note' value='Additional review required' maxlength='1000'>
            <button class='danger'>Reject</button>
          </form>
        </div>
        """
    return (
        "<tr>"
        f"<td><code>{escape(str(item.get('request_id') or ''))}</code></td>"
        f"<td>#{client_id} {escape(str(item.get('client_name') or ''))}</td>"
        f"<td>{int(item.get('user_id') or 0)}</td>"
        f"<td>{_pill(item.get('status'))}</td>"
        f"<td>{int(item.get('expected_monthly_requests') or 0)}</td>"
        f"<td class='truncate-4'>{escape(str(item.get('use_case') or ''))}</td>"
        f"<td>{escape(str(item.get('created_at') or ''))}</td>"
        f"<td>{actions}</td>"
        "</tr>"
    )


def _invoice_row(item: Dict[str, Any]) -> str:
    refresh = ""
    if str(item.get("status") or "") in {"pending", "expired"}:
        refresh = "<form method='post' action='/admin/api/commercial/scan'><button>Scan TON</button></form>"
    return (
        "<tr>"
        f"<td><code>{escape(str(item.get('invoice_id') or ''))}</code></td>"
        f"<td>#{int(item.get('client_id') or 0)} {escape(str(item.get('client_name') or ''))}</td>"
        f"<td>{int(item.get('user_id') or 0)}</td>"
        f"<td>{escape(str(item.get('package_code') or ''))}</td>"
        f"<td>{int(item.get('credits') or 0)}</td>"
        f"<td>{escape(str(item.get('price_ton') or '0'))} TON</td>"
        f"<td>{_pill(item.get('status'))}</td>"
        f"<td><code>{escape(str(item.get('public_reference') or ''))}</code></td>"
        f"<td class='truncate-4'>{escape(str(item.get('tx_hash') or item.get('last_error') or '—'))}</td>"
        f"<td>{escape(str(item.get('created_at') or ''))}</td>"
        f"<td>{refresh}</td>"
        "</tr>"
    )


def _commercial_dashboard() -> str:
    runtime = get_commercial_runtime_health(include_workers=True)
    packages = list_credit_packages(include_disabled=True)
    requests = list_live_access_requests(limit=100)
    invoices = list_all_credit_invoices(limit=100)
    warnings = "".join(
        f"<div class='card danger'><b>Commercial warning:</b> {escape(str(item).replace('_', ' '))}</div>"
        for item in runtime.get("warnings") or []
    )
    worker_rows = "".join(
        "<tr>"
        f"<td><code>{escape(str(item.get('worker_id') or ''))}</code></td>"
        f"<td>{_pill(item.get('status'))}</td>"
        f"<td>{'fresh' if item.get('fresh') else 'stale'}</td>"
        f"<td>{escape(str(item.get('last_seen_at') or ''))}</td>"
        "</tr>"
        for item in runtime.get("workers") or []
    )
    package_rows = "".join(_package_row(item) for item in packages)
    request_rows = "".join(_request_row(item) for item in requests)
    invoice_rows = "".join(_invoice_row(item) for item in invoices)
    return f"""
    <div class='card'>
      <h3>API commercial launch</h3>
      <div class='grid'>
        <div>Status <b>{escape(str(runtime.get('status') or 'unknown'))}</b></div>
        <div>Launch enabled <b>{'yes' if runtime.get('enabled') else 'no'}</b></div>
        <div>Live keys <b>{'yes' if runtime.get('live_keys_enabled') else 'no'}</b></div>
        <div>Treasury incoming <b>{'yes' if runtime.get('treasury_incoming_enabled') else 'no'}</b></div>
        <div>Network <b>{escape(str(runtime.get('network') or ''))}</b></div>
        <div>Fresh workers <b>{int(runtime.get('fresh_workers') or 0)}</b></div>
        <div>Pending invoices <b>{int(runtime.get('pending_invoices') or 0)}</b></div>
        <div>Paid 24h <b>{int(runtime.get('paid_24h') or 0)}</b></div>
        <div>Credits sold 24h <b>{int(runtime.get('credits_sold_24h') or 0)}</b></div>
        <div>Live requests <b>{int(runtime.get('pending_live_requests') or 0)}</b></div>
      </div>
      <form method='post' action='/admin/api/commercial/scan'><button>Scan TON payments now</button></form>
    </div>
    {warnings}
    <div class='card'><h3>Commercial worker</h3><div class='table-scroll'><table>
      <tr><th>Worker</th><th>Status</th><th>Heartbeat</th><th>Last seen</th></tr>
      {worker_rows or '<tr><td colspan=4 class=muted>No commercial worker heartbeat.</td></tr>'}
    </table></div></div>
    <div class='card'><h3>Create API credit package</h3>
      <form method='post' action='/admin/api/commercial/packages/create' class='grid'>
        <label>Code<br><input name='package_code' placeholder='starter_100' required></label>
        <label>Name<br><input name='display_name' placeholder='Starter 100' required></label>
        <label>Credits<br><input name='credits' value='100' inputmode='numeric' required></label>
        <label>Price TON<br><input name='price_ton' inputmode='decimal' placeholder='0.5' required></label>
        <label>Sort<br><input name='sort_order' value='0' inputmode='numeric'></label>
        <label><br><input type='checkbox' name='enabled' checked> enabled</label>
        <div><br><button>Create package</button></div>
      </form>
      <p class='muted'>No package price is created automatically. Configure the commercial price explicitly here.</p>
    </div>
    <div class='card'><h3>Credit packages</h3><div class='table-scroll'><table>
      <tr><th>Code</th><th colspan=6>Configuration</th><th>Updated</th></tr>
      {package_rows or '<tr><td colspan=8 class=muted>No configured credit packages.</td></tr>'}
    </table></div></div>
    <div class='card'><h3>Live access requests</h3><div class='table-scroll'><table>
      <tr><th>Request</th><th>Project</th><th>User</th><th>Status</th><th>Expected/month</th><th>Use case</th><th>Created</th><th>Actions</th></tr>
      {request_rows or '<tr><td colspan=8 class=muted>No live access requests.</td></tr>'}
    </table></div></div>
    <div class='card'><h3>API credit invoices</h3><div class='table-scroll'><table>
      <tr><th>Invoice</th><th>Project</th><th>User</th><th>Package</th><th>Credits</th><th>Price</th><th>Status</th><th>Reference</th><th>Tx / error</th><th>Created</th><th></th></tr>
      {invoice_rows or '<tr><td colspan=11 class=muted>No API credit invoices.</td></tr>'}
    </table></div></div>
    """


def install() -> None:
    original = admin_routes.admin_developer_api
    if getattr(original, "_deepalpha_commercial_admin", False):
        return

    async def admin_with_commercial(request):
        response = await original(request)
        if response.status != 200 or not str(response.content_type or "").startswith("text/html"):
            return response
        try:
            dashboard = _commercial_dashboard()
        except Exception as exc:
            dashboard = f"<div class='card danger'><b>Commercial dashboard unavailable:</b> {escape(type(exc).__name__)}</div>"
        text = response.text or ""
        marker = "</div></body></html>"
        response.text = text.replace(marker, dashboard + marker, 1) if marker in text else text + dashboard
        return response

    admin_with_commercial._deepalpha_commercial_admin = True
    admin_with_commercial._deepalpha_original = original
    admin_routes.admin_developer_api = admin_with_commercial


async def admin_upsert_package(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    form = await request.post()
    code = str(request.match_info.get("package_code") or form.get("package_code") or "")
    try:
        package = upsert_credit_package(
            package_code=code,
            display_name=str(form.get("display_name") or code),
            credits=int(str(form.get("credits") or "0")),
            price_nano=_ton_to_nano(form.get("price_ton")),
            enabled=str(form.get("enabled") or "").lower() in {"on", "true", "1"},
            sort_order=int(str(form.get("sort_order") or "0")),
            actor="admin",
        )
        return admin_routes._message_redirect(f"Credit package {package.get('package_code')} saved")
    except Exception as exc:
        return admin_routes._message_redirect(str(getattr(exc, "code", str(exc))))


async def admin_approve_live(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    form = await request.post()
    try:
        review_live_access(
            client_id=int(request.match_info.get("client_id") or 0),
            approved=True,
            actor="admin",
            note=str(form.get("note") or "Approved"),
        )
        return admin_routes._message_redirect("Live API access approved")
    except Exception as exc:
        return admin_routes._message_redirect(str(getattr(exc, "code", str(exc))))


async def admin_reject_live(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    form = await request.post()
    try:
        review_live_access(
            client_id=int(request.match_info.get("client_id") or 0),
            approved=False,
            actor="admin",
            note=str(form.get("note") or "Rejected"),
        )
        return admin_routes._message_redirect("Live API access rejected")
    except Exception as exc:
        return admin_routes._message_redirect(str(getattr(exc, "code", str(exc))))


async def admin_scan_payments(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    try:
        result = scan_commercial_payments_once(page_limit=100, max_pages=10)
        return admin_routes._message_redirect(
            f"Commercial scan: paid={int(result.get('paid') or 0)}, references={int(result.get('references_seen') or 0)}, error={result.get('error') or 'none'}"
        )
    except Exception as exc:
        return admin_routes._message_redirect(type(exc).__name__)


def setup_developer_api_commercial_admin_routes(app: web.Application) -> None:
    if app.get("developer_api_commercial_admin_routes_installed"):
        return
    app.router.add_post("/admin/api/commercial/packages/create", admin_upsert_package)
    app.router.add_post("/admin/api/commercial/packages/{package_code}", admin_upsert_package)
    app.router.add_post("/admin/api/commercial/live/{client_id}/approve", admin_approve_live)
    app.router.add_post("/admin/api/commercial/live/{client_id}/reject", admin_reject_live)
    app.router.add_post("/admin/api/commercial/scan", admin_scan_payments)
    app["developer_api_commercial_admin_routes_installed"] = True
