import secrets
from html import escape
from typing import Any, Dict, List
from urllib.parse import quote_plus

from aiohttp import web

from admin_routes import SECTIONS, _guard, _key, _layout
from services.developer_api_billing_service import (
    ApiBillingError,
    adjust_api_credits,
    create_billed_api_client,
    list_api_credit_ledger,
    list_api_credit_reservations,
    list_api_products,
    update_api_product,
)
from services.developer_api_service import (
    AVAILABLE_SCOPES,
    issue_api_key,
    list_api_clients,
    list_api_keys,
    revoke_api_key,
)


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _message_redirect(message: str) -> web.HTTPFound:
    return web.HTTPFound(f"/admin/api?msg={quote_plus(str(message or '')[:300])}")


def _client_keys_by_id() -> Dict[int, List[Dict[str, Any]]]:
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for key in list_api_keys(limit=2000):
        grouped.setdefault(int(key.get("client_id") or 0), []).append(key)
    return grouped


def _key_row(key: Dict[str, Any]) -> str:
    status = escape(str(key.get("status") or ""))
    scopes = ", ".join(escape(str(scope)) for scope in key.get("scopes") or [])
    revoke = ""
    if status == "active":
        revoke = (
            f"<form method='post' action='/admin/api/keys/{int(key.get('id') or 0)}/revoke' "
            "style='display:inline' onsubmit=\"return confirm('Revoke this key?')\">"
            "<button class='danger'>Revoke</button></form>"
        )
    return (
        "<tr>"
        f"<td>{int(key.get('id') or 0)}</td>"
        f"<td>{escape(str(key.get('name') or 'default'))}</td>"
        f"<td><code>{escape(str(key.get('key_prefix') or ''))}…</code></td>"
        f"<td>{escape(str(key.get('environment') or 'test'))}</td>"
        f"<td>{scopes}</td>"
        f"<td>{status}</td>"
        f"<td>{escape(str(key.get('last_used_at') or 'never'))}</td>"
        f"<td>{revoke}</td>"
        "</tr>"
    )


def _product_row(product: Dict[str, Any]) -> str:
    code = escape(str(product.get("product_code") or ""))
    checked = "checked" if bool(product.get("enabled")) else ""
    return f"""
    <tr>
      <td><code>{code}</code></td>
      <td colspan='5'>
        <form method='post' action='/admin/api/products/{code}' class='grid'>
          <label>Name<br><input name='display_name' value='{escape(str(product.get('display_name') or ''))}' maxlength='120'></label>
          <label>Credits<br><input name='unit_price' value='{int(product.get('unit_price') or 0)}' inputmode='numeric'></label>
          <label><br><input type='checkbox' name='enabled' {checked}> enabled</label>
          <div><br><button>Save product</button></div>
        </form>
      </td>
      <td>{escape(str(product.get('updated_at') or ''))}</td>
    </tr>
    """


def _ledger_row(entry: Dict[str, Any]) -> str:
    amount = int(entry.get("amount") or 0)
    amount_text = f"+{amount}" if amount > 0 else str(amount)
    return (
        "<tr>"
        f"<td>{int(entry.get('id') or 0)}</td>"
        f"<td>{int(entry.get('client_id') or 0)}</td>"
        f"<td>{escape(str(entry.get('event_type') or ''))}</td>"
        f"<td><b>{escape(amount_text)}</b></td>"
        f"<td>{int(entry.get('balance_after') or 0)}</td>"
        f"<td><code>{escape(str(entry.get('job_id') or '—'))}</code></td>"
        f"<td><code>{escape(str(entry.get('idempotency_key') or ''))}</code></td>"
        f"<td>{escape(str(entry.get('created_at') or ''))}</td>"
        "</tr>"
    )


def _reservation_row(item: Dict[str, Any]) -> str:
    return (
        "<tr>"
        f"<td><code>{escape(str(item.get('reservation_id') or ''))}</code></td>"
        f"<td>{int(item.get('client_id') or 0)}</td>"
        f"<td><code>{escape(str(item.get('job_id') or '—'))}</code></td>"
        f"<td>{escape(str(item.get('product_code') or ''))}</td>"
        f"<td>{int(item.get('units') or 0)}</td>"
        f"<td>{escape(str(item.get('status') or ''))}</td>"
        f"<td><code>{escape(str(item.get('idempotency_key') or ''))}</code></td>"
        f"<td>{escape(str(item.get('created_at') or ''))}</td>"
        "</tr>"
    )


