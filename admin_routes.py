import os
from datetime import datetime, timedelta
from html import escape
import json
import re
from aiohttp import web

from db.database import (
    get_setting,
    set_setting,
    get_all_users,
    get_recent_analyses,
    add_tokens,
    set_user_vip,
    set_user_ban,
)
from services.payment_service import get_pricing_payload
from services.admin_metrics_service import summarize_users
from services.admin_analysis_service import summarize_quality, extract_edge, extract_decision, extract_source_count


# TODO: Replace ADMIN_SECRET_KEY auth with Telegram initData validation + ADMIN_TELEGRAM_IDS.

SECTIONS = [
    ("Dashboard", "/admin"),
    ("Users", "/admin/users"),
    ("Revenue", "/admin/revenue"),
    ("Tokens", "/admin/tokens"),
    ("Settings", "/admin/settings"),
    ("Content", "/admin/content"),
    ("Analyses", "/admin/analyses"),
    ("Quality", "/admin/quality"),
    ("Signals", "/admin/signals"),
    ("Payments", "/admin/payments"),
    ("WebApp", "/admin/webapp"),
    ("Audit", "/admin/audit"),
]

SETTINGS_GROUPS = {
    "Telegram / TON": [
        "paid_mode", "token_price_ton", "analysis_price_tokens", "opportunity_price_tokens",
        "cached_signal_price_tokens", "subscription_price_ton", "subscription_days", "telegram_ton_payments_enabled",
    ],
    "Web / Future Internet": [
        "webapp_enabled", "web_registration_enabled", "web_payments_enabled", "web_ton_enabled",
        "web_tron_usdt_enabled", "web_evm_usdt_enabled", "web_card_payments_enabled",
        "web_analysis_price_usd", "web_subscription_enabled", "maintenance_mode", "announcement_text",
    ],
    "Agents / Analysis": [
        "sports_agent_enabled", "trading_plan_enabled", "turbo_btc_enabled",
        "crypto_threshold_agent_enabled", "opportunity_agent_enabled", "gemini_deep_mode_enabled",
    ],
    "Authors / Donations / Watchlist": [
        "authors_enabled", "donations_enabled", "watchlist_enabled", "author_status_price_ton",
        "platform_fee_percent", "min_donation_ton", "min_withdrawal_ton", "watchlist_extra_slots_price",
        "watchlist_extra_slots_count", "watchlist_limit_regular", "watchlist_limit_vip",
    ],
    "Legal / Texts": ["disclaimer_ru", "disclaimer_en", "announcement_text"],
}


def _admin_key(request: web.Request) -> str:
    return request.query.get("key", "")


async def _require_admin(request: web.Request):
    secret = os.getenv("ADMIN_SECRET_KEY", "")
    if not secret:
        return web.Response(text="Admin is not configured", status=403)
    provided = _admin_key(request)
    if request.method == "POST" and not provided:
        form = await request.post()
        provided = str(form.get("key", ""))
    if provided != secret:
        return web.Response(text="Forbidden", status=403)
    return None


