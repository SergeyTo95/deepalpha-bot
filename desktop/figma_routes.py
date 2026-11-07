"""Authenticated Figma connection and execution for the Velia web client."""
import asyncio

from aiohttp import web

from desktop.figma_connector import FigmaConnector, FigmaError
from velia_desktop_routes import AuthenticationUnavailable


def setup_figma_routes(app, *, session_for, same_origin, json_response, connector=None):
    bridge = connector or FigmaConnector()
    slots = asyncio.Semaphore(8)

    async def lifecycle(application):
        async def prune():
            while True:
                await asyncio.sleep(60)
                bridge.prune()
        task = asyncio.create_task(prune())
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        bridge.connections.clear()
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
            if action == "status":
                return json_response({"ok": True, **bridge.status(owner)})
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
                if action == "connect":
                    result = await bridge.connect(owner, data.get("token"))
                elif action == "verify":
                    result = await bridge.verify(owner)
                elif action == "disconnect":
                    bridge.disconnect(owner)
                    result = {"connected": False}
                else:
                    result = await bridge.call(owner, data)
                return json_response({"ok": True, **result})
        except FigmaError as exc:
            return json_response({"ok": False, "error": exc.code}, exc.status)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return json_response({"ok": False, "error": "figma_invalid_call"}, 400)
        except (AuthenticationUnavailable, OSError, TimeoutError):
            return json_response({"ok": False, "error": "figma_unavailable"}, 503)

    app.router.add_get("/web-api/v1/figma/{action:status|tools}", handle)
    app.router.add_post("/web-api/v1/figma/{action:connect|verify|disconnect|call}", handle)
    return bridge
