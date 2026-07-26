from html import escape
from typing import Any, Dict, List

from aiohttp import web

from admin_routes import SECTIONS, _guard, _key, _layout
from services.developer_api_service import (
    AVAILABLE_SCOPES,
    create_api_client,
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


async def admin_developer_api(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    clients = list_api_clients(limit=500)
    keys_by_client = _client_keys_by_id()
    cards = []
    scope_options = "".join(
        f"<label style='display:inline-block;margin:4px'><input type='checkbox' name='scope' value='{escape(scope)}' "
        f"{'checked' if scope in {'account:read', 'usage:read'} else ''}> {escape(scope)}</label>"
        for scope in sorted(AVAILABLE_SCOPES)
    )
    for client in clients:
        client_id = int(client.get("id") or 0)
        rows = "".join(_key_row(item) for item in keys_by_client.get(client_id, []))
        cards.append(
            f"""<div class='card'>
            <h3 style='margin-top:0'>#{client_id} {escape(str(client.get('name') or ''))}</h3>
            <div class='grid'>
              <div><span class='pill'>status {escape(str(client.get('status') or ''))}</span></div>
              <div>Today <b>{int(client.get('usage_today') or 0)}</b> / {int(client.get('daily_request_limit') or 0)}</div>
              <div>Month <b>{int(client.get('usage_month') or 0)}</b> / {int(client.get('monthly_request_limit') or 0)}</div>
              <div>Rate <b>{int(client.get('rate_limit_per_minute') or 0)}/min</b></div>
              <div>Credits <b>{int(client.get('credit_balance') or 0)}</b></div>
              <div>Active keys <b>{int(client.get('active_keys') or 0)}</b></div>
            </div>
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
    <div class='card'><b>Developer API v1 foundation</b><br>
      Active now: <code>GET /api/v1/account</code>, <code>GET /api/v1/usage</code>, <code>GET /api/v1/capabilities</code>.<br>
      Analysis endpoints remain disabled until billing, idempotency and job execution are connected.
    </div>
    {''.join(cards) if cards else '<div class=card>No API clients yet.</div>'}
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
        create_api_client(
            name=str(form.get("name", "") or ""),
            daily_request_limit=_safe_int(form.get("daily_limit"), 1000, 1, 1_000_000),
            monthly_request_limit=_safe_int(form.get("monthly_limit"), 20000, 1, 20_000_000),
            rate_limit_per_minute=_safe_int(form.get("rate_limit"), 60, 1, 10_000),
            credit_balance=_safe_int(form.get("credits"), 0, 0, 1_000_000_000),
        )
        return web.HTTPFound("/admin/api?msg=API+client+created")
    except ValueError as exc:
        return web.HTTPFound(f"/admin/api?msg={escape(str(exc))}")


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
        return web.HTTPFound(f"/admin/api?msg={escape(str(exc))}")
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
    return web.HTTPFound(f"/admin/api?msg={'Key+revoked' if changed else 'Key+not+active'}")


def setup_developer_api_admin_routes(app: web.Application) -> None:
    if app.get("developer_api_admin_routes_installed"):
        return
    if not any(name == "API" for name, _path in SECTIONS):
        SECTIONS.append(("API", "/admin/api"))
    app.router.add_get("/admin/api", admin_developer_api)
    app.router.add_post("/admin/api/clients/create", admin_create_api_client)
    app.router.add_post("/admin/api/clients/{client_id}/keys/create", admin_create_api_key)
    app.router.add_post("/admin/api/keys/{key_id}/revoke", admin_revoke_api_key)
    app["developer_api_admin_routes_installed"] = True