def _layout(title: str, section: str, key: str, body: str, flash: str = "") -> str:
    nav = []
    for name, path in SECTIONS:
        active = " active" if name == section else ""
        nav.append(f"<a class='nav{active}' href='{path}?key={escape(key)}'>{name}</a>")
    flash_html = f"<div class='flash'>{escape(flash)}</div>" if flash else ""
    return f"""<!doctype html><html><head><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{escape(title)}</title><style>
body{{margin:0;background:#070b17;color:#e5e7eb;font:14px/1.4 Inter,Arial,sans-serif}}
.wrap{{max-width:1200px;margin:auto;padding:14px}} .top{{background:linear-gradient(90deg,#312e81,#1d4ed8);padding:12px;border-radius:12px}}
.navs{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}} .nav{{padding:7px 10px;border-radius:8px;background:#111827;color:#cbd5e1;text-decoration:none}}
.nav.active{{background:#1e3a8a;color:#fff}} .card{{background:#0f172a;border:1px solid #1f2937;border-radius:12px;padding:12px;margin:10px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}} table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:7px;border-bottom:1px solid #1f2937;vertical-align:top}} .badge{{padding:2px 8px;border-radius:999px;background:#1f2937}}
.ok{{color:#34d399}} .warn{{color:#fbbf24}} .muted{{color:#94a3b8}} input,button,textarea,select{{background:#0b1220;border:1px solid #334155;color:#e5e7eb;padding:6px;border-radius:8px}}
.flash{{background:#14532d;padding:8px;border-radius:8px;margin:10px 0}}
.danger{{background:#7f1d1d}} .btn-danger{{border-color:#7f1d1d}} .right{{text-align:right}}
.table-scroll{{overflow:auto}}.mobile-card-list{{display:none}}.truncate{{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
@media (max-width:720px){{.desktop-table{{display:none}}.mobile-card-list{{display:block}}.wrap{{padding:8px}}th,td{{padding:6px;font-size:12px}}}}
</style></head><body><div class='wrap'><div class='top'><h2 style='margin:0'>DeepAlpha Admin v1</h2><div class='navs'>{''.join(nav)}</div></div>{flash_html}{body}</div></body></html>"""



SAFE_SETTING_KEYS = {"paid_mode","token_price_ton","analysis_price_tokens","subscription_price_ton","announcement_text","webapp_enabled","web_payments_enabled","web_ton_enabled","web_tron_usdt_enabled","web_evm_usdt_enabled","web_card_payments_enabled"}
PROTECTED_KEYS = {"paid_mode","token_price_ton","analysis_price_tokens","subscription_price_ton"}

def _is_on(value: str) -> bool:
    return str(value).strip().lower() in {"1", "on", "true", "yes", "enabled"}


def _badge_bool(value: bool) -> str:
    text = "ON" if value else "OFF"
    css = "ok" if value else "warn"
    return f"<span class='badge {css}'>{text}</span>"


