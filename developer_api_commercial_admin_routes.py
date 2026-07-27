from html import escape
from typing import Any, Dict, List, Optional

from aiohttp import web

import developer_api_admin_routes as admin_routes
from admin_routes import _guard
from db.database import get_connection
from services.developer_api_commercial_launch_service import (
    CommercialLaunchError,
    cancel_invoice_admin,
    credit_invoice_admin,
    invoice_provider_name,
    list_all_invoices,
    list_credit_packages,
    list_live_requests,
    list_payment_events,
    list_recent_purchase_ledger,
    mark_invoice_paid_admin,
    review_live_access,
    scan_payments_once,
    upsert_credit_package,
)
from services.developer_api_commercial_service import get_commercial_runtime_health


def _pill(value: Any) -> str:
    status = str(value or "unknown")
    good = {"credited", "paid", "live_approved", "operational", "active"}
    bad = {"live_rejected", "live_suspended", "expired", "cancelled", "failed", "refunded", "degraded"}
    css = "success" if status in good else "danger" if status in bad else ""
    return f"<span class='pill {css}'>{escape(status)}</span>"


def _package_row(item: Dict[str, Any]) -> str:
    checked = "checked" if bool(item.get("enabled")) else ""
    code = escape(str(item.get("package_code") or ""))
    return f"""
    <tr><td><code>{code}</code></td><td colspan='7'>
      <form method='post' action='/admin/api/commercial/packages/{code}' class='grid'>
        <label>Name<br><input name='display_name' value='{escape(str(item.get('display_name') or ''))}' maxlength='120' required></label>
        <label>Credits<br><input name='credits' value='{int(item.get('credits') or 0)}' inputmode='numeric' required></label>
        <label>Price<br><input name='price_amount' value='{escape(str(item.get('price_amount') or ''))}' inputmode='decimal' required></label>
        <label>Currency<br><input name='price_currency' value='{escape(str(item.get('price_currency') or 'TON'))}' maxlength='12' required></label>
        <label>Sort<br><input name='sort_order' value='{int(item.get('sort_order') or 0)}' inputmode='numeric'></label>
        <label><br><input type='checkbox' name='enabled' {checked}> enabled</label>
        <div><br><button>Save package</button></div>
      </form>
    </td><td>{escape(str(item.get('updated_at') or ''))}</td></tr>
    """


def _live_actions(item: Dict[str, Any]) -> str:
    client_id = int(item.get("client_id") or 0)
    status = str(item.get("status") or "")
    commercial_status = str(item.get("commercial_status") or "")
    forms: List[str] = []
    if status in {"live_requested", "live_rejected", "live_suspended"}:
        forms.append(
            f"<form method='post' action='/admin/api/commercial/live/{client_id}/approve'>"
            "<input name='comment' placeholder='Approval comment' maxlength='1000'><button>Approve</button></form>"
        )
    if status == "live_requested":
        forms.append(
            f"<form method='post' action='/admin/api/commercial/live/{client_id}/reject'>"
            "<input name='comment' placeholder='Required rejection reason' maxlength='1000' required>"
            "<button class='danger'>Reject</button></form>"
        )
    if commercial_status == "live_approved":
        forms.append(
            f"<form method='post' action='/admin/api/commercial/live/{client_id}/suspend'>"
            "<input name='comment' placeholder='Suspension reason' maxlength='1000'>"
            "<button class='danger'>Suspend</button></form>"
        )
    return "<div class='button-row'>" + "".join(forms) + "</div>" if forms else "—"


