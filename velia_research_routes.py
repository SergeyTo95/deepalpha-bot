import asyncio
import json
import logging

from services import velia_project_service as projects
from services import velia_research_center_service as research
from services import velia_research_literature_service as literature
from services import velia_research_reasoning_service as reasoning
from velia_mobile_routes import _json_response, _mobile_api_available, _require_mobile_auth


MAX_BODY = 24 * 1024


async def _body(request):
    raw = bytearray()
    async for chunk in request.content.iter_chunked(4096):
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            raise projects.ProjectError("request_too_large", 413)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise projects.ProjectError("invalid_json")
    if not isinstance(data, dict):
        raise projects.ProjectError("invalid_json")
    return data


def setup_velia_research_routes(app):
    async def startup(_app):
        if not _mobile_api_available():
            return
        try:
            await asyncio.to_thread(research.ensure_tables)
            await asyncio.to_thread(literature.ensure_tables)
            await asyncio.to_thread(reasoning.ensure_tables)
        except Exception:
            logging.getLogger(__name__).exception("VELIA_RESEARCH_STORAGE_UNAVAILABLE")

    app.on_startup.append(startup)

    def guarded(handler, *, storage=True):
        async def wrapped(request):
            if not _mobile_api_available():
                return _json_response({"ok": False, "error": "velia_mobile_api_disabled"}, status=503)
            auth = await asyncio.to_thread(_require_mobile_auth, request)
            if not auth:
                return _json_response({"ok": False, "error": "unauthorized"}, status=401)
            expected_account = request.headers.get("X-Velia-Account")
            if expected_account is not None and expected_account != str(auth["user_id"]):
                return _json_response({"ok": False, "error": "account_changed"}, status=409)
            if storage and not projects.ready():
                return _json_response({"ok": False, "error": "research_unavailable"}, status=503)
            try:
                return await handler(request, int(auth["user_id"]))
            except projects.ProjectError as exc:
                return _json_response({"ok": False, "error": exc.code}, status=exc.status)
            except (TypeError, ValueError):
                return _json_response({"ok": False, "error": "invalid_request"}, status=400)
        return wrapped

    async def get_status(request, uid):
        del request, uid
        state = research.status()
        state["literature"] = literature.status()
        state["reasoning"] = reasoning.status()
        return _json_response({"ok": True, "research": state})

    async def mission_list(request, uid):
        data = await asyncio.to_thread(research.list_missions, uid, int(request.query.get("offset", 0)))
        return _json_response({"ok": True, **data})

    async def mission_create(request, uid):
        data = await _body(request)
        mission = await asyncio.to_thread(
            research.create_mission, uid, data, request.headers.get("Idempotency-Key")
        )
        return _json_response({"ok": True, "mission": mission}, status=201)

    async def mission_get(request, uid):
        mission = await asyncio.to_thread(research.get_mission, uid, request.match_info["mission_id"])
        return _json_response({"ok": True, "mission": mission})

    async def mission_events(request, uid):
        events = await asyncio.to_thread(
            research.list_events, uid, request.match_info["mission_id"], int(request.query.get("after", 0))
        )
        return _json_response({"ok": True, "events": events})

    async def hypothesis_create(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            research.add_hypothesis,
            uid,
            request.match_info["mission_id"],
            data.get("title", ""),
            data.get("rationale", ""),
        )
        return _json_response({"ok": True, "hypothesis": item}, status=201)

    async def experiment_create(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            research.plan_experiment,
            uid,
            request.match_info["mission_id"],
            data.get("method"),
            data.get("hypothesis_id"),
        )
        return _json_response({"ok": True, "experiment": item}, status=201)

    async def literature_search(request, uid):
        data = await _body(request)
        result = await asyncio.to_thread(
            literature.collect,
            uid,
            request.match_info["mission_id"],
            data.get("query", ""),
            data.get("max_results", 12),
        )
        return _json_response({"ok": True, "literature": result})

    async def literature_sources(request, uid):
        result = await asyncio.to_thread(
            literature.list_sources,
            uid,
            request.match_info["mission_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def synthesis_create(request, uid):
        data = await _body(request)
        result = await asyncio.to_thread(
            reasoning.synthesize,
            uid,
            request.match_info["mission_id"],
            data.get("max_sources", 12),
        )
        return _json_response({"ok": True, "synthesis": result})

    async def synthesis_list(request, uid):
        result = await asyncio.to_thread(
            reasoning.list_syntheses,
            uid,
            request.match_info["mission_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def mission_cancel(request, uid):
        mission = await asyncio.to_thread(research.cancel_mission, uid, request.match_info["mission_id"])
        return _json_response({"ok": True, "mission": mission})

    prefix = "/mobile-api/v1/research"
    app.router.add_get(prefix + "/status", guarded(get_status, storage=False))
    app.router.add_get(prefix + "/missions", guarded(mission_list))
    app.router.add_post(prefix + "/missions", guarded(mission_create))
    app.router.add_get(prefix + "/missions/{mission_id}", guarded(mission_get))
    app.router.add_get(prefix + "/missions/{mission_id}/events", guarded(mission_events))
    app.router.add_post(prefix + "/missions/{mission_id}/literature", guarded(literature_search))
    app.router.add_get(prefix + "/missions/{mission_id}/sources", guarded(literature_sources))
    app.router.add_post(prefix + "/missions/{mission_id}/synthesize", guarded(synthesis_create))
    app.router.add_get(prefix + "/missions/{mission_id}/syntheses", guarded(synthesis_list))
    app.router.add_post(prefix + "/missions/{mission_id}/hypotheses", guarded(hypothesis_create))
    app.router.add_post(prefix + "/missions/{mission_id}/experiments", guarded(experiment_create))
    app.router.add_post(prefix + "/missions/{mission_id}/cancel", guarded(mission_cancel))