async def admin_dashboard(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    users = get_all_users(limit=50000)
    analyses = get_recent_analyses(limit=20000)
    now = datetime.utcnow()
    today = now.date().isoformat()
    seven = (now - timedelta(days=7)).date().isoformat()
    today_count = sum(1 for a in analyses if str(a.get("created_at", "")).startswith(today))
    seven_count = sum(1 for a in analyses if str(a.get("created_at", ""))[:10] >= seven)
    um = summarize_users(users)
    qm = summarize_quality(analyses[:1000])
    latest_users_rows = "".join([f"<tr><td>{u.get('user_id')}</td><td>{escape(str(u.get('username','')))}</td><td>{escape(str(u.get('first_name','')))}</td><td>{escape(str(u.get('created_at','')))}</td></tr>" for u in users[:10]])
    latest_analyses_rows = "".join([f"<tr><td>{escape(str(a.get('created_at','')))}</td><td>{a.get('user_id',0)}</td><td>{escape(str(a.get('question',''))[:90])}</td><td>{escape(str(a.get('category','')))}</td></tr>" for a in analyses[:10]])
    body = f"""
    <div class='grid'>
      <div class='card'>Total users<br><b>{um['total_users']}</b></div><div class='card'>New users today<br><b>{um['new_today']}</b></div>
      <div class='card'>New users 7d<br><b>{um['new_7d']}</b></div><div class='card'>Active users 7d<br><b>{um['active_7d']}</b></div>
      <div class='card'>Total token balance<br><b>{um['token_sum']}</b></div><div class='card'>Total analyses<br><b>{len(analyses)}</b></div><div class='card'>Total opportunities<br><b>{um['opportunities']}</b></div>
      <div class='card'>Analyses today<br><b>{today_count}</b></div><div class='card'>Analyses 7d<br><b>{seven_count}</b></div>
      <div class='card'>VIP users<br><b>{um['vip']}</b></div><div class='card'>Banned users<br><b>{um['banned']}</b></div>
      <div class='card'>Authors<br><b>{um['authors']}</b></div><div class='card'>Paid mode<br><b>{escape(get_setting('paid_mode','off'))}</b></div>
      <div class='card'>Token price TON<br><b>{escape(get_setting('token_price_ton','0.1'))}</b></div><div class='card'>Analysis price tokens<br><b>{escape(get_setting('analysis_price_tokens','10'))}</b></div>
      <div class='card'>Sub price TON<br><b>{escape(get_setting('subscription_price_ton','1'))}</b></div><div class='card'>WebApp enabled<br><b>{_badge_bool(_is_on(get_setting('webapp_enabled','on')))}</b></div>
    </div>
    <div class='card'>Feature flags: sports={escape(get_setting('sports_agent_enabled','off'))}, trading_plan={escape(get_setting('trading_plan_enabled','off'))}, turbo_btc={escape(get_setting('turbo_btc_enabled','off'))}, crypto_threshold={escape(get_setting('crypto_threshold_agent_enabled','off'))}, opportunity={escape(get_setting('opportunity_agent_enabled','off'))}</div>
    <div class='card'>Payment flags: tg_ton={escape(get_setting('telegram_ton_payments_enabled','on'))}, web_ton={escape(get_setting('web_ton_enabled','off'))}, web_tron={escape(get_setting('web_tron_usdt_enabled','off'))}, web_evm={escape(get_setting('web_evm_usdt_enabled','off'))}, web_card={escape(get_setting('web_card_payments_enabled','off'))}</div>
    <div class='card'><b>Quick links:</b> <a href='/admin/users?key={escape(key)}'>Users</a> · <a href='/admin/settings?key={escape(key)}'>Settings</a> · <a href='/admin/analyses?key={escape(key)}'>Analyses</a> · <a href='/admin/payments?key={escape(key)}'>Payments</a> · <a href='/admin/quality?key={escape(key)}'>Quality</a></div>
    <div class='card'><h3>Latest users</h3><table><tr><th>user_id</th><th>username</th><th>first_name</th><th>created</th></tr>{latest_users_rows or '<tr><td colspan=4>not available yet</td></tr>'}</table></div>
    <div class='card'><h3>Latest analyses</h3><table><tr><th>time</th><th>user_id</th><th>question</th><th>category</th></tr>{latest_analyses_rows or '<tr><td colspan=4>not available yet</td></tr>'}</table></div>
    <div class='card'>Quality snapshot: no_sources={qm.get('no_sources','n/a')}, low_conf={qm.get('low_confidence','n/a')}, edge_zero={qm.get('edge_zero','n/a')}, NO TRADE={qm.get('decisions',{}).get('NO TRADE',0)}, WAIT={qm.get('decisions',{}).get('WAIT',0)}</div>
    """
    return web.Response(text=_layout("Dashboard", "Dashboard", key, body), content_type="text/html")


async def admin_users(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    q = request.query.get("q", "").strip().lower()
    limit_raw = request.query.get("limit", "100")
    limit = min(max(int(limit_raw) if limit_raw.isdigit() else 100, 1), 500)
    users = get_all_users(limit=5000)
    if q:
        users = [u for u in users if q in str(u.get("user_id", "")).lower() or q in str(u.get("username", "")).lower() or q in str(u.get("first_name", "")).lower()]
    rows = []
    for u in users:
        uid = int(u.get("user_id", 0) or 0)
        badges = []
        if u.get("is_vip"): badges.append("VIP")
        if u.get("is_banned"): badges.append("BANNED")
        if u.get("is_author"): badges.append("AUTHOR")
        if u.get("subscription_until"): badges.append("SUB")
        rows.append(f"""<tr><td>{uid}</td><td>{escape(str(u.get('username','')))}</td><td>{escape(str(u.get('first_name','')))}</td><td>{escape(str(u.get('language','')))}</td><td>{u.get('token_balance',0)}</td><td>{' '.join([f'<span class=badge>{b}</span>' for b in badges])}</td><td>{u.get('total_analyses',0)}</td><td>{u.get('total_opportunities',0)}</td><td>{escape(str(u.get('created_at','')))}</td><td>{escape(str(u.get('updated_at','')))}</td>
        <td>
          <form method='post' action='/admin/users/{uid}/tokens/add?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'><input name='amount' value='10' size='4'><button onclick="return confirm('Add tokens?')">Add</button></form>
          <form method='post' action='/admin/users/{uid}/tokens/subtract?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'><input name='amount' value='10' size='4'><button class='btn-danger' onclick="return confirm('Subtract tokens?')">Sub</button></form>
          <form method='post' action='/admin/users/{uid}/vip?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'><select name='enabled'><option value='1'>VIP on</option><option value='0'>VIP off</option></select><button>Set</button></form>
          <form method='post' action='/admin/users/{uid}/ban?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'><select name='enabled'><option value='1'>Ban</option><option value='0'>Unban</option></select><button>Set</button></form>
        </td></tr>""")
    rows = rows[:limit]
    body = f"<div class='card'><form><input type='hidden' name='key' value='{escape(key)}'><input name='q' value='{escape(q)}' placeholder='search user_id/username/name'><input name='limit' value='{limit}' size='4'><button>Search</button></form></div><div class='card'><table><tr><th>user_id</th><th>username</th><th>first_name</th><th>lang</th><th>tokens</th><th>status</th><th>analyses</th><th>opps</th><th>created</th><th>last_seen</th><th>actions</th></tr>{''.join(rows) or '<tr><td colspan=11>not available yet</td></tr>'}</table></div>"
    return web.Response(text=_layout("Users", "Users", key, body, request.query.get("msg", "")), content_type="text/html")


async def admin_user_detail(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    uid = int(request.match_info.get("user_id", "0") or 0)
    users = get_all_users(limit=50000)
    user = next((u for u in users if int(u.get("user_id", 0) or 0) == uid), None)
    analyses = [a for a in get_recent_analyses(limit=500) if int(a.get("user_id", 0) or 0) == uid][:20]
    if not user:
        body = "<div class='card'>User not found.</div>"
    else:
        rows = "".join([f"<tr><td>{escape(str(a.get('created_at','')))}</td><td>{escape(str(a.get('question','')))}</td><td>{escape(str(a.get('category','')))}</td></tr>" for a in analyses])
        body = f"<div class='card'><h3>User {uid}</h3><pre>{escape(str(user))}</pre></div><div class='card'><h3>Recent analyses</h3><div class='table-scroll'><table><tr><th>time</th><th>question</th><th>category</th></tr>{rows or '<tr><td colspan=3>not available yet</td></tr>'}</table></div></div>"
    return web.Response(text=_layout("User detail", "Users", key, body), content_type="text/html")


async def admin_user_action(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    uid = int(request.match_info.get("user_id", "0") or 0)
    action = request.match_info.get("action", "")
    form = await request.post()
    msg = "Updated"
    if uid <= 0:
        msg = "Invalid user_id"
    elif action in {"tokens/add", "tokens/subtract"}:
        amount_raw = str(form.get("amount", "0"))
        if not amount_raw.isdigit() or int(amount_raw) <= 0:
            msg = "Amount must be a positive integer"
        else:
            amount = int(amount_raw)
            add_tokens(uid, amount if action.endswith("add") else -amount)
            msg = f"Tokens updated for {uid}"
    elif action == "vip":
        set_user_vip(uid, str(form.get("enabled", "1")) == "1")
    elif action == "ban":
        set_user_ban(uid, str(form.get("enabled", "1")) == "1")
    return web.HTTPFound(f"/admin/users?key={escape(key)}&msg={escape(msg)}")


async def admin_settings(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    flash = request.query.get("msg", "")
    if request.method == "POST":
        form = await request.post()
        errs = []
        for group in SETTINGS_GROUPS.values():
            for k in group:
                if k in form:
                    try:
                        set_setting(k, str(form.get(k, "")))
                    except Exception:
                        errs.append(k)
        msg = "Settings saved" if not errs else f"Saved with errors: {', '.join(errs)}"
        return web.HTTPFound(f"/admin/settings?key={escape(key)}&msg={escape(msg)}")
    parts = []
    for group, keys in SETTINGS_GROUPS.items():
        fields = "".join([f"<label>{escape(k)}<br><input name='{escape(k)}' value='{escape(get_setting(k,''))}'></label><br><br>" for k in keys])
        parts.append(f"<div class='card'><h3>{escape(group)}</h3>{fields}</div>")
    body = f"<form method='post' action='/admin/settings?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'>{''.join(parts)}<div class='card'><button>Save all settings</button></div></form>"
    return web.Response(text=_layout("Settings", "Settings", key, body, flash), content_type="text/html")


async def admin_analyses(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    limit_raw = request.query.get("limit", "50")
    limit = min(max(int(limit_raw) if limit_raw.isdigit() else 50, 1), 300)
    analyses = get_recent_analyses(limit=limit)
    if not analyses:
        body = "<div class='card muted'>Structured analysis history is not available yet. Stage 2 will add analysis history storage/API.</div>"
    else:
        rows = "".join([f"<tr><td>{escape(str(a.get('id','')))}</td><td>{escape(str(a.get('created_at','')))}</td><td>{a.get('user_id',0)}</td><td>{escape(str(a.get('question',''))[:100])}</td><td>{escape(str(a.get('category','')))}</td><td>{escape(str(a.get('market_probability','')))}</td><td>{escape(str(a.get('system_probability','')))}</td><td>{escape(str(extract_decision(a) or ''))}</td><td>{extract_edge(a)}</td><td>{extract_source_count(a)}</td><td>{escape(str(a.get('url','')))}</td><td><a href='/admin/analyses/{a.get('id')}?key={escape(key)}'>view</a></td></tr>" for a in analyses])
        body = f"<div class='card'><form><input type='hidden' name='key' value='{escape(key)}'><input name='limit' value='{limit}' size='4'><button>Apply</button></form><div class='table-scroll'><table><tr><th>id</th><th>timestamp</th><th>user_id</th><th>question</th><th>category</th><th>market_probability</th><th>model</th><th>decision</th><th>edge</th><th>sources</th><th>url</th><th>detail</th></tr>{rows}</table></div></div>"
    return web.Response(text=_layout("Analyses", "Analyses", key, body), content_type="text/html")


async def admin_analysis_detail(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    aid = int(request.match_info.get("id", "0") or 0)
    analyses = get_recent_analyses(limit=500)
    found = next((a for a in analyses if int(a.get("id", 0) or 0) == aid), None)
    if not found:
        body = "<div class='card muted'>Analysis detail is not available yet for this id.</div>"
    else:
        body = f"<div class='card'><h3>Analysis #{aid}</h3><p><b>Question:</b> {escape(str(found.get('question','')))}</p><p><b>Final answer:</b> {escape(str(found.get('conclusion','')))}</p><pre>{escape(str(found))}</pre><p class='muted'>Trading plan/sports/news detail fields: not available yet in structured storage.</p></div>"
    return web.Response(text=_layout("Analysis detail", "Analyses", key, body), content_type="text/html")


async def admin_quality(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    analyses = get_recent_analyses(limit=1000)
    no_url = sum(1 for a in analyses if not str(a.get("url", "")).strip())
    no_trade = sum(1 for a in analyses if "NO TRADE" in str(a.get("conclusion", "")).upper())
    watch = sum(1 for a in analyses if "WATCH" in str(a.get("conclusion", "")).upper())
    consider = sum(1 for a in analyses if "CONSIDER" in str(a.get("conclusion", "")).upper())
    body = f"<div class='grid'><div class='card'>Analyses without sources<br><b>{no_url}</b></div><div class='card'>NO TRADE count<br><b>{no_trade}</b></div><div class='card'>WATCH count<br><b>{watch}</b></div><div class='card'>CONSIDER count<br><b>{consider}</b></div></div><div class='card'>Low data_quality: not available yet</div><div class='card'>Edge=0 analyses: not available yet</div><div class='card muted'>TODO Stage 2 structured analytics storage.</div>"
    return web.Response(text=_layout("Quality", "Quality", key, body), content_type="text/html")


async def admin_payments(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    pricing_preview = escape(json.dumps(get_pricing_payload(), ensure_ascii=False, indent=2))
    body = f"""
    <div class='card'><h3>Telegram Payments</h3>
      TON enabled: <span class='ok'>{escape(get_setting('telegram_ton_payments_enabled','on'))}</span><br>
      token_price_ton: {escape(get_setting('token_price_ton','0.1'))}<br>
      subscription_price_ton: {escape(get_setting('subscription_price_ton','1'))}<br>
      /api/pending status: active route<br>TON Connect manifest status: active route
    </div>
    <div class='card'><h3>Future Web Payments</h3>
      web_payments_enabled={escape(get_setting('web_payments_enabled','off'))}, web_ton_enabled={escape(get_setting('web_ton_enabled','off'))}, web_tron_usdt_enabled={escape(get_setting('web_tron_usdt_enabled','off'))}, web_evm_usdt_enabled={escape(get_setting('web_evm_usdt_enabled','off'))}, web_card_payments_enabled={escape(get_setting('web_card_payments_enabled','off'))}, web_analysis_price_usd={escape(get_setting('web_analysis_price_usd','0'))}
    </div>
    <div class='card'><table><tr><th>Provider</th><th>Channel</th><th>Enabled</th><th>Status</th><th>Notes</th></tr>
      <tr><td>TON</td><td>Telegram</td><td>enabled</td><td>active</td><td>current working payment flow</td></tr>
      <tr><td>TON</td><td>Web</td><td>{escape(get_setting('web_ton_enabled','off'))}</td><td>placeholder</td><td>future open web</td></tr>
      <tr><td>TRON USDT</td><td>Web</td><td>{escape(get_setting('web_tron_usdt_enabled','off'))}</td><td>placeholder</td><td>Stage 2/3</td></tr>
      <tr><td>EVM USDT/USDC</td><td>Web</td><td>{escape(get_setting('web_evm_usdt_enabled','off'))}</td><td>placeholder</td><td>Stage 2/3</td></tr>
      <tr><td>Card</td><td>Web</td><td>{escape(get_setting('web_card_payments_enabled','off'))}</td><td>placeholder</td><td>future provider</td></tr>
    </table></div>
    <div class='card'><h3>/api/pricing preview</h3><pre>{pricing_preview}</pre></div>
    """
    return web.Response(text=_layout("Payments", "Payments", key, body), content_type="text/html")


async def admin_webapp(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    body = "<div class='card'>Current Telegram WebApp: active</div><div class='card'>webapp/index.html: untouched/current</div><div class='card'>Future React frontend: not built yet</div><div class='card'>Public API readiness: /api/health, /api/pricing, /api/settings/public</div><div class='card'>Planned future APIs: /api/me, /api/balance, /api/analyze, /api/analyses/history, /api/analyses/{id}, /api/payments/options</div>"
    return web.Response(text=_layout("WebApp", "WebApp", key, body), content_type="text/html")


async def admin_audit(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    body = "<div class='card'>Admin audit log is not available yet.</div><div class='card muted'>TODO Stage 2: log admin actions: token changes, VIP changes, bans, settings updates.</div>"
    return web.Response(text=_layout("Audit", "Audit", key, body), content_type="text/html")


async def admin_revenue(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    users = get_all_users(limit=50000)
    token_sum = sum(int(u.get("token_balance", 0) or 0) for u in users)
    price = float(get_setting("token_price_ton", "0.1") or 0)
    est = round(token_sum * price, 4)
    body = f"<div class='grid'><div class='card'>Total token balance<br><b>{token_sum}</b></div><div class='card'>token_price_ton<br><b>{price}</b></div><div class='card'>Estimated token value TON<br><b>{est}</b></div><div class='card'>subscription_price_ton<br><b>{escape(get_setting('subscription_price_ton','1'))}</b></div><div class='card'>author_status_price_ton<br><b>{escape(get_setting('author_status_price_ton','5'))}</b></div><div class='card'>platform_fee_percent<br><b>{escape(get_setting('platform_fee_percent','20'))}</b></div></div><div class='card muted'>Pending/completed payment event storage is not available yet — needs payment event storage.</div>"
    return web.Response(text=_layout("Revenue", "Revenue", key, body), content_type="text/html")


async def admin_tokens(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    users = get_all_users(limit=50000)
    total = sum(int(u.get("token_balance", 0) or 0) for u in users)
    zero = sum(1 for u in users if int(u.get("token_balance", 0) or 0) <= 0)
    high = [u for u in users if int(u.get("token_balance", 0) or 0) >= 1000][:20]
    avg = round(total / len(users), 2) if users else 0
    rows = "".join([f"<tr><td>{u.get('user_id')}</td><td>{escape(str(u.get('username','')))}</td><td>{u.get('token_balance',0)}</td></tr>" for u in sorted(users, key=lambda x: int(x.get('token_balance',0) or 0), reverse=True)[:10]])
    body = f"<div class='grid'><div class='card'>Total balance<br><b>{total}</b></div><div class='card'>Average balance<br><b>{avg}</b></div><div class='card'>Zero-balance users<br><b>{zero}</b></div><div class='card'>High-balance users (>=1000)<br><b>{len(high)}</b></div></div><div class='card'>analysis_price_tokens={escape(get_setting('analysis_price_tokens','10'))}, cached_signal_price_tokens={escape(get_setting('cached_signal_price_tokens','5'))}, opportunity_price_tokens={escape(get_setting('opportunity_price_tokens','20'))}, watchlist_extra_slots_price={escape(get_setting('watchlist_extra_slots_price','20'))}</div><div class='card'><h3>Top balances</h3><div class='table-scroll'><table><tr><th>user_id</th><th>username</th><th>balance</th></tr>{rows}</table></div></div><div class='card muted'>Bulk grant is disabled in v1 for safety.</div>"
    return web.Response(text=_layout("Tokens", "Tokens", key, body), content_type="text/html")


async def admin_signals(request: web.Request):
    denied = await _require_admin(request)
    if denied:
        return denied
    key = _admin_key(request)
    rows = get_recent_analyses(limit=300)
    qm = summarize_quality(rows)
    body = f"<div class='grid'><div class='card'>Actionable (CONSIDER/WATCH)<br><b>{qm.get('decisions',{}).get('CONSIDER',0)+qm.get('decisions',{}).get('WATCH',0)}</b></div><div class='card'>NO TRADE<br><b>{qm.get('decisions',{}).get('NO TRADE',0)}</b></div><div class='card'>WAIT<br><b>{qm.get('decisions',{}).get('WAIT',0)}</b></div><div class='card'>Average edge<br><b>{qm.get('avg_edge','n/a')}</b></div></div><div class='card muted'>Signals dashboard is based on currently available analysis rows. Structured signal history will improve this in Stage 2.</div>"
    return web.Response(text=_layout("Signals", "Signals", key, body), content_type="text/html")


def setup_admin_routes(app: web.Application) -> None:
    app.router.add_get("/admin", admin_dashboard)
    app.router.add_get("/admin/users", admin_users)
    app.router.add_get("/admin/users/{user_id}", admin_user_detail)
    app.router.add_post("/admin/users/{user_id}/{action:.+}", admin_user_action)
    app.router.add_get("/admin/revenue", admin_revenue)
    app.router.add_get("/admin/tokens", admin_tokens)
    app.router.add_get("/admin/settings", admin_settings)
    app.router.add_post("/admin/settings", admin_settings)
    app.router.add_get("/admin/analyses", admin_analyses)
    app.router.add_get("/admin/analyses/{id}", admin_analysis_detail)
    app.router.add_get("/admin/quality", admin_quality)
    app.router.add_get("/admin/signals", admin_signals)
    app.router.add_get("/admin/payments", admin_payments)
    app.router.add_get("/admin/webapp", admin_webapp)
    app.router.add_get("/admin/audit", admin_audit)
