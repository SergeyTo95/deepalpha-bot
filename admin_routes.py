import os, json
from html import escape
from aiohttp import web
from db.database import get_all_users, get_recent_analyses, get_setting, set_setting, add_tokens, set_user_vip, set_user_ban, set_user_language
from services.admin_analysis_service import extract_decision, extract_edge, extract_source_count

SECTIONS=[("Dashboard","/admin"),("Users","/admin/users"),("Revenue","/admin/revenue"),("Tokens","/admin/tokens"),("Analyses","/admin/analyses"),("Quality","/admin/quality"),("Signals","/admin/signals"),("Settings","/admin/settings"),("Content","/admin/content"),("Payments","/admin/payments"),("WebApp","/admin/webapp"),("Audit","/admin/audit")]

BOOL_KEYS={"paid_mode","webapp_enabled","maintenance_mode","sports_agent_enabled","trading_plan_enabled","turbo_btc_enabled","telegram_ton_payments_enabled","web_payments_enabled","web_ton_enabled","web_tron_usdt_enabled","web_evm_usdt_enabled","web_card_payments_enabled"}


def _key(req): return req.query.get("key","")

async def _guard(req):
    sec=os.getenv("ADMIN_SECRET_KEY","")
    if not sec: return web.Response(text="Admin is not configured",status=403)
    k=_key(req)
    if req.method=="POST" and not k:
        form=await req.post(); k=str(form.get("key", ""))
    if k!=sec: return web.Response(text="Forbidden",status=403)

def _layout(title,active,key,body,flash=""):
    nav="".join([f"<a class='nav{' active' if n==active else ''}' href='{p}?key={escape(key)}'>{n}</a>" for n,p in SECTIONS])
    return f"""<!doctype html><html><head><meta name='viewport' content='width=device-width, initial-scale=1'><title>{escape(title)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;overflow-x:hidden;background:#070b17;color:#e5e7eb;font:14px Arial}} .wrap{{max-width:1200px;margin:auto;padding:10px}} .top{{background:linear-gradient(90deg,#312e81,#1d4ed8);padding:10px;border-radius:10px}}
.navs{{display:flex;gap:6px;flex-wrap:wrap;overflow:auto}} .nav{{padding:7px 10px;border-radius:8px;background:#111827;color:#cbd5e1;text-decoration:none;white-space:nowrap}} .active{{background:#1e3a8a;color:#fff}}
.card{{background:#0f172a;border:1px solid #1f2937;border-radius:10px;padding:10px;margin:8px 0;max-width:100%}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px}}
.table-scroll{{overflow:auto}} table{{width:100%;border-collapse:collapse}} th,td{{padding:6px;border-bottom:1px solid #1f2937;vertical-align:top;overflow-wrap:anywhere}} input,select,textarea,button{{max-width:100%;background:#0b1220;color:#e5e7eb;border:1px solid #334155;padding:6px;border-radius:8px}}
.desktop-table{{display:block}} .mobile-card-list{{display:none}} .mobile-card{{background:#111827;border-radius:8px;padding:8px;margin:8px 0}} .field-row{{display:flex;gap:6px;flex-wrap:wrap}} .actions-grid{{display:grid;grid-template-columns:1fr 1fr;gap:6px}} .pill{{display:inline-block;background:#1f2937;border-radius:999px;padding:2px 8px}} .danger{{background:#7f1d1d}} .success{{color:#34d399}} .muted{{color:#94a3b8}} .truncate-2{{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}} .truncate-4{{display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}}
@media (max-width:760px){{.desktop-table{{display:none}} .mobile-card-list{{display:block}} body{{font-size:13px}} .wrap{{padding:6px}} }}
</style></head><body><div class='wrap'><div class='top'><h2 style='margin:0'>DeepAlpha Admin Center</h2><div class='navs'>{nav}</div></div>{f"<div class='card success'>{escape(flash)}</div>" if flash else ''}{body}</div></body></html>"""

