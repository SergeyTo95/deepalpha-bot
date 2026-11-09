"""Authenticated Figma connection and execution for the Velia web client."""
import asyncio
from contextlib import nullcontext

from aiohttp import web

from desktop.figma_connector import FigmaConnector, FigmaError
from desktop.figma_oauth import FigmaOAuth, ORIGIN as OAUTH_ORIGIN
from velia_desktop_routes import AuthenticationUnavailable


def setup_figma_routes(app, *, session_for, same_origin, json_response, connector=None, origin=None, oauth=None,
                       prefix="/web-api/v1/figma", provider=None):
    bridge = connector or FigmaConnector()
    oauth = oauth or (FigmaOAuth(bridge, origin) if origin else None)
    bridge.oauth = oauth
    slots = asyncio.Semaphore(8)

    async def lifecycle(application):
        async def prune():
            while True:
                await asyncio.sleep(60)
                bridge.prune()
                if oauth:
                    oauth.prune()
        task = asyncio.create_task(prune())
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        bridge.connections.clear()
        if oauth:
            oauth.pending.clear()
    app.cleanup_ctx.append(lifecycle)

    async def handle(request):
        if request.method != "GET" and not same_origin(request):
            return json_response({"ok": False, "error": "invalid_origin"}, 403)
        try:
            session = await session_for(request)
            if not session:
                return json_response({"ok": False, "error": "unauthorized"}, 401)
            owner = (str(session.user_id), session.id)
            action = request.match_info["action"]
            if provider == "google" and action in {"status", "tools", "verify", "call"}:
                await bridge.restore(owner)
            if action == "status":
                value = bridge.status(owner)
                if hasattr(oauth, "backend"):
                    with oauth.authentication(getattr(session, "access", None)):
                        settings = await oauth.backend("config")
                    value["available"] = settings.get("configured") is True
                return json_response({"ok": True, **value, "oauth_available": oauth is not None})
            if action == "tools":
                return json_response({"ok": True, "tools": bridge.catalog(owner)})
            if slots.locked():
                raise FigmaError("figma_busy", 429)
            async with slots:
                if request.content_length and request.content_length > 64000:
                    raise FigmaError("figma_invalid_call", 413)
                data = await request.json()
                if not isinstance(data, dict):
                    raise FigmaError("figma_invalid_call")
                if action == "authorize":
                    if oauth is None:
                        raise FigmaError("figma_authorization_unavailable", 503)
                    with oauth.authentication(getattr(session, "access", None)) if hasattr(oauth, "authentication") else nullcontext():
                        result = await oauth.start(owner, options=data)
                elif action == "connect":
                    if oauth:
                        oauth.cancel(owner)
                    result = await bridge.connect(owner, data.get("token"))
                elif action == "verify":
                    result = await bridge.verify(owner)
                elif action == "disconnect":
                    if provider == "google":
                        await bridge.forget(owner)
                    else:
                        bridge.disconnect(owner)
                    result = {"connected": False}
                else:
                    result = await bridge.call(owner, data)
                return json_response({"ok": True, **result})
        except FigmaError as exc:
            return json_response({"ok": False, "error": exc.code.replace("figma_", "connection_") if provider else exc.code}, exc.status)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return json_response({"ok": False, "error": "figma_invalid_call"}, 400)
        except (AuthenticationUnavailable, OSError, TimeoutError):
            return json_response({"ok": False, "error": "figma_unavailable"}, 503)

    async def callback(request):
        outcome = "error"
        try:
            session = await session_for(request)
            states, codes = request.query.getall("state", []), request.query.getall("code", [])
            if oauth is not None and session is not None and len(states) == 1 and request.query.get("error"):
                oauth.reject((str(session.user_id), session.id), states[0])
            if (oauth is not None and session is not None and not request.query.get("error")
                    and len(states) == len(codes) == 1 and request.query.get("iss") in {None, oauth.settings["issuer"] if provider else OAUTH_ORIGIN}
                    and not slots.locked()):
                async with slots:
                    with oauth.authentication(getattr(session, "access", None)) if hasattr(oauth, "authentication") else nullcontext():
                        await oauth.complete((str(session.user_id), session.id), states[0], codes[0], callback_query=list(request.query.items()))
                outcome = "connected"
        except (FigmaError, AuthenticationUnavailable, OSError, TimeoutError, ValueError, TypeError):
            pass
        target = "/?connection=" + provider + "&result=" + outcome if provider else "/?figma=" + outcome
        raise web.HTTPSeeOther(target, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    app.router.add_get(prefix + "/callback", callback)
    if provider == "google":
        app.router.add_get("/web-api/v1/velia/oauth/google/callback", callback)
    app.router.add_get(prefix + "/{action:status|tools}", handle)
    app.router.add_post(prefix + "/{action:authorize|" + ("connect|" if bridge.pair_required else "") + "verify|disconnect|call}", handle)
    return bridge
