"""Authenticated utility APIs; all external credentials stay on the server."""
import asyncio
import json
import os

from aiohttp import web

from services import velia_speech_service as speech
from services import web_search_service as search
from services import velia_open_knowledge_service as knowledge
from services.velia_calculator_service import CalculationError, calculate
from services.velia_free_api_budget_service import FreeAPIError, free_only

_PREFIX = "/mobile-api/v1/utilities"


async def _read(request, limit):
    data = bytearray()
    async for chunk in request.content.iter_chunked(16384):
        data.extend(chunk)
        if len(data) > limit:
            raise FreeAPIError("utility_body_too_large", 413)
    return bytes(data)


async def _json(request):
    raw = await _read(request, 16384)
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except (ValueError, UnicodeError):
        pass
    raise FreeAPIError("utility_json_invalid", 400)


def setup_free_tools_routes(app, routes_module):
    if app.get("velia_free_tools_installed"):
        return

    def secured(handler):
        async def wrapped(request):
            if not routes_module._mobile_api_available():
                return routes_module._disabled_response()
            auth = routes_module._require_mobile_auth(request)
            if not auth:
                return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
            try:
                response = await handler(request, int(auth["user_id"]))
                response.headers["Cache-Control"] = "no-store"
                return response
            except (FreeAPIError, CalculationError) as exc:
                return routes_module._json_response({"ok": False, "error": exc.code}, status=exc.status)
        return wrapped

    @secured
    async def status(request, user_id):
        provider = os.getenv("WEB_SEARCH_PROVIDER", "").strip().lower()
        return routes_module._json_response({"ok": True, "free_apis_only": free_only(),
            "calculator": {"available": True, "external_api": False},
            "search": {"configured": provider == "tavily" and bool(os.getenv("WEB_SEARCH_API_KEY", "").strip())
                       and (not free_only() or os.getenv("VELIA_TAVILY_FREE_PLAN_CONFIRMED", "false").lower() in {"true", "1", "on"}),
                       "provider": "tavily", "monthly_cap": search.tavily_monthly_limit(),
                       "account_plan_verified": False},
            "open_knowledge": {"wikipedia": True, "crossref": True, "api_key_required": False},
            "speech": speech.configuration()})

    @secured
    async def calculator(request, user_id):
        payload = await _json(request)
        result = await asyncio.to_thread(calculate, payload.get("expression"))
        return routes_module._json_response({"ok": True, **result})

    @secured
    async def web_search(request, user_id):
        payload = await _json(request)
        query = payload.get("query")
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000:
            raise FreeAPIError("search_query_invalid", 400)
        rows = await asyncio.to_thread(search.search_web_strict, query, 5, user_id=user_id)
        return routes_module._json_response({"ok": True, "results": rows})

    @secured
    async def wikipedia(request, user_id):
        payload = await _json(request)
        result = await asyncio.to_thread(knowledge.wikipedia_search, payload.get("query"), payload.get("language"))
        return routes_module._json_response({"ok": True, **result})

    @secured
    async def papers(request, user_id):
        payload = await _json(request)
        result = await asyncio.to_thread(knowledge.papers_search, payload.get("query"))
        return routes_module._json_response({"ok": True, **result})

    @secured
    async def voices(request, user_id):
        rows = await asyncio.to_thread(speech.voices)
        return routes_module._json_response({"ok": True, "voices": rows})

    @secured
    async def synthesize(request, user_id):
        payload = await _json(request)
        data = await asyncio.to_thread(speech.synthesize, payload.get("text"), payload.get("voice"), user_id=user_id)
        return web.Response(body=data, content_type="audio/mpeg")

    @secured
    async def transcribe(request, user_id):
        raw = await _read(request, 2 * 1024 * 1024)
        result = await asyncio.to_thread(speech.transcribe, raw, request.query.get("language"), user_id=user_id)
        return routes_module._json_response({"ok": True, **result})

    app.router.add_get(f"{_PREFIX}/status", status)
    app.router.add_post(f"{_PREFIX}/calculate", calculator)
    app.router.add_post(f"{_PREFIX}/search", web_search)
    app.router.add_post(f"{_PREFIX}/knowledge/search", wikipedia)
    app.router.add_post(f"{_PREFIX}/papers/search", papers)
    app.router.add_get(f"{_PREFIX}/speech/voices", voices)
    app.router.add_post(f"{_PREFIX}/speech/synthesize", synthesize)
    app.router.add_post(f"{_PREFIX}/speech/transcribe", transcribe)
    app["velia_free_tools_installed"] = True
