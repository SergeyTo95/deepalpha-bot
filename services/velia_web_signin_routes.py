"""Identity authority for the VELIA web gateway."""
import asyncio
import hmac
import logging
import os
import re
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from services import velia_web_signin_store as store
from services.velia_mobile_auth_service import authenticate_access_token, issue_web_identity_session

PATH = "/mobile-api/v1/auth/web-signin"
GOOGLE_CALLBACK = "/web-api/v1/auth/google/callback"
LOG = logging.getLogger(__name__)
OPAQUE = re.compile(r"[A-Za-z0-9_-]{43}")
EMAIL = re.compile(r"[a-z0-9_!#$%&'*+/=?^`{|}~-]+(?:\.[a-z0-9_!#$%&'*+/=?^`{|}~-]+)*@"
                   r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def bridge_key():
    value = os.getenv("VELIA_WEB_SIGNIN_KEY", "")
    return value if OPAQUE.fullmatch(value) else ""


def google_settings():
    client = os.getenv("VELIA_WEB_GOOGLE_CLIENT_ID", "").strip()
    secret = os.getenv("VELIA_WEB_GOOGLE_CLIENT_SECRET", "").strip()
    redirect = os.getenv("VELIA_WEB_GOOGLE_REDIRECT_URI", "").strip()
    target = urlsplit(redirect)
    if (client and secret and target.scheme == "https" and target.hostname and not target.username
            and target.path == GOOGLE_CALLBACK and not target.query and not target.fragment):
        return client, secret, redirect
    return None


def email_settings():
    key = os.getenv("VELIA_WEB_SIGNIN_RESEND_KEY", "").strip()
    sender = os.getenv("VELIA_WEB_SIGNIN_FROM", "").strip()
    return (key, sender) if key and EMAIL.fullmatch(sender.lower()) else None


def normalize_email(value):
    if not isinstance(value, str):
        raise store.SignInError("signin_invalid_email", 400)
    address = value.strip().lower()
    if len(address) > 254 or not EMAIL.fullmatch(address) or len(address.split("@")[0]) > 64:
        raise store.SignInError("signin_invalid_email", 400)
    return address


async def google_profile(settings, code, verifier):
    client, secret, redirect = settings
    # The access token comes directly from Google's token endpoint. Use it
    # exclusively with Google's authenticated UserInfo endpoint for identity.
    async with ClientSession(timeout=ClientTimeout(total=8)) as http:
        async with http.post("https://oauth2.googleapis.com/token", allow_redirects=False, data={
            "client_id": client, "client_secret": secret, "redirect_uri": redirect,
            "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
        }) as response:
            if response.status != 200:
                raise store.SignInError("signin_google_failed", 401 if response.status < 500 else 503)
            token = (await response.json()).get("access_token")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+\-/=]{1,4096}", token):
            raise store.SignInError("signin_google_failed", 401)
        async with http.get("https://openidconnect.googleapis.com/v1/userinfo", allow_redirects=False,
                            headers={"Authorization": f"Bearer {token}"}) as response:
            if response.status != 200:
                raise store.SignInError("signin_google_failed", 401 if response.status < 500 else 503)
            profile = await response.json()
    subject = profile.get("sub")
    if not isinstance(subject, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", subject):
        raise store.SignInError("signin_google_failed", 401)
    email = profile.get("email")
    email = email[:254] if isinstance(email, str) and profile.get("email_verified") is True else ""
    name = profile.get("name")
    name = name.strip()[:120] if isinstance(name, str) else ""
    return subject, email, name


async def send_code(settings, email, challenge, code):
    key, sender = settings
    async with ClientSession(timeout=ClientTimeout(total=8)) as http:
        async with http.post("https://api.resend.com/emails", allow_redirects=False,
            headers={"Authorization": f"Bearer {key}", "Idempotency-Key": f"velia-signin-{challenge}"},
            json={"from": f"VELIA <{sender}>", "to": [email], "subject": "Код входа в VELIA",
                  "text": f"Код входа в VELIA: {code}\n\nКод действует 10 минут. Если ты его не запрашивал, закрой это письмо."},
        ) as response:
            if response.status not in {200, 201, 202}:
                LOG.warning("VELIA_SIGNIN_MAIL_REJECTED status=%s", response.status)
                raise store.SignInError("signin_delivery_failed", 503)


def setup_web_signin_authority(app, *, json_response, available, read_json, bearer_token):
    email_ready = False

    async def lifecycle(application):
        nonlocal email_ready
        if bridge_key() and email_settings():
            try:
                await asyncio.to_thread(store.initialize)
                email_ready = True
            except Exception:
                LOG.error("VELIA_SIGNIN_EMAIL_SCHEMA_UNAVAILABLE")
        yield

    async def handler(request):
        key = bridge_key()
        token = bearer_token(request)
        if not key or not OPAQUE.fullmatch(token) or not hmac.compare_digest(key, token):
            return json_response({"ok": False, "error": "unauthorized"}, 401)
        if not available():
            return json_response({"ok": False, "error": "signin_unavailable"}, 503)
        try:
            body = await read_json(request)
            if body is None or request.content_length and request.content_length > 8192:
                raise store.SignInError("signin_invalid_request", 400)
            action = body.get("action")
            google = google_settings()
            mail = email_settings() if email_ready else None
            if action == "options":
                return json_response({"ok": True, "email": bool(mail), "google": (
                    {"client_id": google[0], "redirect_uri": google[2]} if google else None)})
            link_user_id = None
            if "link_token" in body:
                access = body["link_token"]
                if not isinstance(access, str) or not re.fullmatch(r"va_[A-Za-z0-9_-]{64}", access):
                    raise store.SignInError("unauthorized", 401)
                member = await asyncio.to_thread(authenticate_access_token, access)
                if not member:
                    raise store.SignInError("unauthorized", 401)
                link_user_id = member["user_id"]
            if action == "email_start" and mail:
                email = normalize_email(body.get("email"))
                challenge, code = await asyncio.to_thread(store.start_email, email, link_user_id, key)
                await send_code(mail, email, challenge, code)
                return json_response({"ok": True, "challenge": challenge})

            device = body.get("device_id")
            if not isinstance(device, str) or not re.fullmatch(r"velia-web:[0-9a-f]{32}", device):
                raise store.SignInError("signin_invalid_request", 400)
            if action == "email_verify" and mail:
                challenge, code = body.get("challenge"), body.get("code")
                if (not isinstance(challenge, str) or not OPAQUE.fullmatch(challenge)
                        or not isinstance(code, str) or not re.fullmatch(r"[0-9]{8}", code)):
                    raise store.SignInError("signin_invalid_code", 400)
                email = await asyncio.to_thread(store.confirm_email, challenge, code, link_user_id, key)
                if not email:
                    raise store.SignInError("signin_invalid_code", 401)
                provider, subject, name = "email", email, ""
            elif action == "google_verify" and google:
                code, verifier = body.get("code"), body.get("verifier")
                if (not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,2048}", code)
                        or not isinstance(verifier, str) or not OPAQUE.fullmatch(verifier)):
                    raise store.SignInError("signin_google_failed", 400)
                subject, email, name = await google_profile(google, code, verifier)
                provider = "google"
            else:
                raise store.SignInError("signin_unavailable", 503)
            user_id = await asyncio.to_thread(store.resolve_identity, provider, subject,
                email=email, name=name, link_user_id=link_user_id)
            if link_user_id is not None:
                return json_response({"ok": True, "linked": True})
            result = await asyncio.to_thread(issue_web_identity_session, user_id, device_id=device)
            return json_response(result)
        except store.SignInError as exc:
            return json_response({"ok": False, "error": exc.code}, exc.status)
        except (ClientError, TimeoutError, OSError, ValueError, TypeError, AttributeError):
            LOG.warning("VELIA_SIGNIN_PROVIDER_UNAVAILABLE")
            return json_response({"ok": False, "error": "signin_unavailable"}, 503)
        except Exception:
            LOG.error("VELIA_SIGNIN_AUTHORITY_UNAVAILABLE")
            return json_response({"ok": False, "error": "signin_unavailable"}, 503)

    app.cleanup_ctx.append(lifecycle)
    app.router.add_post(PATH, handler)
