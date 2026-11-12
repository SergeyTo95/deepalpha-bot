"""Explicit mobile feature routes exposed through the encrypted Web session."""
import json
import hashlib
import re
from aiohttp import web
from velia_desktop_routes import AuthenticationUnavailable

ROUTES = [["GET","projects"],["GET","projects/{projectId}"],["POST","projects"],["PATCH","projects/{projectId}"],["GET","project-resources"],["POST","project-resources"],["PATCH","project-resources/{resourceId}"],["GET","medical/status"],["GET","medical/cases"],["POST","medical/cases"],["GET","medical/cases/{caseId}"],["POST","medical/cases/{caseId}/study/begin"],["POST","medical/cases/{caseId}/study/complete"],["POST","medical/cases/{caseId}/research"],["GET","research/status"],["GET","research/missions"],["POST","research/missions"],["GET","research/missions/{missionId}"],["GET","research/missions/{missionId}/sources"],["GET","research/missions/{missionId}/evidence-claims"],["GET","research/missions/{missionId}/claims"],["GET","research/missions/{missionId}/reports"],["GET","research/missions/{missionId}/scientific-alerts"],["GET","research/claims/{claimId}/timeline"],["POST","research/missions/{missionId}/literature"],["POST","research/missions/{missionId}/synthesize"],["POST","research/missions/{missionId}/reports"],["POST","research/missions/{missionId}/runs"],["POST","research/scientific-alerts/{alertId}/acknowledge"],["GET","profile"],["PATCH","profile"],["GET","plugins"],["PATCH","plugins"],["GET","studio/status"],["GET","studio/sessions"],["POST","studio/sessions"],["GET","studio/sessions/{sessionId}"],["GET","studio/sessions/{sessionId}/messages"],["POST","studio/sessions/{sessionId}/assets"],["POST","studio/sessions/{sessionId}/generate"],["GET","usage"],["GET","agent/status"],["GET","agent/tools"],["POST","agent/jobs"],["GET","agent/jobs/{jobId}"],["POST","agent/jobs/{jobId}/actions/{actionId}/approve"],["POST","agent/jobs/{jobId}/actions/{actionId}/reject"],["POST","agent/jobs/{jobId}/run"],["GET","agent/connectors/google-calendar/status"],["GET","agent/connectors/google-calendar/connect"],["DELETE","agent/connectors/google-calendar"],["GET","agents/status"],["GET","agents/capabilities"],["GET","agents"],["POST","agents"],["DELETE","agents/{agentId}"],["GET","agents/{agentId}/conversations"],["POST","agents/{agentId}/conversations"],["GET","developer/projects"],["GET","developer/factory/status"],["GET","developer/autopilot/status"],["GET","developer/autopilot/ci/status"],["GET","developer/autopilot/review/status"],["GET","developer/autopilot/merge-policy/status"],["GET","developer/autopilot/missions"],["POST","developer/autopilot/missions"],["POST","developer/autopilot/missions/{missionId}/activate"],["POST","developer/autopilot/missions/{missionId}/pause"],["GET","developer/autopilot/missions/{missionId}/tasks"],["POST","developer/autopilot/missions/{missionId}/tasks"],["POST","developer/autopilot/tasks/{taskId}/cancel"],["GET","developer/autopilot/missions/{missionId}/runs"],["GET","developer/autopilot/runs/{runId}/ci"],["GET","developer/autopilot/runs/{runId}/reviews"],["GET","developer/autopilot/runs/{runId}/merge-policy"]]
ROUTES += [("GET", "pulse"), ("POST", "pulse"), ("GET", "pulse/settings"), ("PATCH", "pulse/settings"), ("POST", "pulse/{item_id}"), ("DELETE", "pulse/{item_id}"), ("POST", "pulse/notifications/{notification_id}/read")]
ROUTES += [("GET", "notebook"), ("PUT", "notebook")]
ROUTES += [("GET", "economy/me"), ("GET", "economy/catalog")]
ROUTES += [(method, "notebook/materials/{material_id}") for method in ("GET", "PUT", "DELETE")]
ROUTES += [("GET", "chat-topics"), ("POST", "chat-topics"), ("GET", "conversations/{conversation_id}")]
ROUTES += [("GET", "developer/status"), ("POST", "developer/github/disconnect"), ("POST", "developer/github/web-install-url"),
           ("GET", "developer/installations"), ("GET", "developer/repositories"), ("POST", "developer/projects")]
ROUTES += [("GET", "agents/{agentId}"), ("PATCH", "agents/{agentId}")]
ROUTES += [("PATCH", "conversations/{conversation_id}"), ("POST", "conversations/{conversation_id}/share")]
ROUTES += [("GET", "agent/schedules/status"), ("GET", "agent/schedules"), ("POST", "agent/schedules"),
           ("GET", "agent/schedules/{scheduleId}"), ("POST", "agent/schedules/{scheduleId}/enable"),
           ("POST", "agent/schedules/{scheduleId}/disable"), ("DELETE", "agent/schedules/{scheduleId}")]
ROUTES += [("GET", "research/missions/{missionId}/runs"), ("GET", "research/runs/{runId}"),
           ("POST", "research/runs/{runId}/cancel")]
PATTERNS = [(method, re.compile(re.sub(r"\{[^}]+\}", "[A-Za-z0-9_-]{1,128}", path) + r"\Z"))
            for method, path in ROUTES]
