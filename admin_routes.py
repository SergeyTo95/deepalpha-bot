import asyncio
import html
import json
import os
import uuid
from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote_plus

from aiohttp import web

from services.velia_admin_control_service import (
    adjust_user_tokens,
    ai_snapshot,
    audit_snapshot,
    deployment_snapshot,
    list_users,
    memory_queue_snapshot,
    overview_snapshot,
    recent_errors,
    set_user_banned,
    set_user_token_balance,
    set_user_vip_status,
    user_detail,
    velyon_memory_health,
)
from services.velia_admin_security_service import (
    ADMIN_CSRF_COOKIE,
    ADMIN_SESSION_COOKIE,
    ADMIN_SESSION_TTL_SECONDS,
    configured_admin_id,
    consume_admin_login_code,
    get_admin_session,
    record_admin_audit,
    revoke_admin_session,
    verify_admin_csrf,
)
from services.velia_admin_telegram_auth_service import build_admin_login_url


CONTROL_CENTER_AUTH_V2 = True
SECTIONS = [
    ("Overview", "/admin"),
    ("System", "/admin/system"),
    ("Users", "/admin/users"),
    ("AI / Core", "/admin/ai"),
    ("Errors", "/admin/errors"),
    ("Memory", "/admin/memory"),
    ("Deployments", "/admin/deployments"),
    ("Audit", "/admin/audit"),
]


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _request_id(request: web.Request) -> str:
    incoming = str(request.headers.get("X-Request-ID", "") or "").strip()
    if incoming and len(incoming) <= 160:
        return incoming
    cached = str(request.get("velia_request_id", "") or "")
    if cached:
        return cached
    value = uuid.uuid4().hex
    request["velia_request_id"] = value
    return value


def _secure_cookie_enabled() -> bool:
    explicit = str(os.getenv("COOKIE_SECURE", "") or "").strip().lower()
    if explicit:
        return explicit in {"1", "true", "yes", "on"}
    environment = str(os.getenv("RAILWAY_ENVIRONMENT_NAME", "") or "").strip().lower()
    return environment in {"production", "prod"}


def _set_admin_cookies(response: web.StreamResponse, session_token: str, csrf_token: str) -> None:
    secure = _secure_cookie_enabled()
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        session_token,
        max_age=ADMIN_SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="Strict",
        path="/admin",
    )
    response.set_cookie(
        ADMIN_CSRF_COOKIE,
        csrf_token,
        max_age=ADMIN_SESSION_TTL_SECONDS,
        httponly=False,
        secure=secure,
        samesite="Strict",
        path="/admin",
    )


def _clear_admin_cookies(response: web.StreamResponse) -> None:
    response.del_cookie(ADMIN_SESSION_COOKIE, path="/admin")
    response.del_cookie(ADMIN_CSRF_COOKIE, path="/admin")


def _current_admin(request: web.Request) -> Optional[Dict[str, Any]]:
    cached = request.get("velia_admin_session")
    if cached:
        return cached
    raw = str(request.cookies.get(ADMIN_SESSION_COOKIE, "") or "")
    session = get_admin_session(raw) if raw else None
    if session:
        request["velia_admin_session"] = session
    return session


async def _guard(request: web.Request):
    if configured_admin_id() <= 0:
        return web.Response(text="VELIA Control Center is not configured", status=503)
    session = await asyncio.to_thread(_current_admin, request)
    if not session:
        if request.method in {"GET", "HEAD"}:
            return web.HTTPFound("/admin/login")
        return web.Response(text="Unauthorized", status=401)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        form = await request.post()
        csrf = str(
            form.get("_csrf", "")
            or request.headers.get("X-VELIA-CSRF", "")
            or ""
        )
        if not verify_admin_csrf(session, csrf):
            return web.Response(text="Invalid CSRF token", status=403)
    return None


def _key(request: web.Request) -> str:
    # Compatibility export used by the pre-existing Developer API admin routes.
    # It is now a CSRF token, never an admin secret and never belongs in a URL.
    return str(request.cookies.get(ADMIN_CSRF_COOKIE, "") or "")


def _metric(value: Any, *, suffix: str = "", precision: Optional[int] = None) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, float) and precision is not None:
        return f"{value:.{precision}f}{suffix}"
    return f"{value}{suffix}"


def _status(value: Any) -> str:
    normalized = str(value or "unavailable").lower()
    css = "good" if normalized in {"online", "ok", "healthy", "success"} else "warn" if normalized in {"degraded", "pending", "retrying"} else "bad" if normalized in {"offline", "failed", "error"} else "muted"
    return f"<span class='status {css}'><i></i>{_e(normalized.title())}</span>"