async def admin_dashboard(r):
    d=await _guard(r)
    if d:return d
    key=_key(r); users=get_all_users(5000); an=get_recent_analyses(500)
    body=f"<div class='grid'><div class='card'>Users <b>{len(users)}</b></div><div class='card'>Analyses <b>{len(an)}</b></div><div class='card'>Token sum <b>{sum(int(u.get('token_balance',0) or 0) for u in users)}</b></div></div>"
    return web.Response(text=_layout("Dashboard","Dashboard",key,body),content_type='text/html')

async def admin_users(r):
    d=await _guard(r)
    if d:return d
    key=_key(r); q=r.query.get('q','').lower(); limit=min(max(int(r.query.get('limit','100')) if r.query.get('limit','100').isdigit() else 100,1),500)
    users=get_all_users(5000)
    if q: users=[u for u in users if q in str(u.get('user_id','')).lower() or q in str(u.get('username','')).lower() or q in str(u.get('first_name','')).lower()]
    users=users[:limit]
    rows=[]; cards=[]
    for u in users:
        uid=int(u.get('user_id',0) or 0)
        rows.append(f"<tr><td><a href='/admin/users/{uid}?key={escape(key)}'>{uid}</a></td><td>{escape(str(u.get('username','')))}</td><td>{escape(str(u.get('first_name','')))}</td><td>{escape(str(u.get('language','')))}</td><td>{u.get('token_balance',0)}</td><td>{u.get('total_analyses',0)}</td><td>{u.get('total_opportunities',0)}</td><td><form method='post' action='/admin/users/{uid}/tokens/add?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'><input name='amount' value='10' size='4'><button>Add</button></form></td></tr>")
        cards.append(f"<div class='mobile-card'><b>{uid}</b> @{escape(str(u.get('username','')))}<br>{escape(str(u.get('first_name','')))} · {escape(str(u.get('language','')))}<br><span class='pill'>tokens {u.get('token_balance',0)}</span> <span class='pill'>A {u.get('total_analyses',0)}</span><div class='actions-grid'><a href='/admin/users/{uid}?key={escape(key)}'>Detail</a><form method='post' action='/admin/users/{uid}/tokens/subtract?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'><input name='amount' value='10' size='3'><button class='danger' onclick=\"return confirm('Subtract?')\">Sub</button></form></div></div>")
    body=f"<div class='card'><form class='field-row'><input type='hidden' name='key' value='{escape(key)}'><input name='q' value='{escape(q)}'><input name='limit' value='{limit}' size='4'><button>Filter</button></form></div><div class='desktop-table table-scroll'><table><tr><th>id</th><th>username</th><th>first</th><th>lang</th><th>tokens</th><th>analyses</th><th>opps</th><th>action</th></tr>{''.join(rows)}</table></div><div class='mobile-card-list'>{''.join(cards)}</div>"
    return web.Response(text=_layout('Users','Users',key,body,r.query.get('msg','')),content_type='text/html')

async def admin_user_detail(r):
    d=await _guard(r)
    if d:return d
    key=_key(r); uid=int(r.match_info['user_id']); u=next((x for x in get_all_users(50000) if int(x.get('user_id',0) or 0)==uid),None)
    if not u: body='<div class=card>User not found</div>'
    else:
        body=f"<div class='grid'><div class='card'><h3>Profile</h3><pre>{escape(str(u))}</pre></div><div class='card'><h3>Actions</h3><form method='post' action='/admin/users/{uid}/tokens/set?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'><input name='amount' value='{int(u.get('token_balance',0) or 0)}'><button>Set exact balance</button></form><form method='post' action='/admin/users/{uid}/vip?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'><select name='enabled'><option value='1'>VIP on</option><option value='0'>VIP off</option></select><button>Save</button></form><form method='post' action='/admin/users/{uid}/ban?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'><select name='enabled'><option value='1'>Ban</option><option value='0'>Unban</option></select><button>Save</button></form><form method='post' action='/admin/users/{uid}/language?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'><select name='lang'><option>ru</option><option>en</option></select><button>Set language</button></form></div></div>"
    return web.Response(text=_layout('User detail','Users',key,body,r.query.get('msg','')),content_type='text/html')

