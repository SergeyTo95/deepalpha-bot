"""Per-user Guardian security inbox. Independent of chats and Pulse reminders."""
import asyncio
from urllib.parse import urlsplit

from aiohttp import web

from services import velia_guardian_service as guardian


def setup_guardian_routes(app, mobile_module, web_user_resolver):
    slots = asyncio.Semaphore(8)

    def reply(payload, status=200):
        response = mobile_module._json_response(payload, status=status)
        response.headers["Cache-Control"] = "no-store"
        return response

    def web_origin_safe(request):
        # Cookie-authenticated write endpoints require same-origin requests.
        host = request.host.lower()
        for header in ("Origin", "Referer"):
            raw = str(request.headers.get(header, "") or "")
            if raw:
                url = urlsplit(raw)
                if url.netloc.lower() != host or url.scheme not in {"https", "http"}:
                    return False
        return request.headers.get("Sec-Fetch-Site", "") not in {"cross-site", "same-site"}

    async def handle(request):
        is_mobile = request.path.startswith("/mobile-api/")
        if is_mobile:
            if not mobile_module._mobile_api_available():
                return mobile_module._disabled_response()
            auth = mobile_module._require_mobile_auth(request)
            user_id = int(auth.get("user_id") or 0) if auth else 0
        else:
            user_id = int(web_user_resolver(request) or 0)
            if request.method != "GET" and not web_origin_safe(request):
                return reply({"ok": False, "error": "origin_not_allowed"}, 403)
        if user_id <= 0:
            return reply({"ok": False, "error": "unauthorized"}, 401)
        if slots.locked():
            return reply({"ok": False, "error": "guardian_busy"}, 429)
        try:
            async with slots:
                if request.method == "GET":
                    result = await asyncio.to_thread(guardian.list_events, user_id)
                    return reply({"ok": True, **result})
                if request.content_length is not None and request.content_length > 512:
                    return reply({"ok": False, "error": "invalid_request"}, 413)
                data = await request.json()
                if not isinstance(data, dict) or set(data) - {"event_id"}:
                    return reply({"ok": False, "error": "invalid_request"}, 400)
                event_id = data.get("event_id")
                if event_id is not None and not isinstance(event_id, str):
                    return reply({"ok": False, "error": "invalid_request"}, 400)
                updated = await asyncio.to_thread(
                    guardian.mark_read, user_id, event_id=event_id,
                )
                return reply({"ok": True, "updated": updated})
        except (ValueError, TypeError):
            return reply({"ok": False, "error": "invalid_request"}, 400)
        except Exception:
            return reply({"ok": False, "error": "guardian_unavailable"}, 503)

    for prefix in ("/mobile-api/v1/guardian", "/api/security"):
        app.router.add_get(prefix + "/events", handle)
        app.router.add_post(prefix + "/events/read", handle)
