"""Browser OAuth and email verification, using the existing VELIA session."""
import hashlib
import hmac
import json
import os
import re
import secrets
from base64 import urlsafe_b64encode
from urllib.parse import urlencode

from aiohttp import web
from cryptography.fernet import InvalidToken

from desktop.auth_admission import AuthAdmission
from velia_desktop_routes import AuthenticationUnavailable

AUTHORITY = "/mobile-api/v1/auth/web-signin"
CALLBACK = "/web-api/v1/auth/google/callback"
GOOGLE_COOKIE = "__Host-velia-google-flow"
EMAIL_COOKIE = "__Host-velia-email-flow"
FLOW_TTL = 600
OPAQUE = re.compile(r"[A-Za-z0-9_-]{43}")
ERRORS = {"signin_rate_limit", "signin_invalid_email", "signin_invalid_code",
          "signin_delivery_failed", "signin_identity_in_use", "signin_unavailable",
          "signin_google_failed", "unauthorized"}


def setup_signin_routes(app, *, origin, cipher, same_origin, session_for, upstream, json_response,
                        complete_signin):
    starts = AuthAdmission(attempts=8, concurrent=4)
    verifies = AuthAdmission(attempts=30, concurrent=6)

    def error(code, status):
        return json_response({"ok": False, "error": code}, status)

    async def authority(action, **fields):
        key = os.getenv("VELIA_WEB_SIGNIN_KEY", "")
        if not OPAQUE.fullmatch(key):
            return 503, {"ok": False, "error": "signin_unavailable"}
        return await upstream("POST", AUTHORITY, token=key, data={"action": action, **fields})

    async def options():
        status, data = await authority("options")
        if status != 200 or data.get("ok") is not True:
            return False, None
        google = data.get("google")
        if (not isinstance(google, dict) or google.get("redirect_uri") != origin + CALLBACK
                or not isinstance(google.get("client_id"), str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", google["client_id"])):
            google = None
        return data.get("email") is True, google

    def save_flow(response, name, flow):
        token = cipher.encrypt(json.dumps({"kind": name, **flow}).encode()).decode()
        response.set_cookie(name, token, max_age=FLOW_TTL, secure=True, httponly=True, samesite="Lax", path="/")

    def read_flow(request, name):
        token = request.cookies.get(name, "")
        if not token or len(token) > 2048:
            return {}
        try:
            value = json.loads(cipher.decrypt(token.encode(), ttl=FLOW_TTL))
            return value if isinstance(value, dict) and value.get("kind") == name else {}
        except (InvalidToken, ValueError, TypeError):
            return {}

    def clear_flow(response, name):
        response.set_cookie(name, "", max_age=0, secure=True, httponly=True, samesite="Lax", path="/")

    def redirect(result):
        return web.Response(status=303, headers={"Location": "/?" + urlencode({"signin": result}),
                            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def link_session(request, linking):
        current = await session_for(request)
        if linking:
            if current:
                return current
        elif current is None:
            return None
        raise PermissionError()

    async def list_methods(request):
        try:
            email, google = await options()
        except AuthenticationUnavailable:
            return error("signin_unavailable", 503)
        return json_response({"ok": True, "email": email, "google": google is not None})

    async def start_google(request, body):
        if set(body) != {"link"} or type(body["link"]) is not bool:
            return error("signin_invalid_request", 400)
        member = await link_session(request, body["link"])
        _, google = await options()
        if not google:
            return error("signin_unavailable", 503)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(32)
        challenge = urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            **google, "response_type": "code", "scope": "openid email profile", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
        })
        response = json_response({"ok": True, "url": url})
        save_flow(response, GOOGLE_COOKIE, {"state": state, "verifier": verifier, "member": member.user_id if member else None})
        return response

    async def finish_google(request):
        result = "signin_google_failed"
        flow = read_flow(request, GOOGLE_COOKIE)
        try:
            state, code = request.query.get("state", ""), request.query.get("code", "")
            if (not code or len(code) > 2048 or not flow or not OPAQUE.fullmatch(state)
                    or not hmac.compare_digest(state, flow["state"])):
                return redirect(result)
            if not verifies.admit(request):
                return redirect("signin_rate_limit")
            async with verifies.slots:
                member = await link_session(request, flow["member"] is not None)
                if (member.user_id if member else None) != flow["member"]:
                    return redirect("unauthorized")
                device = "velia-web:" + secrets.token_hex(16)
                fields = {"link_token": member.access} if member else {}
                status, data = await authority("google_verify", code=code, verifier=flow["verifier"], device_id=device, **fields)
                if status != 200 or data.get("ok") is not True:
                    return redirect(data.get("error") if data.get("error") in ERRORS else result)
                if member:
                    return redirect("linked" if data.get("linked") is True else result)
                response = redirect("signed_in")
                completed = await complete_signin(request, data, device, response)
                return completed if completed.status == 303 else redirect(result)
        except PermissionError:
            return redirect("unauthorized")
        except AuthenticationUnavailable:
            return redirect("signin_unavailable")

    async def google_callback(request):
        response = await finish_google(request)
        clear_flow(response, GOOGLE_COOKIE)
        return response

    async def start_email(request, body):
        if set(body) != {"email", "link"} or type(body["link"]) is not bool:
            return error("signin_invalid_request", 400)
        member = await link_session(request, body["link"])
        fields = {"link_token": member.access} if member else {}
        status, data = await authority("email_start", email=body["email"], **fields)
        if status != 200 or data.get("ok") is not True:
            return error(data.get("error") if data.get("error") in ERRORS else "signin_unavailable", status)
        challenge = data.get("challenge")
        if not isinstance(challenge, str) or not OPAQUE.fullmatch(challenge):
            return error("signin_unavailable", 503)
        response = json_response({"ok": True})
        save_flow(response, EMAIL_COOKIE, {"challenge": challenge, "member": member.user_id if member else None})
        return response

    async def verify_email(request, body):
        if set(body) != {"code"} or not isinstance(body["code"], str) or not re.fullmatch(r"[0-9]{8}", body["code"]):
            return error("signin_invalid_code", 400)
        flow = read_flow(request, EMAIL_COOKIE)
        if not flow:
            return error("signin_invalid_code", 401)
        member = await link_session(request, flow["member"] is not None)
        if (member.user_id if member else None) != flow["member"]:
            return error("unauthorized", 401)
        fields = {"link_token": member.access} if member else {}
        device = "velia-web:" + secrets.token_hex(16)
        status, data = await authority("email_verify", challenge=flow["challenge"], code=body["code"], device_id=device, **fields)
        if status != 200 or data.get("ok") is not True:
            return error(data.get("error") if data.get("error") in ERRORS else "signin_unavailable", status)
        if member:
            if data.get("linked") is not True:
                return error("signin_unavailable", 503)
            response = json_response({"ok": True, "linked": True})
        else:
            response = await complete_signin(request, data, device)
        clear_flow(response, EMAIL_COOKIE)
        return response

    def post(handler, admission):
        async def handle(request):
            if not same_origin(request):
                return error("invalid_origin", 403)
            if not admission.admit(request):
                return error("signin_rate_limit", 429)
            async with admission.slots:
                try:
                    body = await request.json()
                    if not isinstance(body, dict):
                        return error("signin_invalid_request", 400)
                    return await handler(request, body)
                except PermissionError:
                    return error("unauthorized", 401)
                except AuthenticationUnavailable:
                    return error("signin_unavailable", 503)
                except (ValueError, TypeError):
                    return error("signin_invalid_request", 400)
        return handle

    app.router.add_get("/web-api/v1/auth/methods", list_methods)
    app.router.add_post("/web-api/v1/auth/google/start", post(start_google, starts))
    app.router.add_get(CALLBACK, google_callback, allow_head=False)
    app.router.add_post("/web-api/v1/auth/email/start", post(start_email, starts))
    app.router.add_post("/web-api/v1/auth/email/verify", post(verify_email, verifies))
