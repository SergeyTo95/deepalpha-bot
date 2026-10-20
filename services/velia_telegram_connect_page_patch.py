import html
import os
import re
from typing import Any, Callable, Optional

from aiohttp import web


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _bot_username() -> str:
    raw = str(os.getenv("BOT_USERNAME", "DeepAlphaAI_bot") or "DeepAlphaAI_bot")
    candidate = raw.lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", candidate):
        return candidate
    return "DeepAlphaAI_bot"


def build_telegram_connect_url() -> str:
    return f"https://t.me/{_bot_username()}?start=velia_connect"


def build_unauthenticated_connect_page() -> str:
    telegram_url = html.escape(build_telegram_connect_url(), quote=True)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Подключение VELIA</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      background: #111016; color: #f2eef7; font-family: system-ui, sans-serif;
      padding: 22px; box-sizing: border-box;
    }}
    main {{
      width: min(100%, 560px); background: #201e27; border: 1px solid #35313e;
      border-radius: 26px; padding: 28px; box-sizing: border-box;
    }}
    .logo {{
      width: 54px; height: 54px; border-radius: 50%; display: grid; place-items: center;
      background: #6847ff; font-weight: 900; font-size: 24px; margin-bottom: 22px;
    }}
    h1 {{ margin: 0 0 12px; font-size: 28px; }}
    p {{ color: #cec7d5; line-height: 1.55; }}
    .button {{
      display: block; text-align: center; padding: 15px 18px; margin-top: 22px;
      border-radius: 16px; background: #6b4eff; color: white; text-decoration:none;
      font-weight:750;
    }}
    .secondary {{ background: transparent; border: 1px solid #6c6674; }}
    .note {{ margin-top:20px; font-size:14px; color:#aaa2b1; }}
  </style>
</head>
<body>
  <main>
    <div class="logo">V</div>
    <h1>Подключение VELIA</h1>
    <p>Получи одноразовый код в личном сообщении от DeepAlpha AI Bot.</p>
    <p>Код привязан к твоему Telegram ID, действует 5 минут и срабатывает только один раз.</p>
    <a class="button" href="{telegram_url}">Получить код в Telegram</a>
    <a class="button secondary" href="/app">Войти через WebApp</a>
    <p class="note">После получения вернись в приложение VELIA и вставь 16-символьный код.</p>
  </main>
</body>
</html>"""


def install(
    app: web.Application,
    web_user_resolver: Callable[[web.Request], Optional[int]],
) -> None:
    if app.get("velia_telegram_connect_page_patch_installed"):
        return

    @web.middleware
    async def telegram_connect_page_middleware(request: web.Request, handler: Any):
        if request.method == "GET" and request.path == "/mobile-connect":
            if not _env_bool("VELIA_MOBILE_API_ENABLED", False):
                return await handler(request)
            try:
                authenticated_user_id = int(web_user_resolver(request) or 0)
            except Exception:
                authenticated_user_id = 0
            if authenticated_user_id <= 0:
                response = web.Response(
                    text=build_unauthenticated_connect_page(),
                    content_type="text/html",
                    status=200,
                )
                response.headers["Cache-Control"] = "no-store"
                response.headers["Pragma"] = "no-cache"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["Content-Security-Policy"] = (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
                )
                return response
        return await handler(request)

    app.middlewares.append(telegram_connect_page_middleware)

    # Conversation UX is installed here because this established late runtime
    # patch receives the fully configured aiohttp app after the base mobile chat
    # routes and chat wrappers have been installed, but before aiohttp freezes
    # the router. Linked-chat context therefore wraps the final _build_prompt
    # used by both blocking and SSE generation without rewriting those pipelines.
    import services.velia_chat_service as chat_service_module
    import services.velia_conversation_links_routes as links_routes_module
    import services.velia_conversation_links_service as links_service_module
    import services.velia_conversation_ux_routes as conversation_ux_routes_module
    import velia_mobile_routes as mobile_routes_module
    from services.velia_conversation_links_bidirectional_service import (
        build_linked_context_bidirectional,
        link_conversations_bidirectional,
        list_conversation_link_summaries_bidirectional,
        list_conversation_links_bidirectional,
        unlink_conversation_bidirectional,
    )
    from services.velia_conversation_ux_order_service import (
        list_conversations_ordered_stable,
        reorder_visible_conversations,
    )
    from services.velia_conversation_ux_routes import setup_velia_conversation_ux_routes
    from services.velia_conversation_ux_service import install_conversation_ordering

    install_conversation_ordering(chat_service_module, mobile_routes_module)
    # Replace the legacy list implementation with the same canonical order used
    # by pagination-safe reorder so equal timestamps cannot produce different pages.
    chat_service_module.list_conversations = list_conversations_ordered_stable
    mobile_routes_module.list_conversations = list_conversations_ordered_stable

    # Stored target/source columns are now an implementation detail. Runtime APIs,
    # badges, unlink and prompt context all treat the relation as an undirected pair,
    # so users cannot connect two chats "the wrong way around".
    links_routes_module.link_conversations = link_conversations_bidirectional
    links_routes_module.list_conversation_links = list_conversation_links_bidirectional
    links_routes_module.unlink_conversation = unlink_conversation_bidirectional
    links_routes_module.list_conversation_link_summaries = (
        list_conversation_link_summaries_bidirectional
    )
    links_service_module.build_linked_context = build_linked_context_bidirectional
    links_service_module.install_linked_conversation_prompt(chat_service_module)

    # Keep the public route contract stable while swapping in the pagination-safe
    # implementation before the route handlers are registered.
    conversation_ux_routes_module.reorder_conversations = reorder_visible_conversations
    setup_velia_conversation_ux_routes(app, mobile_routes_module)
    links_routes_module.setup_velia_conversation_links_routes(app, mobile_routes_module)

    app["velia_telegram_connect_page_patch_installed"] = True