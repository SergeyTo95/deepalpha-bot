from __future__ import annotations

import asyncio
import hmac
import html
import json
import secrets
from typing import Any, Callable, Dict, Optional

from aiohttp import web

from services.velia_usdt_checkout_service import (
    create_usdt_payment_intent,
    get_usdt_intent_for_user,
    usdt_checkout_catalog,
)


MAX_BODY_BYTES = 16 * 1024
CSRF_COOKIE = "velia_usdt_checkout_csrf"


def _json(data: Dict[str, Any], status: int = 200) -> web.Response:
    response = web.json_response(data, status=status, dumps=lambda value: json.dumps(value, ensure_ascii=False, default=str))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _secure_html(body: str, *, csrf_token: Optional[str] = None) -> web.Response:
    response = web.Response(text=body, content_type="text/html")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    if csrf_token:
        response.set_cookie(
            CSRF_COOKIE,
            csrf_token,
            secure=True,
            httponly=True,
            samesite="Strict",
            max_age=3600,
            path="/velia/pay",
        )
    return response


def _web_user_id(resolver: Callable[[web.Request], Optional[int]], request: web.Request) -> int:
    try:
        return int(resolver(request) or 0)
    except Exception:
        return 0


def _checkout_page(catalog: Dict[str, Any], csrf_token: str, intent: Optional[Dict[str, Any]] = None) -> str:
    enabled = bool(catalog.get("checkout_enabled"))
    products = catalog.get("products") or []
    networks = catalog.get("networks") or []
    network_options = "".join(
        f'<option value="{html.escape(str(item["network"]))}">{html.escape(str(item["network"]).upper())} · USDT</option>'
        for item in networks
        if item.get("configured")
    )
    product_cards = []
    for product in products:
        code = html.escape(str(product.get("code") or ""), quote=True)
        name = html.escape(str(product.get("name") or ""))
        store = html.escape(str(product.get("store_price_usd") or ""))
        usdt = html.escape(str(product.get("usdt_price") or ""))
        product_cards.append(
            f"""
            <article class="card">
              <div><strong>{name}</strong><div class="muted">Store <s>${store}</s></div></div>
              <div class="price">{usdt} USDT <span class="discount">−30%</span></div>
              <form method="post" action="/velia/pay">
                <input type="hidden" name="csrf" value="{html.escape(csrf_token, quote=True)}">
                <input type="hidden" name="product_code" value="{code}">
                <select name="network" required>{network_options}</select>
                <button type="submit" {'disabled' if not enabled or not network_options else ''}>Оплатить USDT</button>
              </form>
            </article>
            """
        )

    intent_html = ""
    if intent:
        status = html.escape(str(intent.get("status") or ""))
        reference = html.escape(str(intent.get("public_reference") or ""))
        network = html.escape(str(intent.get("network") or "").upper())
        amount = html.escape(str(intent.get("amount_usdt") or ""))
        address = html.escape(str(intent.get("deposit_address") or ""))
        expires = html.escape(str(intent.get("expires_at") or ""))
        intent_html = f"""
        <section class="invoice">
          <div class="badge">Счёт создан · {network}</div>
          <h2>Отправь ровно {amount} USDT</h2>
          <p class="warning">Важно: отправь именно указанную сумму. Последние знаки — уникальный идентификатор платежа.</p>
          <label>Адрес</label><code>{address}</code>
          <label>Сеть</label><code>{network}</code>
          <label>Статус</label><code id="payment-status">{status}</code>
          <label>Счёт</label><code>{reference}</code>
          <p class="muted">Действует до {expires}. После finalized/confirmed транзакции Credits или тариф выдаются автоматически.</p>
          <script>
            setInterval(async () => {{
              try {{
                const r = await fetch('/velia/pay/status/{reference}', {{credentials:'same-origin'}});
                const d = await r.json();
                if (d.ok && d.intent) document.getElementById('payment-status').textContent = d.intent.status;
              }} catch (_) {{}}
            }}, 5000);
          </script>
        </section>
        """

    state_note = (
        "Оплата активна на настроенных сетях."
        if enabled and network_options
        else "USDT checkout пока закрыт серверным safety-switch или сеть ещё не настроена. Адрес оплаты не выдаётся."
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VELIA · USDT −30%</title>
<style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;background:#0d0d12;color:#f5f3fa;font-family:system-ui,sans-serif;padding:24px}}main{{max-width:760px;margin:auto}}h1{{font-size:34px;margin:8px 0}}.hero{{background:linear-gradient(135deg,#21194a,#15151d);padding:26px;border-radius:26px;border:1px solid #39324f}}.discount{{background:#7a5cff;color:white;border-radius:999px;padding:4px 9px;font-size:13px;font-weight:800}}.cards{{display:grid;gap:12px;margin-top:18px}}.card,.invoice{{background:#191920;border:1px solid #2d2d39;border-radius:20px;padding:18px}}.card{{display:grid;gap:12px}}.price{{font-size:21px;font-weight:800}}.muted{{color:#aaa5b5;font-size:13px;margin-top:4px}}form{{display:flex;gap:10px;flex-wrap:wrap}}select,button{{border-radius:12px;padding:12px 14px;border:1px solid #444252;background:#24242d;color:white}}button{{background:#7657ff;border:0;font-weight:800;cursor:pointer}}button:disabled{{opacity:.4;cursor:not-allowed}}.invoice{{margin:18px 0;border-color:#7657ff}}.badge{{font-weight:800;color:#a995ff}}label{{display:block;color:#aaa5b5;margin-top:12px;font-size:13px}}code{{display:block;overflow-wrap:anywhere;background:#101015;padding:12px;border-radius:10px;margin-top:5px}}.warning{{color:#ffd38b}}</style></head>
<body><main>
<section class="hero"><span class="discount">USDT −30%</span><h1>VELIA дешевле при оплате USDT</h1><p>Те же тарифы и Credits, но на 30% дешевле Store. Поддерживаемые сети показываются явно перед созданием счёта.</p><p class="muted">{html.escape(state_note)}</p></section>
{intent_html}
<section class="cards">{''.join(product_cards)}</section>
</main></body></html>"""


def setup_velia_usdt_checkout_routes(
    app: web.Application,
    mobile_routes_module: Any,
    web_user_resolver: Callable[[web.Request], Optional[int]],
) -> None:
    require_mobile_auth = getattr(mobile_routes_module, "_require_mobile_auth", None)
    mobile_api_available = getattr(mobile_routes_module, "_mobile_api_available", None)
    if not callable(require_mobile_auth) or not callable(mobile_api_available):
        raise RuntimeError("VELIA mobile auth boundary unavailable")

    async def mobile_catalog(request: web.Request) -> web.Response:
        if not mobile_api_available():
            return _json({"ok": False, "error": "velia_mobile_api_disabled"}, 503)
        auth = require_mobile_auth(request)
        if not auth:
            return _json({"ok": False, "error": "unauthorized"}, 401)
        return _json(usdt_checkout_catalog())

    async def mobile_create_intent(request: web.Request) -> web.Response:
        if not mobile_api_available():
            return _json({"ok": False, "error": "velia_mobile_api_disabled"}, 503)
        auth = require_mobile_auth(request)
        if not auth:
            return _json({"ok": False, "error": "unauthorized"}, 401)
        if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
            return _json({"ok": False, "error": "request_too_large"}, 413)
        try:
            data = await request.json()
        except Exception:
            return _json({"ok": False, "error": "invalid_json"}, 400)
        if not isinstance(data, dict):
            return _json({"ok": False, "error": "invalid_json"}, 400)
        result = await asyncio.to_thread(
            create_usdt_payment_intent,
            user_id=int(auth["user_id"]),
            product_code=str(data.get("product_code") or ""),
            network=str(data.get("network") or ""),
            idempotency_key=str(data.get("idempotency_key") or ""),
        )
        return _json(result, 200 if result.get("ok") else 400)

    async def mobile_intent(request: web.Request) -> web.Response:
        if not mobile_api_available():
            return _json({"ok": False, "error": "velia_mobile_api_disabled"}, 503)
        auth = require_mobile_auth(request)
        if not auth:
            return _json({"ok": False, "error": "unauthorized"}, 401)
        result = await asyncio.to_thread(
            get_usdt_intent_for_user,
            int(auth["user_id"]),
            request.match_info.get("reference", ""),
        )
        return _json(result, 200 if result.get("ok") else 404)

    async def web_checkout(request: web.Request) -> web.Response:
        user_id = _web_user_id(web_user_resolver, request)
        if user_id <= 0:
            raise web.HTTPFound("/app")
        csrf_token = str(request.cookies.get(CSRF_COOKIE) or "")
        if len(csrf_token) < 32:
            csrf_token = secrets.token_urlsafe(32)
        intent = None
        reference = str(request.query.get("intent") or "").strip()
        if reference:
            loaded = await asyncio.to_thread(get_usdt_intent_for_user, user_id, reference)
            intent = loaded.get("intent") if loaded.get("ok") else None
        return _secure_html(_checkout_page(usdt_checkout_catalog(), csrf_token, intent), csrf_token=csrf_token)

    async def web_create_intent(request: web.Request) -> web.Response:
        user_id = _web_user_id(web_user_resolver, request)
        if user_id <= 0:
            raise web.HTTPFound("/app")
        if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
            return _secure_html("<h1>Request too large</h1>")
        form = await request.post()
        supplied_csrf = str(form.get("csrf") or "")
        cookie_csrf = str(request.cookies.get(CSRF_COOKIE) or "")
        if not supplied_csrf or not cookie_csrf or not hmac.compare_digest(supplied_csrf, cookie_csrf):
            return _secure_html("<h1>Invalid checkout session</h1>")
        idem = f"web-usdt:{user_id}:{secrets.token_urlsafe(18)}"
        result = await asyncio.to_thread(
            create_usdt_payment_intent,
            user_id=user_id,
            product_code=str(form.get("product_code") or ""),
            network=str(form.get("network") or ""),
            idempotency_key=idem,
        )
        if not result.get("ok"):
            body = _checkout_page(usdt_checkout_catalog(), cookie_csrf)
            body = body.replace("</main>", f'<p class="warning">Не удалось создать счёт: {html.escape(str(result.get("error") or "error"))}</p></main>')
            return _secure_html(body)
        reference = str((result.get("intent") or {}).get("public_reference") or "")
        raise web.HTTPFound("/velia/pay?intent=" + reference)

    async def web_status(request: web.Request) -> web.Response:
        user_id = _web_user_id(web_user_resolver, request)
        if user_id <= 0:
            return _json({"ok": False, "error": "unauthorized"}, 401)
        result = await asyncio.to_thread(
            get_usdt_intent_for_user,
            user_id,
            request.match_info.get("reference", ""),
        )
        return _json(result, 200 if result.get("ok") else 404)

    app.router.add_get("/mobile-api/v1/economy/usdt/catalog", mobile_catalog)
    app.router.add_post("/mobile-api/v1/economy/usdt/intents", mobile_create_intent)
    app.router.add_get("/mobile-api/v1/economy/usdt/intents/{reference}", mobile_intent)
    app.router.add_get("/velia/pay", web_checkout)
    app.router.add_post("/velia/pay", web_create_intent)
    app.router.add_get("/velia/pay/status/{reference}", web_status)