async def admin_user_action(r):
    d=await _guard(r)
    if d:return d
    key=_key(r); uid=int(r.match_info['user_id']); action=r.match_info['action']; f=await r.post(); msg='Updated'; users=get_all_users(50000); u=next((x for x in users if int(x.get('user_id',0) or 0)==uid),None)
    if action.startswith('tokens/'):
        amt=str(f.get('amount','0'))
        if not amt.isdigit(): msg='Invalid amount'
        else:
            a=int(amt)
            if action=='tokens/add': add_tokens(uid,a)
            elif action=='tokens/subtract':
                if u and int(u.get('token_balance',0) or 0)-a<0: msg='Cannot go below zero'
                else: add_tokens(uid,-a)
            elif action=='tokens/set' and u: add_tokens(uid,a-int(u.get('token_balance',0) or 0))
    elif action=='vip': set_user_vip(uid,str(f.get('enabled','1'))=='1')
    elif action=='ban': set_user_ban(uid,str(f.get('enabled','1'))=='1')
    elif action=='language':
        lang=str(f.get('lang','ru'))
        if lang in {'ru','en'}: set_user_language(uid,lang)
    print(f"ADMIN_ACTION type={action} target={uid}")
    return web.HTTPFound(f"/admin/users/{uid if r.path.endswith('/language') else ''}?key={escape(key)}&msg={escape(msg)}")

async def admin_analyses(r):
    d=await _guard(r)
    if d:return d
    key=_key(r); limit=min(max(int(r.query.get('limit','50')) if r.query.get('limit','50').isdigit() else 50,1),300); rows=get_recent_analyses(limit)
    trs=[]; cards=[]
    for a in rows:
        aid=a.get('id'); dec=extract_decision(a); edge=extract_edge(a); sc=extract_source_count(a)
        trs.append(f"<tr><td>{aid}</td><td>{escape(str(a.get('created_at','')))}</td><td>{a.get('user_id',0)}</td><td class='truncate-4'>{escape(str(a.get('question','')))}</td><td>{escape(str(a.get('category','')))}</td><td>{escape(str(dec))}</td><td>{edge}</td><td>{sc}</td><td><a href='/admin/analyses/{aid}?key={escape(key)}'>detail</a></td></tr>")
        cards.append(f"<div class='mobile-card'><b>#{aid}</b> {escape(str(a.get('created_at',''))[:16])}<br>u:{a.get('user_id',0)} · {escape(str(a.get('category','')))}<div class='truncate-4'>{escape(str(a.get('question','')))}</div><div class='truncate-4'>{escape(str(dec))}</div>edge:{edge} sources:{sc}<br><a href='/admin/analyses/{aid}?key={escape(key)}'>Detail</a></div>")
    body=f"<div class='card'><form><input type='hidden' name='key' value='{escape(key)}'><input name='limit' value='{limit}' size='4'><button>Apply</button></form></div><div class='desktop-table table-scroll'><table><tr><th>id</th><th>time</th><th>user</th><th>question</th><th>category</th><th>decision</th><th>edge</th><th>sources</th><th></th></tr>{''.join(trs)}</table></div><div class='mobile-card-list'>{''.join(cards)}</div>"
    return web.Response(text=_layout('Analyses','Analyses',key,body),content_type='text/html')

async def admin_analysis_detail(r):
    d=await _guard(r)
    if d:return d
    key=_key(r); aid=int(r.match_info['id']); row=next((x for x in get_recent_analyses(500) if int(x.get('id',0) or 0)==aid),None)
    if not row: body='<div class=card>Not found</div>'
    else: body=f"<div class='card'><h3>Core</h3><pre>{escape(str({k:row.get(k) for k in ['id','created_at','user_id','category','market_probability','system_probability','conclusion']}))}</pre></div><div class='card'><h3>Question</h3><div class='truncate-4'>{escape(str(row.get('question','')))}</div></div><div class='card'><h3>Raw row JSON</h3><pre>{escape(json.dumps(row,ensure_ascii=False,indent=2,default=str))}</pre></div><div class='card'><h3>Admin actions</h3><button disabled>Mark reviewed</button> <button disabled>Archive</button> <button disabled>Hide</button><div class='muted'>Requires structured analysis storage. Not enabled in v1.</div></div>"
    return web.Response(text=_layout('Analysis detail','Analyses',key,body),content_type='text/html')