async def admin_developer_api(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    clients = list_api_clients(limit=500)
    keys_by_client = _client_keys_by_id()
    products = list_api_products()
    ledger = list_api_credit_ledger(limit=100)
    reservations = list_api_credit_reservations(limit=50)
    cards = []
    scope_options = "".join(
        f"<label style='display:inline-block;margin:4px'><input type='checkbox' name='scope' value='{escape(scope)}' "
        f"{'checked' if scope in {'account:read', 'usage:read'} else ''}> {escape(scope)}</label>"
        for scope in sorted(AVAILABLE_SCOPES)
    )
    for client in clients:
        client_id = int(client.get("id") or 0)
        rows = "".join(_key_row(item) for item in keys_by_client.get(client_id, []))
        adjustment_key = f"admin_adjust:{client_id}:{secrets.token_hex(12)}"
        cards.append(
            f"""<div class='card'>
            <h3 style='margin-top:0'>#{client_id} {escape(str(client.get('name') or ''))}</h3>
            <div class='grid'>
              <div><span class='pill'>status {escape(str(client.get('status') or ''))}</span></div>
              <div>Today <b>{int(client.get('usage_today') or 0)}</b> / {int(client.get('daily_request_limit') or 0)}</div>
              <div>Month <b>{int(client.get('usage_month') or 0)}</b> / {int(client.get('monthly_request_limit') or 0)}</div>
              <div>Rate <b>{int(client.get('rate_limit_per_minute') or 0)}/min</b></div>
              <div>Available credits <b>{int(client.get('credit_balance') or 0)}</b></div>
              <div>Active keys <b>{int(client.get('active_keys') or 0)}</b></div>
            </div>
            <details><summary>Adjust API credits</summary>
              <form method='post' action='/admin/api/clients/{client_id}/credits'>
                <input type='hidden' name='idempotency_key' value='{adjustment_key}'>
                <label>Delta: positive adds, negative removes<br><input name='delta' value='100' required inputmode='numeric'></label><br><br>
                <label>Reason<br><input name='reason' value='Manual admin adjustment' maxlength='500' required></label><br><br>
                <button>Apply credit adjustment</button>
              </form>
            </details>
            <details><summary>Create API key</summary>
              <form method='post' action='/admin/api/clients/{client_id}/keys/create'>
                <label>Name<br><input name='name' value='default' maxlength='80'></label><br><br>
                <label>Environment<br><select name='environment'><option value='test'>test</option><option value='live'>live</option></select></label><br><br>
                <div>{scope_options}</div><br>
                <button>Create key</button>
              </form>
            </details>
            <div class='table-scroll'><table>
              <tr><th>ID</th><th>Name</th><th>Prefix</th><th>Env</th><th>Scopes</th><th>Status</th><th>Last used</th><th></th></tr>
              {rows or '<tr><td colspan=8 class=muted>No keys</td></tr>'}
            </table></div>
            </div>"""
        )

    product_rows = "".join(_product_row(item) for item in products)
    ledger_rows = "".join(_ledger_row(item) for item in ledger)
    reservation_rows = "".join(_reservation_row(item) for item in reservations)
    body = f"""
    <div class='card'>
      <h3>Create API client</h3>
      <form method='post' action='/admin/api/clients/create' class='grid'>
        <label>Name<br><input name='name' required maxlength='120'></label>
        <label>Daily limit<br><input name='daily_limit' value='1000'></label>
        <label>Monthly limit<br><input name='monthly_limit' value='20000'></label>
        <label>Requests/min<br><input name='rate_limit' value='60'></label>
        <label>Initial credits<br><input name='credits' value='0'></label>
        <div><br><button>Create client</button></div>
      </form>
    </div>
    <div class='card'><b>API Billing Foundation</b><br>
      Credits are append-only ledger entries. Job creation reserves credits atomically; success finalizes the charge; internal failure refunds the same reservation.<br>
      Public analysis endpoints remain disabled until the execution worker is connected.
    </div>
    <div class='card'>
      <h3>API products and prices</h3>
      <div class='table-scroll'><table>
        <tr><th>Product</th><th colspan='5'>Configuration</th><th>Updated</th></tr>
        {product_rows or '<tr><td colspan=7 class=muted>No products</td></tr>'}
      </table></div>
    </div>
    {''.join(cards) if cards else '<div class=card>No API clients yet.</div>'}
    <div class='card'>
      <h3>Recent credit ledger</h3>
      <div class='table-scroll'><table>
        <tr><th>ID</th><th>Client</th><th>Event</th><th>Delta</th><th>Balance</th><th>Job</th><th>Idempotency</th><th>Created</th></tr>
        {ledger_rows or '<tr><td colspan=8 class=muted>No ledger entries</td></tr>'}
      </table></div>
    </div>
    <div class='card'>
      <h3>Recent reservations</h3>
      <div class='table-scroll'><table>
        <tr><th>Reservation</th><th>Client</th><th>Job</th><th>Product</th><th>Credits</th><th>Status</th><th>Idempotency</th><th>Created</th></tr>
        {reservation_rows or '<tr><td colspan=8 class=muted>No reservations</td></tr>'}
      </table></div>
    </div>
    """
    return web.Response(
        text=_layout("Developer API", "API", _key(request), body, request.query.get("msg", "")),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def admin_create_api_client(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    form = await request.post()
    try:
        create_billed_api_client(
            name=str(form.get("name", "") or ""),
            daily_request_limit=_safe_int(form.get("daily_limit"), 1000, 1, 1_000_000),
            monthly_request_limit=_safe_int(form.get("monthly_limit"), 20000, 1, 20_000_000),
            rate_limit_per_minute=_safe_int(form.get("rate_limit"), 60, 1, 10_000),
            initial_credits=_safe_int(form.get("credits"), 0, 0, 1_000_000_000),
        )
        return _message_redirect("API client created")
    except (ValueError, ApiBillingError) as exc:
        return _message_redirect(getattr(exc, "code", str(exc)))


async def admin_adjust_api_credits(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    client_id = int(request.match_info.get("client_id") or 0)
    form = await request.post()
    try:
        result = adjust_api_credits(
            client_id=client_id,
            delta=int(str(form.get("delta", "0") or "0").strip()),
            reason=str(form.get("reason", "Manual admin adjustment") or "Manual admin adjustment"),
            idempotency_key=str(form.get("idempotency_key", "") or ""),
            actor="admin",
        )
        suffix = "idempotent" if result.get("idempotent") else "applied"
        return _message_redirect(f"Credit adjustment {suffix}; balance {int(result.get('balance_after') or 0)}")
    except (ValueError, ApiBillingError) as exc:
        return _message_redirect(getattr(exc, "code", str(exc)))


async def admin_update_api_product(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    form = await request.post()
    try:
        product = update_api_product(
            product_code=str(request.match_info.get("product_code") or ""),
            display_name=str(form.get("display_name", "") or ""),
            unit_price=int(str(form.get("unit_price", "0") or "0").strip()),
            enabled=str(form.get("enabled", "") or "").lower() in {"on", "1", "true", "yes"},
            actor="admin",
        )
        return _message_redirect(f"Product {product.get('product_code')} saved")
    except (ValueError, ApiBillingError) as exc:
        return _message_redirect(getattr(exc, "code", str(exc)))


async def admin_create_api_key(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    client_id = int(request.match_info.get("client_id") or 0)
    form = await request.post()
    scopes = form.getall("scope", [])
    try:
        created = issue_api_key(
            client_id=client_id,
            name=str(form.get("name", "default") or "default"),
            environment=str(form.get("environment", "test") or "test"),
            scopes=scopes,
        )
    except ValueError as exc:
        return _message_redirect(str(exc))
    raw_key = escape(str(created.get("raw_key") or ""))
    body = f"""<div class='card success'><h3>API key created</h3>
    <p>This secret is shown once. Store it now; only its SHA-256 hash is kept in the database.</p>
    <textarea rows='4' readonly style='width:100%'>{raw_key}</textarea>
    <p><code>Authorization: Bearer {raw_key}</code></p>
    <a href='/admin/api'>Return to API clients</a></div>"""
    return web.Response(
        text=_layout("API key created", "API", "", body),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def admin_revoke_api_key(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    key_id = int(request.match_info.get("key_id") or 0)
    changed = revoke_api_key(key_id)
    return _message_redirect("Key revoked" if changed else "Key not active")


def setup_developer_api_admin_routes(app: web.Application) -> None:
    if app.get("developer_api_admin_routes_installed"):
        return
    if not any(name == "API" for name, _path in SECTIONS):
        SECTIONS.append(("API", "/admin/api"))
    app.router.add_get("/admin/api", admin_developer_api)
    app.router.add_post("/admin/api/clients/create", admin_create_api_client)
    app.router.add_post("/admin/api/clients/{client_id}/credits", admin_adjust_api_credits)
    app.router.add_post("/admin/api/clients/{client_id}/keys/create", admin_create_api_key)
    app.router.add_post("/admin/api/keys/{key_id}/revoke", admin_revoke_api_key)
    app.router.add_post("/admin/api/products/{product_code}", admin_update_api_product)
    app["developer_api_admin_routes_installed"] = True