def _request_row(item: Dict[str, Any]) -> str:
    return (
        "<tr>"
        f"<td><code>{escape(str(item.get('request_id') or ''))}</code></td>"
        f"<td>#{int(item.get('client_id') or 0)} {escape(str(item.get('client_name') or ''))}</td>"
        f"<td>{int(item.get('user_id') or 0)}</td>"
        f"<td>{_pill(item.get('status'))}</td>"
        f"<td>{escape(str(item.get('company_name') or ''))}<br><a href='{escape(str(item.get('website') or '#'))}' target='_blank' rel='noreferrer'>{escape(str(item.get('website') or '—'))}</a></td>"
        f"<td>{escape(str(item.get('contact') or ''))}</td>"
        f"<td>{int(item.get('expected_monthly_requests') or 0)}</td>"
        f"<td class='truncate-4'>{escape(str(item.get('use_case') or ''))}</td>"
        f"<td class='truncate-4'>{escape(str(item.get('admin_comment') or item.get('review_note') or '—'))}</td>"
        f"<td>{escape(str(item.get('created_at') or ''))}</td>"
        f"<td>{_live_actions(item)}</td>"
        "</tr>"
    )


def _invoice_actions(item: Dict[str, Any]) -> str:
    invoice_id = escape(str(item.get("invoice_id") or ""))
    status = str(item.get("status") or "")
    forms: List[str] = []
    if status in {"pending", "awaiting_payment", "payment_detected"}:
        forms.append(
            f"<form method='post' action='/admin/api/credit-invoices/{invoice_id}/mark-paid'>"
            "<input name='payment_reference' placeholder='payment reference' maxlength='240'>"
            "<button>Mark paid</button></form>"
        )
    if status in {"payment_detected", "paid", "crediting"}:
        forms.append(
            f"<form method='post' action='/admin/api/credit-invoices/{invoice_id}/credit'>"
            "<button>Credit once</button></form>"
        )
    if status not in {"credited", "refunded", "cancelled"}:
        forms.append(
            f"<form method='post' action='/admin/api/credit-invoices/{invoice_id}/cancel'>"
            "<input name='reason' placeholder='reason' maxlength='500'>"
            "<button class='danger'>Cancel</button></form>"
        )
    return "<div class='button-row'>" + "".join(forms) + "</div>" if forms else "—"


def _invoice_row(item: Dict[str, Any]) -> str:
    return (
        "<tr>"
        f"<td><code>{escape(str(item.get('invoice_id') or ''))}</code></td>"
        f"<td>#{int(item.get('client_id') or 0)} {escape(str(item.get('client_name') or ''))}</td>"
        f"<td>{int(item.get('owner_user_id') or 0)}</td>"
        f"<td>{escape(str(item.get('package_code') or ''))}</td>"
        f"<td>{int(item.get('credits') or 0)}</td>"
        f"<td>{escape(str(item.get('amount') or ''))} {escape(str(item.get('currency') or ''))}</td>"
        f"<td>{escape(str(item.get('payment_provider') or ''))}</td>"
        f"<td>{_pill(item.get('status'))}</td>"
        f"<td><code>{escape(str(item.get('payment_reference') or ''))}</code></td>"
        f"<td class='truncate-4'>{escape(str(item.get('tx_hash') or item.get('last_error') or '—'))}</td>"
        f"<td>{escape(str(item.get('paid_at') or '—'))}</td>"
        f"<td>{escape(str(item.get('credited_at') or '—'))}</td>"
        f"<td>{_invoice_actions(item)}</td>"
        "</tr>"
    )