async def admin_settings(r):
    d=await _guard(r)
    if d:return d
    key=_key(r)
    keys=["paid_mode","token_price_ton","analysis_price_tokens","opportunity_price_tokens","cached_signal_price_tokens","subscription_price_ton","web_payments_enabled","web_tron_usdt_enabled","announcement_text","ton_wallet_withdraw_fee_enabled","ton_wallet_withdraw_fee_percent","ton_wallet_withdraw_fee_min_nano","ton_wallet_withdraw_fee_max_nano","ton_wallet_fee_wallet","ton_wallet_fee_mode","ton_wallet_token_purchase_enabled","ton_platform_wallet","ton_token_price_per_internal_token_nano","ton_token_purchase_min_tokens","ton_token_purchase_bonus_percent"]
    labels={"token_price_ton":"Token price, TON","subscription_price_ton":"Subscription price, TON","analysis_price_tokens":"Analysis price, tokens","cached_signal_price_tokens":"Cached signal price, tokens","opportunity_price_tokens":"Opportunity price, tokens","web_payments_enabled":"Web payments enabled","web_tron_usdt_enabled":"Web TRON USDT enabled"}
    if r.method=='POST':
        f=await r.post()
        for k in keys:
            if k in f: set_setting(k,str(f.get(k,''))[:5000])
        return web.HTTPFound(f"/admin/settings?key={escape(key)}&msg=Saved")
    fields=[]
    for k in keys:
        v=get_setting(k,'')
        label=labels.get(k,k)
        if k in BOOL_KEYS: inp=f"<select name='{k}'><option value='off' {'selected' if v!='on' else ''}>off</option><option value='on' {'selected' if v=='on' else ''}>on</option></select>"
        elif 'text' in k: inp=f"<textarea name='{k}' rows='3'>{escape(v)}</textarea>"
        else: inp=f"<input name='{k}' value='{escape(v)}'>"
        fields.append(f"<label>{escape(label)}<br>{inp}</label><br><br>")
    body=f"<div class='card'><form method='post' action='/admin/settings?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'>{''.join(fields)}<button>Save settings</button></form></div>"
    return web.Response(text=_layout('Settings','Settings',key,body,r.query.get('msg','')),content_type='text/html')

async def admin_content(r):
    d=await _guard(r)
    if d:return d
    key=_key(r); fields=["announcement_text","maintenance_message","disclaimer_ru","disclaimer_en","welcome_message_ru","welcome_message_en","pricing_note_ru","pricing_note_en"]
    if r.method=='POST':
        f=await r.post(); errs=[]
        for x in fields:
            try:set_setting(x,str(f.get(x,''))[:5000])
            except Exception: errs.append(x)
        return web.HTTPFound(f"/admin/content?key={escape(key)}&msg={'Saved' if not errs else 'Saved with errors'}")
    body="<div class='card'><form method='post' action='/admin/content?key={k}'><input type='hidden' name='key' value='{k}'>".replace('{k}',escape(key))
    for x in fields: body+=f"<label>{x}<br><textarea name='{x}' rows='3'>{escape(get_setting(x,''))}</textarea></label><br><br>"
    body+="<button>Save content</button></form></div>"
    return web.Response(text=_layout('Content','Content',key,body,r.query.get('msg','')),content_type='text/html')

async def _simple(name,section):
    async def h(r):
        d=await _guard(r)
        if d:return d
        return web.Response(text=_layout(name,section,_key(r),"<div class='card muted'>Operational panel placeholder.</div>"),content_type='text/html')
    return h



async def admin_revenue(r):
    d=await _guard(r)
    if d:return d
    key=_key(r)
    body=f"<div class='card'><form method='post' action='/admin/settings?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'><label>token_price_ton<br><input name='token_price_ton' value='{escape(get_setting('token_price_ton','0.1'))}'></label><label>subscription_price_ton<br><input name='subscription_price_ton' value='{escape(get_setting('subscription_price_ton','1'))}'></label><button>Save pricing</button></form></div>"
    return web.Response(text=_layout('Revenue','Revenue',key,body),content_type='text/html')

