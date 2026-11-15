"""VELIA Guardian browser inbox: authenticated relay to the identity authority.

Never inspect chats or user prompts. The authority owns the event storage and
resolves event ownership strictly from the active user access token.
"""
import asyncio
import re

from aiohttp import web

_EVENT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


def setup_guardian_routes(app, *, session_for, same_origin, upstream, json_response):
    slots = asyncio.Semaphore(8)

    def respond(data, status=200):
        result = json_response(data, status)
        result.headers["Cache-Control"] = "no-store"
        return result

    async def handle(request):
        if request.method != "GET" and not same_origin(request):
            return respond({"ok": False, "error": "invalid_origin"}, 403)

        if slots.locked():
            return respond({"ok": False, "error": "guardian_busy"}, 429)
        async with slots:
            try:
                session = await session_for(request)
                if session is None:
                    return respond({"ok": False, "error": "unauthorized"}, 401)
                if request.method == "GET":
                    status, payload = await upstream(
                        "GET", "/mobile-api/v1/guardian/events", token=session.access
                    )
                    if status == 401:
                        return respond({"ok": False, "error": "unauthorized"}, 401)
                    if status != 200 or not isinstance(payload, dict) or payload.get("ok") is not True:
                        return respond({"ok": False, "error": "guardian_unavailable"}, 503)
                    items = payload.get("events")
                    if not isinstance(items, list):
                        return respond({"ok": False, "error": "guardian_unavailable"}, 503)
                    # Bound response size and never relay arbitrary upstream fields.
                    events = []
                    for row in items[:100]:
                        if not isinstance(row, dict):
                            continue
                        events.append({
                            "id": str(row.get("id") or "")[:36],
                            "kind": str(row.get("kind") or "")[:40],
                            "severity": str(row.get("severity") or "info")[:15],
                            "source": str(row.get("source") or "")[:40],
                            "detail": str(row.get("detail") or "")[:80],
                            "created_at": str(row.get("created_at") or "")[:40],
                            "read": row.get("read") is True,
                        })
                    return respond({
                        "ok": True, "events": events,
                        "unread": max(0, min(100000, int(payload.get("unread") or 0))),
                    })

                if request.content_length is None or request.content_length > 256:
                    return respond({"ok": False, "error": "invalid_request"}, 400)
                data = await request.json()
                if not isinstance(data, dict) or set(data) - {"event_id"}:
                    return respond({"ok": False, "error": "invalid_request"}, 400)
                event_id = data.get("event_id")
                if event_id is not None and (not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id)):
                    return respond({"ok": False, "error": "invalid_request"}, 400)
                status, payload = await upstream(
                    "POST", "/mobile-api/v1/guardian/events/read",
                    token=session.access, data={} if event_id is None else {"event_id": event_id}
                )
                if status == 401:
                    return respond({"ok": False, "error": "unauthorized"}, 401)
                if status != 200 or not isinstance(payload, dict) or payload.get("ok") is not True:
                    return respond({"ok": False, "error": "guardian_unavailable"}, 503)
                return respond({"ok": True, "updated": max(0, int(payload.get("updated") or 0))})
            except (ValueError, TypeError, web.HTTPBadRequest):
                return respond({"ok": False, "error": "invalid_request"}, 400)
            except Exception:
                return respond({"ok": False, "error": "guardian_unavailable"}, 503)

    app.router.add_get("/web-api/v1/features/guardian/events", handle)
    app.router.add_post("/web-api/v1/features/guardian/events/read", handle)