def _json_safe(value: Any) -> str:
    return _e(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _layout(title: str, active: str, key: str, body: str, flash: str = "") -> str:
    nav = "".join(
        f"<a class='nav {'active' if name == active else ''}' href='{path}'>"
        f"<span>{_e(name)}</span></a>"
        for name, path in SECTIONS
    )
    csrf = _e(key)
    flash_html = f"<div class='flash'>{_e(flash)}</div>" if flash else ""
    return f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>
<meta name='referrer' content='no-referrer'>
<meta name='color-scheme' content='dark'>
<meta name='velia-csrf' content='{csrf}'>
<title>{_e(title)} · VELIA Control Center</title>
<style>
:root{{--bg:#05070b;--panel:#0b0f16;--panel2:#0f1520;--line:#1d2735;--text:#eef2f8;--muted:#8d9bad;--accent:#8a7dff;--accent2:#4fd1c5;--danger:#ff6b7a;--warn:#f6c85f;--good:#57d39b}}
*{{box-sizing:border-box}}html,body{{margin:0;background:radial-gradient(circle at 90% -10%,#161438 0,transparent 32%),var(--bg);color:var(--text);font:14px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}body{{min-height:100vh}}a{{color:inherit}}button,input,select{{font:inherit}}.shell{{display:grid;grid-template-columns:224px minmax(0,1fr);min-height:100vh}}.side{{position:sticky;top:0;height:100vh;padding:22px 14px;border-right:1px solid var(--line);background:rgba(5,7,11,.84);backdrop-filter:blur(18px)}}.brand{{padding:4px 10px 22px}}.brand b{{font-size:17px;letter-spacing:.03em}}.brand small{{display:block;color:var(--muted);margin-top:3px}}.navs{{display:grid;gap:4px}}.nav{{text-decoration:none;color:#9ba9ba;padding:10px 11px;border-radius:10px}}.nav:hover{{background:#101622;color:#fff}}.nav.active{{background:linear-gradient(90deg,rgba(138,125,255,.19),rgba(79,209,197,.07));color:#fff;border:1px solid rgba(138,125,255,.24)}}.logout{{position:absolute;bottom:18px;left:14px;right:14px}}.main{{min-width:0;padding:28px clamp(16px,3vw,42px) 60px}}.topline{{display:flex;gap:12px;align-items:flex-start;justify-content:space-between;margin-bottom:22px}}h1{{font-size:27px;margin:0;letter-spacing:-.025em}}.subtitle{{color:var(--muted);margin-top:5px}}.pill,.status{{display:inline-flex;gap:7px;align-items:center;border:1px solid var(--line);background:#0c121b;border-radius:999px;padding:5px 9px;color:#b8c4d2;font-size:12px}}.status i{{width:7px;height:7px;border-radius:50%;background:#6f7a87}}.status.good i{{background:var(--good);box-shadow:0 0 10px rgba(87,211,155,.55)}}.status.warn i{{background:var(--warn)}}.status.bad i{{background:var(--danger)}}.grid{{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:12px}}.card{{grid-column:span 3;border:1px solid var(--line);background:linear-gradient(145deg,rgba(15,21,32,.95),rgba(9,13,20,.96));border-radius:15px;padding:16px;min-width:0}}.card.wide{{grid-column:span 6}}.card.full{{grid-column:1/-1}}.label{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.075em}}.value{{font-size:25px;font-weight:720;margin-top:7px;letter-spacing:-.02em}}.hint{{font-size:12px;color:var(--muted);margin-top:5px}}h2{{font-size:16px;margin:0 0 13px}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:13px}}table{{width:100%;border-collapse:collapse;min-width:720px}}th,td{{text-align:left;padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top}}th{{position:sticky;top:0;background:#0b111a;color:#8593a5;font-size:11px;text-transform:uppercase;letter-spacing:.06em}}tr:last-child td{{border-bottom:0}}code{{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:#c9c5ff}}.muted{{color:var(--muted)}}.good-text{{color:var(--good)}}.bad-text{{color:var(--danger)}}.flash{{border:1px solid rgba(87,211,155,.3);background:rgba(87,211,155,.08);padding:11px 13px;border-radius:11px;margin-bottom:14px}}form.inline{{display:flex;gap:8px;align-items:end;flex-wrap:wrap}}label{{display:grid;gap:5px;color:#aeb9c8;font-size:12px}}input,select{{border:1px solid #273346;background:#090e16;color:#f0f4fa;border-radius:9px;padding:9px 10px;outline:none}}input:focus,select:focus{{border-color:#685ee8;box-shadow:0 0 0 3px rgba(104,94,232,.12)}}button,.button{{border:1px solid #3a4657;background:#151d29;color:#fff;border-radius:9px;padding:9px 12px;cursor:pointer;text-decoration:none;display:inline-block}}button.primary,.button.primary{{border-color:#6f65e8;background:linear-gradient(135deg,#655bea,#8177f1)}}button.danger{{border-color:#713443;background:#33171f;color:#ffb5bf}}button:hover,.button:hover{{filter:brightness(1.08)}}.action-box{{display:grid;gap:10px;padding:13px;border:1px solid var(--line);border-radius:12px;background:#090e15}}.action-row{{display:flex;gap:8px;flex-wrap:wrap;align-items:end}}.confirm{{display:flex;align-items:center;gap:7px;color:#aeb9c8}}.confirm input{{width:auto}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;color:#c6d0dc;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}}.empty{{padding:18px;color:var(--muted)}}
@media(max-width:1050px){{.card{{grid-column:span 6}}.card.wide{{grid-column:1/-1}}}}
@media(max-width:760px){{.shell{{display:block}}.side{{position:sticky;z-index:5;height:auto;border-right:0;border-bottom:1px solid var(--line);padding:10px}}.brand{{padding:3px 6px 9px}}.brand small{{display:none}}.navs{{display:flex;overflow:auto;gap:5px;padding-bottom:2px}}.nav{{white-space:nowrap;padding:8px 10px}}.logout{{position:static;margin-top:8px}}.logout button{{display:none}}.main{{padding:18px 12px 44px}}.topline{{margin-bottom:16px}}h1{{font-size:22px}}.card,.card.wide{{grid-column:1/-1}}}}
</style>
</head>
<body>
<div class='shell'>
<aside class='side'><div class='brand'><b>VELIA</b><small>Control Center</small></div><nav class='navs'>{nav}</nav><div class='logout'><form method='post' action='/admin/logout'><button type='submit'>Sign out</button></form></div></aside>
<main class='main'><div class='topline'><div><h1>{_e(title)}</h1><div class='subtitle'>Internal owner console · live production data only</div></div><span class='pill'>Owner session</span></div>{flash_html}{body}</main>
</div>
<script>
(()=>{{
 const csrf=document.querySelector('meta[name="velia-csrf"]')?.content||'';
 document.querySelectorAll('form').forEach(form=>{{
   if((form.method||'get').toLowerCase()==='post' && !form.querySelector('input[name="_csrf"]')){{
     const input=document.createElement('input'); input.type='hidden'; input.name='_csrf'; input.value=csrf; form.appendChild(input);
   }}
 }});
 document.querySelectorAll('[data-confirm]').forEach(el=>{{el.addEventListener('click',ev=>{{if(!window.confirm(el.getAttribute('data-confirm')||'Confirm action?')) ev.preventDefault();}})}});
}})();
</script>
</body></html>"""


def _login_page(error: str = "") -> str:
    admin_ready = configured_admin_id() > 0
    link = build_admin_login_url()
    error_html = f"<div class='error'>{_e(error)}</div>" if error else ""
    ready_text = "Owner identity is configured" if admin_ready else "ADMIN_ID is not configured"
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'><meta name='color-scheme' content='dark'><title>VELIA Control Center</title><style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:18px;background:radial-gradient(circle at 70% 0,#18143a,transparent 35%),#05070b;color:#eef2f8;font:14px/1.5 Inter,system-ui,sans-serif}}.box{{width:min(440px,100%);padding:26px;border:1px solid #222c3a;border-radius:18px;background:rgba(11,15,22,.95);box-shadow:0 25px 80px rgba(0,0,0,.45)}}h1{{font-size:24px;margin:0 0 5px}}p{{color:#94a2b3}}.step{{padding:12px;border:1px solid #202b39;border-radius:12px;margin:11px 0;background:#090e15}}a,button{{display:block;width:100%;text-align:center;border:1px solid #7168e8;background:linear-gradient(135deg,#6258e8,#8278f2);color:#fff;text-decoration:none;padding:11px;border-radius:10px;cursor:pointer;font:inherit}}input{{width:100%;margin:8px 0 10px;border:1px solid #303b4d;background:#060a10;color:#fff;border-radius:10px;padding:12px;font:16px ui-monospace,monospace;text-transform:uppercase;letter-spacing:.09em}}.muted{{color:#8997a8;font-size:12px}}.error{{color:#ff9daa;background:#351820;border:1px solid #67303c;padding:9px 11px;border-radius:9px;margin:12px 0}}</style></head><body><div class='box'><h1>VELIA Control Center</h1><p>Owner-only administrative access. Identity is confirmed by Telegram; the browser never submits a Telegram user ID.</p>{error_html}<div class='step'><b>1. Confirm in Telegram</b><p>Open the production bot and request a one-time 5-minute code.</p><a href='{_e(link)}' rel='noreferrer'>Open Telegram</a></div><div class='step'><b>2. Enter one-time code</b><form method='post' action='/admin/login'><input name='code' inputmode='text' autocomplete='one-time-code' placeholder='XXXX-XXXX-XXXX-XXXX' minlength='16' maxlength='19' required><button type='submit'>Sign in securely</button></form></div><div class='muted'>{_e(ready_text)} · session expires after 8 hours · codes are one-time</div></div></body></html>"""


async def admin_login(request: web.Request) -> web.Response:
    existing = await asyncio.to_thread(_current_admin, request)
    if existing:
        return web.HTTPFound("/admin")
    if request.method == "GET":
        return web.Response(text=_login_page(), content_type="text/html", headers={"Cache-Control": "no-store"})
    if configured_admin_id() <= 0:
        return web.Response(text=_login_page("Owner identity is not configured on the server."), status=503, content_type="text/html", headers={"Cache-Control": "no-store"})
    form = await request.post()
    code = str(form.get("code", "") or "")
    result = await asyncio.to_thread(
        consume_admin_login_code,
        code,
        user_agent=request.headers.get("User-Agent", ""),
        ip=request.remote or "",
    )
    if not result.get("ok"):
        await asyncio.to_thread(
            record_admin_audit,
            admin_user_id=None,
            action="admin.login",
            target_type="admin_session",
            request_id=_request_id(request),
            success=False,
            error_code=str(result.get("error") or "login_failed"),
            source="web",
            ip=request.remote or "",
            user_agent=request.headers.get("User-Agent", ""),
        )
        await asyncio.sleep(0.2)
        return web.Response(text=_login_page("Invalid, expired, or already used code."), status=401, content_type="text/html", headers={"Cache-Control": "no-store"})
    response = web.HTTPFound("/admin")
    _set_admin_cookies(response, str(result["session_token"]), str(result["csrf_token"]))
    response.headers["Cache-Control"] = "no-store"
    await asyncio.to_thread(
        record_admin_audit,
        admin_user_id=int(result["admin_user_id"]),
        action="admin.login",
        target_type="admin_session",
        request_id=_request_id(request),
        success=True,
        source="web",
        ip=request.remote or "",
        user_agent=request.headers.get("User-Agent", ""),
    )
    return response


async def admin_logout(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    session = request.get("velia_admin_session") or {}
    raw = str(request.cookies.get(ADMIN_SESSION_COOKIE, "") or "")
    await asyncio.to_thread(revoke_admin_session, raw)
    await asyncio.to_thread(
        record_admin_audit,
        admin_user_id=int(session.get("admin_user_id") or 0) or None,
        action="admin.logout",
        target_type="admin_session",
        request_id=_request_id(request),
        success=True,
        source="web",
        ip=request.remote or "",
        user_agent=request.headers.get("User-Agent", ""),
    )
    response = web.HTTPFound("/admin/login")
    _clear_admin_cookies(response)
    response.headers["Cache-Control"] = "no-store"
    return response


def _overview_cards(data: Dict[str, Any]) -> str:
    users = data.get("users") or {}
    ai = data.get("ai") or {}
    gen = data.get("generations") or {}
    images = gen.get("images") or {}
    videos = gen.get("videos") or {}
    return f"""
<div class='grid'>
  <div class='card'><div class='label'>VELIA status</div><div class='value'>{_status(data.get('velia_status'))}</div><div class='hint'>Backend + database observation</div></div>
  <div class='card'><div class='label'>Users</div><div class='value'>{_metric(users.get('total'))}</div><div class='hint'>Active 24h: {_metric(users.get('active_24h'))}</div></div>
  <div class='card'><div class='label'>AI requests · 24h</div><div class='value'>{_metric(ai.get('requests_24h') if ai.get('available') else None)}</div><div class='hint'>1h {_metric(ai.get('requests_1h') if ai.get('available') else None)} · 7d {_metric(ai.get('requests_7d') if ai.get('available') else None)}</div></div>
  <div class='card'><div class='label'>AI est. cost · 24h</div><div class='value'>{'$'+format(float(ai.get('estimated_cost_24h_usd')),'.4f') if ai.get('available') and ai.get('estimated_cost_24h_usd') is not None else 'Unavailable'}</div><div class='hint'>Persisted provider usage only</div></div>
  <div class='card'><div class='label'>AI latency · 24h</div><div class='value'>{_metric(ai.get('avg_latency_24h_ms') if ai.get('available') else None, suffix=' ms')}</div><div class='hint'>Average persisted provider latency</div></div>
  <div class='card'><div class='label'>AI error rate · 24h</div><div class='value'>{_metric(ai.get('error_rate_24h') if ai.get('available') else None, suffix='%')}</div><div class='hint'>Unavailable when no requests</div></div>
  <div class='card'><div class='label'>Images · succeeded 24h</div><div class='value'>{_metric(images.get('succeeded_24h') if images.get('available') else None)}</div><div class='hint'>Queue/failures not persisted yet</div></div>
  <div class='card'><div class='label'>Videos · succeeded 24h</div><div class='value'>{_metric(videos.get('succeeded_24h') if videos.get('available') else None)}</div><div class='hint'>Queue/failures not persisted yet</div></div>
</div>"""


async def admin_dashboard(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    data = await asyncio.to_thread(overview_snapshot)
    errors = data.get("recent_errors") or []
    memory = data.get("velyon_memory") or {}
    deploy = data.get("deploy") or {}
    error_rows = "".join(
        f"<tr><td>{_e(item.get('timestamp') or 'Unavailable')}</td><td>{_e(item.get('source'))}</td><td><code>{_e(item.get('request_id') or '—')}</code></td><td>{_e(item.get('error') or 'Unavailable')}</td></tr>"
        for item in errors
    ) or "<tr><td colspan='4' class='muted'>No persisted recent errors found.</td></tr>"
    body = _overview_cards(data) + f"""
<div class='grid' style='margin-top:12px'>
 <div class='card wide'><h2>System health</h2><div class='action-row'><span>Backend {_status((data.get('backend') or {}).get('status'))}</span><span>Database {_status((data.get('database') or {}).get('status'))}</span><span>Velyon Core {_status((data.get('velyon_core') or {}).get('status'))}</span><span>Velyon Memory {_status(memory.get('status'))}</span></div><div class='hint'>DB latency: {_metric((data.get('database') or {}).get('latency_ms'), suffix=' ms')} · Memory latency: {_metric(memory.get('latency_ms'), suffix=' ms')}</div></div>
 <div class='card wide'><h2>Deployment</h2><div><span class='label'>Branch</span><br><code>{_e(deploy.get('deployed_branch') or 'Unavailable')}</code></div><div style='margin-top:9px'><span class='label'>Deployed SHA</span><br><code>{_e(deploy.get('deployed_commit_sha') or 'Unavailable')}</code></div><div class='hint'>Never inferred from GitHub HEAD.</div></div>
 <div class='card full'><h2>Recent persisted errors</h2><div class='table-wrap'><table><thead><tr><th>Time</th><th>Source</th><th>Request ID</th><th>Error</th></tr></thead><tbody>{error_rows}</tbody></table></div></div>
 <div class='card full'><h2>Unavailable telemetry</h2><div class='muted'>HTTP request rate, canonical active-user metric, a unified background-job registry, provider live health, and migration version are intentionally shown as Unavailable until a trustworthy source exists.</div></div>
</div>"""
    return web.Response(text=_layout("Overview", "Overview", _key(request), body), content_type="text/html")


async def admin_system(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    data = await asyncio.to_thread(overview_snapshot)
    memory = data.get("velyon_memory") or {}
    queue = memory.get("queue") or {}
    body = f"""<div class='grid'>
<div class='card wide'><h2>Core services</h2><div class='action-box'>
<div>Backend {_status((data.get('backend') or {}).get('status'))}</div>
<div>Database {_status((data.get('database') or {}).get('status'))} <span class='muted'>latency {_metric((data.get('database') or {}).get('latency_ms'),suffix=' ms')}</span></div>
<div>Velyon Core {_status((data.get('velyon_core') or {}).get('status'))}</div>
<div>Velyon Memory {_status(memory.get('status'))} <span class='muted'>latency {_metric(memory.get('latency_ms'),suffix=' ms')}</span></div>
</div></div>
<div class='card wide'><h2>Memory shadow worker</h2><pre>{_json_safe(queue)}</pre></div>
<div class='card full'><h2>Platform request telemetry</h2><div class='muted'>Unavailable: the current backend has no canonical HTTP request/latency/error telemetry table. VELIA AI requests are tracked separately and displayed under AI / Core.</div></div>
</div>"""
    return web.Response(text=_layout("System Health", "System", _key(request), body), content_type="text/html")


async def admin_users(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    q = str(request.query.get("q", "") or "").strip()[:120]
    try:
        limit = max(10, min(int(request.query.get("limit", "50") or 50), 100))
    except ValueError:
        limit = 50
    try:
        offset = max(0, int(request.query.get("offset", "0") or 0))
    except ValueError:
        offset = 0
    data = await asyncio.to_thread(list_users, query=q, limit=limit, offset=offset)
    rows = "".join(
        f"<tr><td><a href='/admin/users/{int(u.get('user_id') or 0)}'><code>{int(u.get('user_id') or 0)}</code></a></td><td>@{_e(u.get('username') or '—')}</td><td>{_e(u.get('first_name') or '—')}</td><td>{_metric(u.get('token_balance'))}</td><td>{'Banned' if u.get('is_banned') else 'Active'}</td><td>{'VIP' if u.get('is_vip') else '—'}</td><td>{_e(u.get('created_at') or 'Unavailable')}</td></tr>"
        for u in data.get("items") or []
    ) or "<tr><td colspan='7' class='muted'>No users found.</td></tr>"
    prev_offset = max(0, offset - limit)
    next_offset = offset + limit
    pagination = ""
    if not q:
        pagination = f"<div class='action-row' style='margin-top:12px'><a class='button' href='/admin/users?limit={limit}&offset={prev_offset}'>Previous</a><a class='button' href='/admin/users?limit={limit}&offset={next_offset}'>Next</a><span class='muted'>Total: {_metric(data.get('total'))}</span></div>"
    body = f"""<div class='card full'><form class='inline' method='get' action='/admin/users'><label>Search by Telegram ID, username, name<input name='q' value='{_e(q)}' placeholder='Search users'></label><label>Rows<select name='limit'><option{' selected' if limit==25 else ''}>25</option><option{' selected' if limit==50 else ''}>50</option><option{' selected' if limit==100 else ''}>100</option></select></label><button class='primary' type='submit'>Search</button></form></div><div class='card full'><div class='table-wrap'><table><thead><tr><th>Telegram ID</th><th>Username</th><th>Name</th><th>Tokens</th><th>Status</th><th>Plan</th><th>Registered</th></tr></thead><tbody>{rows}</tbody></table></div>{pagination}</div>"""
    return web.Response(text=_layout("Users", "Users", _key(request), body, request.query.get("msg", "")), content_type="text/html")


async def admin_user_detail(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    try:
        user_id = int(request.match_info.get("user_id") or 0)
    except ValueError:
        raise web.HTTPNotFound()
    data = await asyncio.to_thread(user_detail, user_id)
    if not data:
        raise web.HTTPNotFound(text="User not found")
    user = data.get("user") or {}
    analyses = data.get("recent_analyses") or []
    analysis_rows = "".join(
        f"<tr><td>{_e(a.get('created_at') or 'Unavailable')}</td><td>{_e(str(a.get('question') or '')[:180])}</td><td>{_e(a.get('category') or '—')}</td></tr>"
        for a in analyses
    ) or "<tr><td colspan='3' class='muted'>No recent analyses.</td></tr>"
    is_banned = bool(user.get("is_banned"))
    is_vip = bool(user.get("is_vip"))
    balance = int(user.get("token_balance") or 0)
    body = f"""<div class='grid'>
<div class='card wide'><h2>User profile</h2><div class='action-box'><div><span class='label'>Telegram ID</span><br><code>{user_id}</code></div><div><span class='label'>Username</span><br>@{_e(user.get('username') or '—')}</div><div><span class='label'>Name</span><br>{_e(user.get('first_name') or '—')}</div><div><span class='label'>Registered</span><br>{_e(user.get('created_at') or 'Unavailable')}</div><div><span class='label'>Last activity</span><br>Unavailable <span class='muted'>(canonical activity is not recorded)</span></div></div></div>
<div class='card wide'><h2>Account state</h2><div class='action-box'><div>Tokens <b>{balance}</b></div><div>Status <b>{'Banned' if is_banned else 'Active'}</b></div><div>VIP <b>{'Yes' if is_vip else 'No'}</b></div><div>Analyses <b>{_metric(user.get('total_analyses'))}</b> · Opportunities <b>{_metric(user.get('total_opportunities'))}</b></div></div></div>
<div class='card full'><h2>Safe owner actions</h2><div class='grid'>
<div class='card wide'><form method='post' action='/admin/users/{user_id}/actions/set-ban'><div class='action-row'><input type='hidden' name='enabled' value='{'0' if is_banned else '1'}'><label class='confirm'><input type='checkbox' name='confirmed' value='yes' required> Confirm {'unban' if is_banned else 'ban'}</label><button class='{'primary' if is_banned else 'danger'}' data-confirm='Confirm {'unban' if is_banned else 'ban'} for user {user_id}?'>{'Unban user' if is_banned else 'Ban user'}</button></div></form></div>
<div class='card wide'><form method='post' action='/admin/users/{user_id}/actions/set-vip'><div class='action-row'><input type='hidden' name='enabled' value='{'0' if is_vip else '1'}'><label class='confirm'><input type='checkbox' name='confirmed' value='yes' required> Confirm VIP change</label><button data-confirm='Confirm VIP change for user {user_id}?'>{'Remove VIP' if is_vip else 'Grant VIP'}</button></div></form></div>
<div class='card wide'><form method='post' action='/admin/users/{user_id}/actions/add-tokens'><div class='action-row'><label>Bonus tokens<input name='amount' type='number' min='1' max='10000000' value='10' required></label><label class='confirm'><input type='checkbox' name='confirmed' value='yes' required> Confirm credit</label><button class='primary' data-confirm='Credit bonus tokens to user {user_id}?'>Add tokens</button></div></form></div>
<div class='card wide'><form method='post' action='/admin/users/{user_id}/actions/set-tokens'><div class='action-row'><label>Exact balance<input name='amount' type='number' min='0' max='1000000000' value='{balance}' required></label><label class='confirm'><input type='checkbox' name='confirmed' value='yes' required> Confirm exact balance</label><button class='danger' data-confirm='Replace token balance for user {user_id}?'>Set balance</button></div></form></div>
</div></div>
<div class='card full'><h2>Recent analyses</h2><div class='table-wrap'><table><thead><tr><th>Time</th><th>Question</th><th>Category</th></tr></thead><tbody>{analysis_rows}</tbody></table></div></div>
</div>"""
    return web.Response(text=_layout(f"User {user_id}", "Users", _key(request), body, request.query.get("msg", "")), content_type="text/html")


async def admin_user_action(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    session = request.get("velia_admin_session") or {}
    admin_user_id = int(session.get("admin_user_id") or 0)
    try:
        user_id = int(request.match_info.get("user_id") or 0)
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid user")
    action = str(request.match_info.get("action") or "")
    form = await request.post()
    if str(form.get("confirmed", "")) != "yes":
        raise web.HTTPBadRequest(text="Explicit confirmation required")
    metadata = {
        "admin_user_id": admin_user_id,
        "user_id": user_id,
        "source": "web",
        "request_id": _request_id(request),
        "ip": request.remote or "",
        "user_agent": request.headers.get("User-Agent", ""),
    }
    if action == "set-ban":
        result = await asyncio.to_thread(set_user_banned, banned=str(form.get("enabled", "0")) == "1", **metadata)
    elif action == "set-vip":
        result = await asyncio.to_thread(set_user_vip_status, vip=str(form.get("enabled", "0")) == "1", **metadata)
    elif action == "add-tokens":
        try:
            amount = int(str(form.get("amount", "0") or "0"))
        except ValueError:
            amount = 0
        if amount <= 0 or amount > 10_000_000:
            raise web.HTTPBadRequest(text="Invalid token amount")
        result = await asyncio.to_thread(adjust_user_tokens, delta=amount, **metadata)
    elif action == "set-tokens":
        try:
            amount = int(str(form.get("amount", "-1") or "-1"))
        except ValueError:
            amount = -1
        if amount < 0 or amount > 1_000_000_000:
            raise web.HTTPBadRequest(text="Invalid token balance")
        result = await asyncio.to_thread(set_user_token_balance, amount=amount, **metadata)
    else:
        raise web.HTTPNotFound()
    if not result.get("ok"):
        return web.HTTPFound(f"/admin/users/{user_id}?msg={quote_plus(str(result.get('error') or 'Action failed'))}")
    return web.HTTPFound(f"/admin/users/{user_id}?msg={quote_plus('Updated and audited')}")


async def admin_ai(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    data = await asyncio.to_thread(ai_snapshot)
    routing = data.get("routing") or {}
    rows = "".join(
        f"<tr><td>{_e(name)}</td><td>{_status('online' if info.get('enabled') else 'unavailable')}</td><td>{_e(info.get('role') or '—')}</td><td><code>{_e(info.get('model') or 'Unavailable')}</code></td><td>{'Yes' if info.get('configured') else ('—' if 'configured' not in info else 'No')}</td></tr>"
        for name, info in routing.items()
    )
    breakdown = data.get("provider_model_breakdown_7d") or []
    br_rows = "".join(
        f"<tr><td>{_e(x.get('provider'))}</td><td><code>{_e(x.get('model'))}</code></td><td>{_metric(x.get('requests'))}</td><td>{_metric(x.get('success_rate'),suffix='%')}</td><td>{_metric(x.get('avg_latency_ms'),suffix=' ms')}</td><td>{_metric(x.get('input_tokens'))}</td><td>{_metric(x.get('output_tokens'))}</td><td>${float(x.get('estimated_cost_usd') or 0):.5f}</td></tr>"
        for x in breakdown
    ) or "<tr><td colspan='8' class='muted'>No persisted provider/model usage for the last 7 days.</td></tr>"
    usage = data.get("usage") or {}
    body = f"""<div class='grid'><div class='card'><div class='label'>Requests 24h</div><div class='value'>{_metric(usage.get('requests_24h') if usage.get('available') else None)}</div></div><div class='card'><div class='label'>Success 24h</div><div class='value'>{_metric(usage.get('success_24h') if usage.get('available') else None)}</div></div><div class='card'><div class='label'>Failures 24h</div><div class='value'>{_metric(usage.get('failed_24h') if usage.get('available') else None)}</div></div><div class='card'><div class='label'>Est. cost 7d</div><div class='value'>{'$'+format(float(usage.get('estimated_cost_7d_usd')),'.4f') if usage.get('available') and usage.get('estimated_cost_7d_usd') is not None else 'Unavailable'}</div></div><div class='card full'><h2>Configured routing</h2><div class='table-wrap'><table><thead><tr><th>Provider</th><th>Configured state</th><th>Role</th><th>Model</th><th>Key configured</th></tr></thead><tbody>{rows}</tbody></table></div><div class='hint'>This is configuration state, not a paid live probe. Raw credentials are never returned.</div></div><div class='card full'><h2>Provider / model usage · 7 days</h2><div class='table-wrap'><table><thead><tr><th>Provider</th><th>Model</th><th>Requests</th><th>Success</th><th>Latency</th><th>Input</th><th>Output</th><th>Est. cost</th></tr></thead><tbody>{br_rows}</tbody></table></div></div></div>"""
    return web.Response(text=_layout("AI / Velyon Core", "AI / Core", _key(request), body), content_type="text/html")


async def admin_errors(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    items = await asyncio.to_thread(recent_errors, 100)
    rows = "".join(
        f"<tr><td>{_e(x.get('timestamp') or 'Unavailable')}</td><td>{_e(x.get('source') or '—')}</td><td><code>{_e(x.get('request_id') or '—')}</code></td><td>{_e(x.get('user_id') or '—')}</td><td>{_e(x.get('error') or 'Unavailable')}</td></tr>"
        for x in items
    ) or "<tr><td colspan='5' class='muted'>No persisted errors found.</td></tr>"
    body = f"<div class='card full'><div class='table-wrap'><table><thead><tr><th>Time</th><th>Source</th><th>Request ID</th><th>User</th><th>Error</th></tr></thead><tbody>{rows}</tbody></table></div><div class='hint'>Stage 1 reads persisted VELIA chat and Velyon Memory errors. It does not pretend stdout/Railway logs are a structured error store.</div></div>"
    return web.Response(text=_layout("Errors", "Errors", _key(request), body), content_type="text/html")


async def admin_memory(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    health, queue = await asyncio.gather(
        asyncio.to_thread(velyon_memory_health),
        asyncio.to_thread(memory_queue_snapshot),
    )
    body = f"""<div class='grid'><div class='card wide'><h2>Velyon Memory service</h2><div class='value'>{_status(health.get('status'))}</div><div class='hint'>HTTP: {_metric(health.get('http_status'))} · latency {_metric(health.get('latency_ms'),suffix=' ms')} · version/SHA {_e(health.get('version') or 'Unavailable')}</div></div><div class='card wide'><h2>Shadow delivery queue</h2><pre>{_json_safe(queue)}</pre></div><div class='card full'><h2>Storage / operations</h2><div class='muted'>Unavailable in Stage 1: the private memory service currently exposes a health contract and the backend persists delivery outbox state, but there is no trusted storage-capacity/operation-rate telemetry contract to display.</div></div></div>"""
    return web.Response(text=_layout("Velyon Memory", "Memory", _key(request), body), content_type="text/html")


async def admin_deployments(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    d = deployment_snapshot()
    fields = [
        ("Railway service", d.get("service")),
        ("Environment", d.get("environment")),
        ("Production branch", d.get("production_branch")),
        ("Deployed branch", d.get("deployed_branch")),
        ("Deployed commit SHA", d.get("deployed_commit_sha")),
        ("Deployment ID", d.get("deployment_id")),
        ("Replica ID", d.get("replica_id")),
        ("Application version", d.get("application_version")),
        ("Deployment time", d.get("deployed_at")),
        ("Migration version", d.get("migration_version")),
    ]
    rows = "".join(f"<tr><td>{_e(label)}</td><td><code>{_e(value or 'Unavailable')}</code></td></tr>" for label, value in fields)
    body = f"<div class='card full'><div class='table-wrap'><table><tbody>{rows}</tbody></table></div><div class='hint'>The deployed SHA is read only from runtime deployment metadata. GitHub branch HEAD is deliberately not substituted when Railway metadata is missing.</div></div>"
    return web.Response(text=_layout("Deployments", "Deployments", _key(request), body), content_type="text/html")


async def admin_audit(request: web.Request) -> web.Response:
    denied = await _guard(request)
    if denied:
        return denied
    items = await asyncio.to_thread(audit_snapshot, 200)
    rows = "".join(
        f"<tr><td>{_e(x.get('timestamp'))}</td><td><code>{_e(x.get('admin_user_id') or '—')}</code></td><td>{_e(x.get('action'))}</td><td>{_e(x.get('target_type') or '—')} {_e(x.get('target_id') or '')}</td><td><code>{_e(x.get('request_id') or '—')}</code></td><td>{'Success' if x.get('success') else 'Failed'}</td><td>{_e(x.get('source') or '—')}</td></tr>"
        for x in items
    ) or "<tr><td colspan='7' class='muted'>Audit log is empty.</td></tr>"
    body = f"<div class='card full'><div class='table-wrap'><table><thead><tr><th>Time</th><th>Admin</th><th>Action</th><th>Target</th><th>Request ID</th><th>Result</th><th>Source</th></tr></thead><tbody>{rows}</tbody></table></div><div class='hint'>Sensitive fields are redacted before durable audit storage. IP is stored only as a SHA-256 hash.</div></div>"
    return web.Response(text=_layout("Audit Log", "Audit", _key(request), body), content_type="text/html")


def setup_admin_routes(app: web.Application) -> None:
    if app.get("velia_control_center_routes_installed"):
        return
    app.router.add_get("/admin/login", admin_login)
    app.router.add_post("/admin/login", admin_login)
    app.router.add_post("/admin/logout", admin_logout)
    app.router.add_get("/admin", admin_dashboard)
    app.router.add_get("/admin/system", admin_system)
    app.router.add_get("/admin/users", admin_users)
    app.router.add_get("/admin/users/{user_id}", admin_user_detail)
    app.router.add_post("/admin/users/{user_id}/actions/{action}", admin_user_action)
    app.router.add_get("/admin/ai", admin_ai)
    app.router.add_get("/admin/errors", admin_errors)
    app.router.add_get("/admin/memory", admin_memory)
    app.router.add_get("/admin/deployments", admin_deployments)
    app.router.add_get("/admin/audit", admin_audit)
    app["velia_control_center_routes_installed"] = True