BINARY_ROUTES = [
    ("POST", re.compile(r"conversations/[A-Za-z0-9_-]{1,128}/attachments\Z")),
    ("POST", re.compile(r"studio/sessions/[A-Za-z0-9_-]{1,128}/assets\Z")),
    ("PUT", re.compile(r"medical/cases/[A-Za-z0-9_-]{1,128}/study/chunks/[0-9]{1,6}\Z")),
    ("GET", re.compile(r"studio-assets/[A-Za-z0-9_-]{1,128}/content\Z")),
    ("GET", re.compile(r"media/(?:images|videos|music)/[A-Za-z0-9_-]{1,128}/content\Z")),
]
PATTERNS += [
    ('GET', re.compile(r'economy/creem/(?:catalog|account)\Z')),
    ('POST', re.compile(r'economy/creem/(?:checkout|portal)\Z')),
]
QUERY_KEYS = {"offset", "limit", "conversation_id", "mode", "kind", "project_id", "before_revision", "unread_only", "client_request_id", "user_id", "expires", "signature", "installation_id"}
SENSITIVE = {"access_token", "refresh_token", "api_key", "provider_api_key", "authorization", "worker_url", "worker_token"}

def safe_result(value):
    if isinstance(value, dict):
        return {k: safe_result(v) for k, v in value.items() if k.lower() not in SENSITIVE}
    if isinstance(value, list):
        return [safe_result(v) for v in value]
    return value

def route_allowed(method, path):
    if path.startswith('pulse/push/'):
        return (method,path) in {('GET','pulse/push/config'),('POST','pulse/push/devices'),('POST','pulse/push/receipt')} or bool(method=='DELETE' and re.fullmatch(r'pulse/push/devices/[a-f0-9-]{36}',path))
    if path.startswith('pulse/') and path!='pulse/settings':
        return bool((method in {'POST','DELETE'} and re.fullmatch(r'pulse/[a-f0-9-]{36}',path)) or (method=='POST' and re.fullmatch(r'pulse/notifications/[a-f0-9-]{36}/read',path)))
    return any(method == verb and pattern.fullmatch(path) for verb, pattern in PATTERNS)

def setup_feature_routes(app, *, session_for, same_origin, upstream, json_response, binary_upstream=None, origin=""):
    from desktop.platform_status import setup_platform_status
    setup_platform_status(app, session_for=session_for, upstream=upstream, json_response=json_response)
    async def relay(request):
        def error(code, status):
            return json_response({"ok": False, "error": code}, status)
        path = request.match_info["path"]
        binary = any(request.method == verb and pattern.fullmatch(path) for verb, pattern in BINARY_ROUTES)
        if not route_allowed(request.method, path) and not binary:
            return error("feature_route_not_found", 404)
        query_keys = QUERY_KEYS | ({"q", "category"} if request.method == "GET" and path == "agents/capabilities" else set())
        if set(request.query) - query_keys or any(len(v) > 256 for v in request.query.values()):
            return error("invalid_request", 400)
        if request.method != "GET":
            if binary:
                valid = (request.headers.get("Origin") == origin
                    and request.headers.get("X-Velia-Request") == "1"
                    and request.headers.get("Sec-Fetch-Site", "same-origin") == "same-origin")
            else:
                valid = same_origin(request)
            if not valid:
                return error("invalid_origin", 403)
        try:
            session = await session_for(request)
            if not session:
                return error("unauthorized", 401)
            target = "/mobile-api/v1/" + path
            if path.startswith("studio-assets/"):
                target = "/api/mobile/studio/assets/" + path.split("/")[1] + "/content"
            elif path.startswith("media/"):
                target = "/api/mobile/" + path.removeprefix("media/")
            if 'user_id' in request.query and request.query['user_id'] != str(session.user_id):
                return error('unauthorized', 403)
            if request.query_string:
                from urllib.parse import urlencode
                target += "?" + urlencode(list(request.query.items()))
            if binary:
                if binary_upstream is None:
                    return error("feature_unavailable", 503)
                return await binary_upstream(request, target, session.access)
            data = None
            if request.method != "GET":
                if path == 'notebook' or path.startswith('notebook/materials/'):
                    limit = 7 * 1024 * 1024 + 4096 if path.startswith('notebook/materials/') else 710000
                    body = bytearray()
                    async for chunk in request.content.iter_chunked(16384):
                        body.extend(chunk)
                        if len(body) > limit:return error('request_too_large', 413)
                    data = json.loads(body)
                else:
                    data = await request.json()
                if not isinstance(data, dict):
                    return error("invalid_request", 400)
            if (path == 'notebook' or path.startswith('notebook/materials/') or path == 'pulse' or path.startswith('pulse/')) and request.method != 'GET':
                account = hashlib.sha256((origin + ':' + str(session.user_id)).encode()).hexdigest()[:24]
                if data.get('account') != account:return error('notebook_owner_changed', 403)
            if path=='pulse/push/devices' and request.method=='POST':data={**data,'origin':origin}
            key = request.headers.get('Idempotency-Key')
            if key and not re.fullmatch(r'[A-Za-z0-9:_-]{8,128}', key):
                return error('invalid_request', 400)
            status, result = await upstream(request.method, target, token=session.access, data=data,
                **({'idempotency_key': key} if key else {}))
            if (path == 'notebook' or path == 'pulse' or path.startswith('pulse/')) and isinstance(result, dict) and result.get('ok'):
                result = {**result, 'account':hashlib.sha256((origin + ':' + str(session.user_id)).encode()).hexdigest()[:24]}
            return json_response(safe_result(result), status)
        except web.HTTPRequestEntityTooLarge:
            return error("request_too_large", 413)
        except (ValueError, UnicodeDecodeError):
            return error("invalid_request", 400)
        except AuthenticationUnavailable:
            return error("feature_unavailable", 503)
    # Origin is derived from the same trusted config used by the session adapter.
    app.router.add_route("*", "/web-api/v1/features/{path:.*}", relay)
