import html
import os
from typing import Any, Dict
from urllib.parse import quote

from aiohttp import web

from services.public_domain_service import resolve_public_origin
from services.velia_conversation_ux_service import (
    ConversationUxError,
    create_share_snapshot,
    get_public_share,
    merge_conversations,
    reorder_conversations,
)


ANDROID_PACKAGE = "ai.deepalpha.android"


def _error_response(mobile_routes_module: Any, error: ConversationUxError) -> web.Response:
    return mobile_routes_module._json_response(
        {"ok": False, "error": error.code},
        status=error.status,
    )


def _store_url(user_agent: str) -> str:
    lowered = str(user_agent or "").lower()
    if "android" in lowered:
        return str(os.getenv("VELIA_ANDROID_STORE_URL") or "").strip()
    if "iphone" in lowered or "ipad" in lowered or "ipod" in lowered:
        return str(os.getenv("VELIA_IOS_STORE_URL") or "").strip()
    return ""


def _platform(user_agent: str) -> str:
    lowered = str(user_agent or "").lower()
    if "android" in lowered:
        return "android"
    if "iphone" in lowered or "ipad" in lowered or "ipod" in lowered:
        return "ios"
    return "web"


def _smart_open_url(token: str, user_agent: str) -> str:
    platform = _platform(user_agent)
    store = _store_url(user_agent)
    deep_link = f"velia://share/{token}"
    if platform == "android" and store:
        encoded_fallback = quote(store, safe="")
        return (
            f"intent://share/{token}#Intent;scheme=velia;package={ANDROID_PACKAGE};"
            f"S.browser_fallback_url={encoded_fallback};end"
        )
    return deep_link