def _billing_control_rows() -> str:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT c.id,c.name,c.credit_balance,c.low_balance_threshold,
            c.daily_spend_limit_credits,c.monthly_spend_limit_credits,c.auto_recharge_enabled,
            c.auto_recharge_package_code,c.commercial_status
            FROM api_clients c JOIN api_client_owners o ON o.client_id=c.id
            ORDER BY c.id DESC LIMIT 200"""
        )
        rows = cursor.fetchall()
        columns = [item[0] for item in (cursor.description or [])]
        result = []
        for raw in rows:
            item = dict(raw) if isinstance(raw, dict) else dict(zip(columns, raw))
            result.append(
                "<tr>"
                f"<td>#{int(item.get('id') or 0)} {escape(str(item.get('name') or ''))}</td>"
                f"<td>{_pill(item.get('commercial_status'))}</td>"
                f"<td>{int(item.get('credit_balance') or 0)}</td>"
                f"<td>{escape(str(item.get('low_balance_threshold') if item.get('low_balance_threshold') is not None else 'off'))}</td>"
                f"<td>{escape(str(item.get('daily_spend_limit_credits') if item.get('daily_spend_limit_credits') is not None else 'off'))}</td>"
                f"<td>{escape(str(item.get('monthly_spend_limit_credits') if item.get('monthly_spend_limit_credits') is not None else 'off'))}</td>"
                f"<td>{'disabled' if not item.get('auto_recharge_enabled') else 'unexpected enabled'}</td>"
                "</tr>"
            )
        return "".join(result)
    finally:
        cursor.close()
        conn.close()


def _commercial_dashboard(request: web.Request) -> str:
    runtime = get_commercial_runtime_health(include_workers=True)
    packages = list_credit_packages(include_disabled=True)
    requests = list_live_requests(limit=100)
    status_filter = str(request.query.get("invoice_status") or "").strip()
    provider_filter = str(request.query.get("provider") or "").strip()
    try:
        client_filter: Optional[int] = int(request.query.get("client_id")) if request.query.get("client_id") else None
    except Exception:
        client_filter = None
    invoices = list_all_invoices(status=status_filter, client_id=client_filter, provider=provider_filter, limit=200)
    ledger = list_recent_purchase_ledger(limit=50)
    events = list_payment_events(limit=100)
    warnings = "".join(
        f"<div class='card danger'><b>Commercial warning:</b> {escape(str(item).replace('_', ' '))}</div>"
        for item in runtime.get("warnings") or []
    )
    package_rows = "".join(_package_row(item) for item in packages)
    request_rows = "".join(_request_row(item) for item in requests)
    invoice_rows = "".join(_invoice_row(item) for item in invoices)
    ledger_rows = "".join(
        "<tr>"
        f"<td>{int(item.get('client_id') or 0)}</td><td>{int(item.get('amount') or 0)}</td>"
        f"<td>{int(item.get('balance_after') or 0)}</td><td><code>{escape(str(item.get('idempotency_key') or ''))}</code></td>"
        f"<td>{escape(str(item.get('created_at') or ''))}</td></tr>"
        for item in ledger
    )
    event_rows = "".join(
        "<tr>"
        f"<td><code>{escape(str(item.get('event_id') or ''))}</code></td>"
        f"<td><code>{escape(str(item.get('invoice_id') or ''))}</code></td>"
        f"<td>{escape(str(item.get('event_type') or ''))}</td><td>{escape(str(item.get('actor') or ''))}</td>"
        f"<td>{escape(str(item.get('from_status') or '—'))} → {escape(str(item.get('to_status') or '—'))}</td>"
        f"<td>{escape(str(item.get('created_at') or ''))}</td></tr>"
        for item in events
    )
    return f"""
    <div class='card'><h3>API commercial launch</h3><div class='grid'>
      <div>Status <b>{escape(str(runtime.get('status') or 'unknown'))}</b></div>
      <div>Configured provider <b>{escape(invoice_provider_name())}</b></div>
      <div>Launch enabled <b>{'yes' if runtime.get('enabled') else 'no'}</b></div>
      <div>Live keys <b>{'yes' if runtime.get('live_keys_enabled') else 'no'}</b></div>
      <div>Treasury incoming <b>{'yes' if runtime.get('treasury_incoming_enabled') else 'no'}</b></div>
      <div>Network <b>{escape(str(runtime.get('network') or ''))}</b></div>
      <div>Pending invoices <b>{int(runtime.get('pending_invoices') or 0)}</b></div>
      <div>Pending live reviews <b>{int(runtime.get('pending_live_requests') or 0)}</b></div>
    </div><form method='post' action='/admin/api/commercial/scan'><button>Scan TON payments now</button></form></div>
    {warnings}
    <div class='card'><h3>Create API credit package</h3>
      <form method='post' action='/admin/api/commercial/packages/create' class='grid'>
        <label>Code<br><input name='package_code' placeholder='starter' required></label>
        <label>Name<br><input name='display_name' placeholder='Starter' required></label>
        <label>Credits<br><input name='credits' value='100' inputmode='numeric' required></label>
        <label>Price<br><input name='price_amount' inputmode='decimal' required></label>
        <label>Currency<br><input name='price_currency' value='TON' maxlength='12' required></label>
        <label>Sort<br><input name='sort_order' value='0' inputmode='numeric'></label>
        <label><br><input type='checkbox' name='enabled' checked> enabled</label>
        <div><br><button>Create package</button></div>
      </form><p class='muted'>The server snapshots package credits, amount and currency into every invoice.</p></div>
    <div class='card'><h3>Credit packages</h3><div class='table-scroll'><table>
      <tr><th>Code</th><th colspan='7'>Configuration</th><th>Updated</th></tr>{package_rows or '<tr><td colspan=9>No packages.</td></tr>'}
    </table></div></div>
    <div class='card'><h3>Live access requests</h3><div class='table-scroll'><table>
      <tr><th>Request</th><th>Project</th><th>User</th><th>Status</th><th>Company / website</th><th>Contact</th><th>Requests/month</th><th>Use case</th><th>Admin comment</th><th>Created</th><th>Actions</th></tr>
      {request_rows or '<tr><td colspan=11>No requests.</td></tr>'}</table></div></div>
    <div class='card'><h3>Invoice filters</h3><form method='get' action='/admin/developer-api' class='grid'>
      <label>Status<br><input name='invoice_status' value='{escape(status_filter)}'></label>
      <label>Client ID<br><input name='client_id' value='{escape(str(client_filter or ''))}'></label>
      <label>Provider<br><input name='provider' value='{escape(provider_filter)}'></label><div><br><button>Filter</button></div>
    </form></div>
    <div class='card'><h3>API credit invoices</h3><div class='table-scroll'><table>
      <tr><th>Invoice</th><th>Project</th><th>Owner</th><th>Package</th><th>Credits</th><th>Amount</th><th>Provider</th><th>Status</th><th>Reference</th><th>Tx / error</th><th>Paid</th><th>Credited</th><th>Actions</th></tr>
      {invoice_rows or '<tr><td colspan=13>No invoices.</td></tr>'}</table></div></div>
    <div class='card'><h3>Project spend controls</h3><div class='table-scroll'><table>
      <tr><th>Project</th><th>Live state</th><th>Balance</th><th>Low balance</th><th>Daily cap</th><th>Monthly cap</th><th>Auto recharge</th></tr>
      {_billing_control_rows() or '<tr><td colspan=7>No projects.</td></tr>'}</table></div></div>
    <div class='card'><h3>Recent purchase ledger</h3><div class='table-scroll'><table>
      <tr><th>Client</th><th>Credits</th><th>Balance after</th><th>Idempotency</th><th>Created</th></tr>
      {ledger_rows or '<tr><td colspan=5>No purchases.</td></tr>'}</table></div></div>
    <div class='card'><h3>Payment audit trail</h3><div class='table-scroll'><table>
      <tr><th>Event</th><th>Invoice</th><th>Type</th><th>Actor</th><th>Transition</th><th>Created</th></tr>
      {event_rows or '<tr><td colspan=6>No payment events.</td></tr>'}</table></div></div>
    """


def install() -> None:
    original = admin_routes.admin_developer_api
    if getattr(original, "_deepalpha_commercial_admin_v2", False):
        return

    async def admin_with_commercial(request):
        response = await original(request)
        if response.status != 200 or not str(response.content_type or "").startswith("text/html"):
            return response
        try:
            dashboard = _commercial_dashboard(request)
        except Exception as exc:
            dashboard = f"<div class='card danger'><b>Commercial dashboard unavailable:</b> {escape(type(exc).__name__)}</div>"
        text = response.text or ""
        marker = "</div></body></html>"
        response.text = text.replace(marker, dashboard + marker, 1) if marker in text else text + dashboard
        return response

    admin_with_commercial._deepalpha_commercial_admin_v2 = True
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
            price_amount=str(form.get("price_amount") or ""),
            price_currency=str(form.get("price_currency") or "TON"),
            enabled=str(form.get("enabled") or "").lower() in {"on", "true", "1"},
            sort_order=int(str(form.get("sort_order") or "0")),
            metadata={},
            actor="admin",
        )
        return admin_routes._message_redirect(f"Credit package {package.get('package_code')} saved")
    except Exception as exc:
        return admin_routes._message_redirect(str(getattr(exc, "code", str(exc))))


async def _admin_live_action(request: web.Request, action: str) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    form = await request.post()
    try:
        review_live_access(
            client_id=int(request.match_info.get("client_id") or 0),
            action=action,
            actor="admin",
            comment=str(form.get("comment") or ""),
        )
        return admin_routes._message_redirect(f"Live API action completed: {action}")
    except Exception as exc:
        return admin_routes._message_redirect(str(getattr(exc, "code", str(exc))))


async def admin_approve_live(request: web.Request) -> web.Response:
    return await _admin_live_action(request, "approve")


async def admin_reject_live(request: web.Request) -> web.Response:
    return await _admin_live_action(request, "reject")


async def admin_suspend_live(request: web.Request) -> web.Response:
    return await _admin_live_action(request, "suspend")


async def admin_mark_paid(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    form = await request.post()
    try:
        result = mark_invoice_paid_admin(
            invoice_id=str(request.match_info.get("invoice_id") or ""),
            actor="admin",
            payment_reference=str(form.get("payment_reference") or ""),
        )
        return admin_routes._message_redirect(f"Invoice marked paid; idempotent={bool(result.get('idempotent'))}")
    except Exception as exc:
        return admin_routes._message_redirect(str(getattr(exc, "code", str(exc))))


async def admin_credit_invoice(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    try:
        result = credit_invoice_admin(
            invoice_id=str(request.match_info.get("invoice_id") or ""),
            actor="admin",
        )
        return admin_routes._message_redirect(
            f"Invoice credited; idempotent={bool(result.get('idempotent'))}; balance={result.get('balance_after')}"
        )
    except Exception as exc:
        return admin_routes._message_redirect(str(getattr(exc, "code", str(exc))))


async def admin_cancel_invoice(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    form = await request.post()
    try:
        result = cancel_invoice_admin(
            invoice_id=str(request.match_info.get("invoice_id") or ""),
            actor="admin",
            reason=str(form.get("reason") or ""),
        )
        return admin_routes._message_redirect(f"Invoice cancelled; idempotent={bool(result.get('idempotent'))}")
    except Exception as exc:
        return admin_routes._message_redirect(str(getattr(exc, "code", str(exc))))


async def admin_scan_payments(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    try:
        result = scan_payments_once(page_limit=100, max_pages=10)
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
    app.router.add_post("/admin/api/commercial/live/{client_id}/suspend", admin_suspend_live)
    app.router.add_post("/admin/api/credit-invoices/{invoice_id}/mark-paid", admin_mark_paid)
    app.router.add_post("/admin/api/credit-invoices/{invoice_id}/credit", admin_credit_invoice)
    app.router.add_post("/admin/api/credit-invoices/{invoice_id}/cancel", admin_cancel_invoice)
    app.router.add_post("/admin/api/commercial/scan", admin_scan_payments)
    app["developer_api_commercial_admin_routes_installed"] = True