async def admin_tokens(r):
    d=await _guard(r)
    if d:return d
    key=_key(r)
    return web.Response(text=_layout('Tokens','Tokens',key,"<div class='card muted'>Use user actions for token operations.</div>"),content_type='text/html')

async def admin_quality(r):
    d=await _guard(r)
    if d:return d
    key=_key(r); rows=get_recent_analyses(300); dec=[str(extract_decision(x)).upper() for x in rows]
    body=f"<div class='card'>NO TRADE: {sum(1 for x in dec if 'NO TRADE' in x)} | WAIT: {sum(1 for x in dec if 'WAIT' in x)} | WATCH: {sum(1 for x in dec if 'WATCH' in x)} | CONSIDER: {sum(1 for x in dec if 'CONSIDER' in x)}</div>"
    return web.Response(text=_layout('Quality','Quality',key,body),content_type='text/html')

async def admin_signals(r):
    d=await _guard(r)
    if d:return d
    key=_key(r); rows=get_recent_analyses(300); dec=[str(extract_decision(x)).upper() for x in rows]
    action=sum(1 for x in dec if 'WATCH' in x or 'CONSIDER' in x)
    msg='No actionable CONSIDER/WATCH signals found in recent analyses.' if action==0 else f'Actionable signals: {action}'
    body=f"<div class='card'>{escape(msg)}</div>"
    return web.Response(text=_layout('Signals','Signals',key,body),content_type='text/html')

async def admin_payments(r):
    d=await _guard(r)
    if d:return d
    key=_key(r)
    body=f"<div class='card'><form method='post' action='/admin/settings?key={escape(key)}'><input type='hidden' name='key' value='{escape(key)}'><label>telegram_ton_payments_enabled<br><select name='telegram_ton_payments_enabled'><option value='off'>off</option><option value='on' {'selected' if get_setting('telegram_ton_payments_enabled','on')=='on' else ''}>on</option></select></label><label>web_payments_enabled<br><select name='web_payments_enabled'><option value='off'>off</option><option value='on' {'selected' if get_setting('web_payments_enabled','off')=='on' else ''}>on</option></select></label><button>Save flags</button></form></div>"
    return web.Response(text=_layout('Payments','Payments',key,body),content_type='text/html')

async def admin_webapp(r):
    d=await _guard(r)
    if d:return d
    return web.Response(text=_layout('WebApp','WebApp',_key(r),"<div class='card'>Legacy Telegram WebApp active. React app not built yet.</div>"),content_type='text/html')

async def admin_audit(r):
    d=await _guard(r)
    if d:return d
    return web.Response(text=_layout('Audit','Audit',_key(r),"<div class='card muted'>Audit storage TODO.</div>"),content_type='text/html')
def setup_admin_routes(app):
    app.router.add_get('/admin',admin_dashboard)
    app.router.add_get('/admin/users',admin_users)
    app.router.add_get('/admin/users/{user_id}',admin_user_detail)
    app.router.add_post('/admin/users/{user_id}/{action:.+}',admin_user_action)
    app.router.add_get('/admin/analyses',admin_analyses)
    app.router.add_get('/admin/analyses/{id}',admin_analysis_detail)
    app.router.add_get('/admin/settings',admin_settings)
    app.router.add_post('/admin/settings',admin_settings)
    app.router.add_get('/admin/content',admin_content)
    app.router.add_post('/admin/content',admin_content)
    app.router.add_get('/admin/revenue', admin_revenue)
    app.router.add_get('/admin/tokens', admin_tokens)
    app.router.add_get('/admin/quality', admin_quality)
    app.router.add_get('/admin/signals', admin_signals)
    app.router.add_get('/admin/payments', admin_payments)
    app.router.add_get('/admin/webapp', admin_webapp)
    app.router.add_get('/admin/audit', admin_audit)