def _public_share_page(share: Dict[str, Any], token: str, user_agent: str) -> web.Response:
    title = html.escape(str(share.get("title") or "VELIA chat"))
    platform = _platform(user_agent)
    store = _store_url(user_agent)
    smart_open = html.escape(_smart_open_url(token, user_agent), quote=True)
    store_escaped = html.escape(store, quote=True)

    rendered_messages = []
    for item in share.get("messages") or []:
        role = str(item.get("role") or "")
        content = html.escape(str(item.get("content") or ""))
        label = "Пользователь" if role == "user" else "VELIA"
        css_class = "user" if role == "user" else "assistant"
        rendered_messages.append(
            f'<section class="message {css_class}"><div class="role">{label}</div>'
            f'<div class="content">{content}</div></section>'
        )
    messages_html = "".join(rendered_messages) or (
        '<section class="empty">В этом snapshot пока нет сообщений.</section>'
    )

    store_button = ""
    store_note = ""
    if store:
        store_label = "Google Play" if platform == "android" else "App Store" if platform == "ios" else "магазин"
        store_button = (
            f'<a class="button secondary" href="{store_escaped}">Открыть {store_label}</a>'
        )
    elif platform in {"android", "ios"}:
        store_note = (
            '<p class="store-note">Ссылка на магазин VELIA для этой платформы ещё не настроена.</p>'
        )

    ios_fallback_attr = ""
    ios_script = ""
    if platform == "ios" and store:
        ios_fallback_attr = f' data-store-fallback="{store_escaped}"'
        ios_script = """
<script>
(function () {
  var link = document.getElementById('open-velia');
  if (!link) return;
  link.addEventListener('click', function () {
    var fallback = link.getAttribute('data-store-fallback');
    if (!fallback) return;
    window.setTimeout(function () {
      if (document.visibilityState === 'visible') window.location.href = fallback;
    }, 1300);
  });
})();
</script>
"""

    page = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="robots" content="noindex,nofollow,noarchive">
  <title>{title} · VELIA</title>
  <style>
    :root {{ color-scheme: dark; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:#111016; color:#f3eff8; font-family:system-ui,-apple-system,sans-serif; }}
    main {{ width:min(100%,820px); margin:0 auto; padding:28px 18px 80px; }}
    .brand {{ font-weight:900; letter-spacing:1.4px; color:#bbaeff; margin-bottom:10px; }}
    h1 {{ margin:0 0 10px; font-size:clamp(25px,6vw,38px); line-height:1.12; }}
    .meta {{ color:#aaa2b1; margin:0 0 26px; line-height:1.5; }}
    .actions {{ display:grid; gap:10px; margin:20px 0 30px; }}
    .button {{ display:block; padding:14px 17px; border-radius:16px; background:#6b4eff; color:white; text-decoration:none; text-align:center; font-weight:800; }}
    .secondary {{ background:#28252f; border:1px solid #44404c; }}
    .message {{ margin:14px 0; padding:16px 17px; border-radius:20px; border:1px solid #34303b; background:#201e27; }}
    .message.user {{ margin-left:8%; background:#2b2732; }}
    .message.assistant {{ margin-right:4%; }}
    .role {{ font-size:12px; font-weight:850; color:#b8a9ff; margin-bottom:8px; text-transform:uppercase; letter-spacing:.7px; }}
    .content {{ white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.55; color:#eee9f3; }}
    .empty {{ padding:22px; color:#aaa2b1; border:1px dashed #44404c; border-radius:18px; }}
    .privacy {{ color:#8f8797; font-size:13px; line-height:1.5; margin-top:30px; }}
    .store-note {{ color:#b8b0bf; font-size:13px; text-align:center; margin:4px 0 0; }}
  </style>
</head>
<body>
<main>
  <div class="brand">VELIA</div>
  <h1>{title}</h1>
  <p class="meta">Read-only snapshot чата. Новые сообщения владельца сюда автоматически не добавляются.</p>
  <div class="actions">
    <a id="open-velia" class="button" href="{smart_open}"{ios_fallback_attr}>Открыть в VELIA</a>
    {store_button}
    {store_note}
  </div>
  {messages_html}
  <p class="privacy">Публичная ссылка показывает только зафиксированную копию сообщений на момент публикации.</p>
</main>
{ios_script}
</body>
</html>"""
    response = web.Response(text=page, content_type="text/html", status=200)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return response


def setup_velia_conversation_ux_routes(app: web.Application, mobile_routes_module: Any) -> None:
    if app.get("velia_conversation_ux_routes_installed"):
        return

    async def handle_reorder(request: web.Request) -> web.Response:
        if not mobile_routes_module._mobile_api_available():
            return mobile_routes_module._disabled_response()
        auth = mobile_routes_module._require_mobile_auth(request)
        if not auth:
            return mobile_routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        data = await mobile_routes_module._read_json(request)
        if data is None or not isinstance(data.get("conversation_ids"), list):
            return mobile_routes_module._json_response({"ok": False, "error": "invalid_json"}, status=400)
        try:
            conversations = reorder_conversations(
                int(auth["user_id"]),
                data["conversation_ids"],
            )
        except ConversationUxError as error:
            return _error_response(mobile_routes_module, error)
        return mobile_routes_module._json_response({"ok": True, "conversations": conversations})

    async def handle_merge(request: web.Request) -> web.Response:
        if not mobile_routes_module._mobile_api_available():
            return mobile_routes_module._disabled_response()
        auth = mobile_routes_module._require_mobile_auth(request)
        if not auth:
            return mobile_routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        data = await mobile_routes_module._read_json(request)
        if data is None or not isinstance(data.get("source_conversation_ids"), list):
            return mobile_routes_module._json_response({"ok": False, "error": "invalid_json"}, status=400)
        try:
            conversation = merge_conversations(
                int(auth["user_id"]),
                data["source_conversation_ids"],
                title=str(data.get("title") or ""),
            )
        except ConversationUxError as error:
            return _error_response(mobile_routes_module, error)
        return mobile_routes_module._json_response(
            {"ok": True, "conversation": conversation},
            status=201,
        )

    async def handle_share_create(request: web.Request) -> web.Response:
        if not mobile_routes_module._mobile_api_available():
            return mobile_routes_module._disabled_response()
        auth = mobile_routes_module._require_mobile_auth(request)
        if not auth:
            return mobile_routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            share = create_share_snapshot(
                int(auth["user_id"]),
                request.match_info["conversation_id"],
            )
        except ConversationUxError as error:
            return _error_response(mobile_routes_module, error)
        origin = resolve_public_origin(os.environ)
        token = str(share["token"])
        path = f"/velia/share/{token}"
        payload = {
            "id": share["id"],
            "url": origin + path,
            "path": path,
            "deep_link": f"velia://share/{token}",
            "expires_at": share["expires_at"],
            "message_count": share["message_count"],
        }
        return mobile_routes_module._json_response({"ok": True, "share": payload}, status=201)

    async def handle_public_share_json(request: web.Request) -> web.Response:
        share = get_public_share(request.match_info["token"])
        if share is None:
            return mobile_routes_module._json_response({"ok": False, "error": "share_not_found"}, status=404)
        return mobile_routes_module._json_response({"ok": True, "share": share})

    async def handle_public_share_page(request: web.Request) -> web.Response:
        token = request.match_info["token"]
        share = get_public_share(token)
        if share is None:
            response = web.Response(
                text="<!doctype html><meta charset='utf-8'><title>VELIA</title><h1>Ссылка недоступна</h1>",
                content_type="text/html",
                status=404,
            )
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        return _public_share_page(
            share,
            token,
            str(request.headers.get("User-Agent") or ""),
        )

    app.router.add_post("/mobile-api/v1/conversations/reorder", handle_reorder)
    app.router.add_post("/mobile-api/v1/conversations/merge", handle_merge)
    app.router.add_post(
        "/mobile-api/v1/conversations/{conversation_id}/share",
        handle_share_create,
    )
    app.router.add_get("/mobile-api/v1/public-shares/{token}", handle_public_share_json)
    app.router.add_get("/velia/share/{token}", handle_public_share_page)
    app["velia_conversation_ux_routes_installed"] = True
