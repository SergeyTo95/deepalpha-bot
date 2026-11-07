"""Session-bound OAuth authorization for the existing Figma cloud bridge."""
import asyncio
import base64
import hashlib
import math
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

from desktop.figma_connector import FigmaError
from services.velia_structured_output import strict_json_loads

ORIGIN = "https://figma-console-mcp.southleft.com"
PATHS = {"/.well-known/oauth-authorization-server", "/oauth/register", "/token"}


async def oauth_http(method, path, *, data=None):
    if path not in PATHS:
        raise FigmaError("figma_authorization_unavailable", 503)
    try:
        async with ClientSession(timeout=ClientTimeout(total=20), trust_env=False) as client:
            async with client.request(method, ORIGIN + path, json=data,
                    headers={"User-Agent": "VELIA/1.0", "Accept": "application/json"}, allow_redirects=False) as response:
                if response.status not in {200, 201}:
                    raise FigmaError("figma_authorization_unavailable", 503)
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > 64000:
                        raise FigmaError("figma_authorization_unavailable", 502)
                    chunks.append(chunk)
                result = strict_json_loads(b"".join(chunks).decode())
                if not isinstance(result, dict):
                    raise FigmaError("figma_authorization_unavailable", 502)
                return result
    except (ClientError, OSError, TimeoutError, ValueError, UnicodeError):
        raise FigmaError("figma_authorization_unavailable", 503) from None


class FigmaOAuth:
    def __init__(self, bridge, origin, request=None):
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("Trusted HTTPS origin required")
        self.bridge = bridge
        self.callback = origin.rstrip("/") + "/web-api/v1/figma/callback"
        self.authorization_endpoint = ORIGIN + "/authorize"
        self.resource = None
        self.request = request or oauth_http
        self.pending = {}
        self.registration = None
        self.registration_lock = asyncio.Lock()

    def prune(self):
        now = time.monotonic()
        for owner, item in list(self.pending.items()):
            if item["expires"] < now:
                del self.pending[owner]

    def cancel(self, owner):
        self.pending.pop(owner, None)

    def reject(self, owner, state):
        flow = self.pending.get(owner)
        if flow and isinstance(state, str) and secrets.compare_digest(flow["state"], state):
            self.cancel(owner)

    async def register(self):
        async with self.registration_lock:
            if self.registration and self.registration["expires"] > time.monotonic():
                return self.registration
            metadata = await self.request("GET", "/.well-known/oauth-authorization-server")
            required = {"issuer": ORIGIN, "authorization_endpoint": ORIGIN + "/authorize",
                        "registration_endpoint": ORIGIN + "/oauth/register", "token_endpoint": ORIGIN + "/token"}
            if any(str(metadata.get(key, "")).rstrip("/") != value for key, value in required.items()):
                raise FigmaError("figma_authorization_unavailable", 502)
            if "S256" not in metadata.get("code_challenge_methods_supported", []):
                raise FigmaError("figma_authorization_unavailable", 502)
            scopes = metadata.get("scopes_supported", [])
            if not isinstance(scopes, list) or len(scopes) > 100 or any(not isinstance(scope, str)
                    or not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", scope) for scope in scopes):
                raise FigmaError("figma_authorization_unavailable", 502)
            result = await self.request("POST", "/oauth/register", data={
                "client_name": "VELIA", "redirect_uris": [self.callback],
                "grant_types": ["authorization_code"], "response_types": ["code"],
                "token_endpoint_auth_method": "none"})
            client_id = result.get("client_id")
            if (not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,500}", client_id)
                    or result.get("token_endpoint_auth_method") != "none"
                    or result.get("redirect_uris") != [self.callback]):
                raise FigmaError("figma_authorization_unavailable", 502)
            self.registration = {"client_id": client_id, "scope": " ".join(scopes),
                                 "expires": time.monotonic() + 86400}
            return self.registration

    async def start(self, owner, *, options=None):
        self.prune()
        old = self.pending.get(owner)
        if old and old["started"] > time.monotonic() - 10:
            raise FigmaError("figma_busy", 429)
        if owner not in self.pending and len(self.pending) >= 256:
            raise FigmaError("figma_busy", 429)
        flow = {"state": secrets.token_urlsafe(32), "verifier": secrets.token_urlsafe(48),
                "expires": time.monotonic() + 600, "started": time.monotonic(), "status": "starting"}
        self.pending[owner] = flow
        try:
            registration = await self.register()
            if self.pending.get(owner) is not flow:
                raise FigmaError("figma_authorization_cancelled", 409)
            flow["client_id"] = registration["client_id"]
            flow["status"] = "pending"
            challenge = base64.urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest()).decode().rstrip("=")
            return {"authorization_url": self.authorization_endpoint + "?" + urlencode({
                "client_id": flow["client_id"], "redirect_uri": self.callback,
                "response_type": "code", "state": flow["state"], "code_challenge": challenge,
                "code_challenge_method": "S256", "scope": registration["scope"],
                **({"resource": self.resource} if self.resource else {})})}
        except BaseException:
            if self.pending.get(owner) is flow:
                self.pending.pop(owner, None)
            raise

    async def complete(self, owner, state, code, *, callback_query=None):
        self.prune()
        flow = self.pending.get(owner)
        if (not flow or flow["status"] != "pending" or not isinstance(state, str)
                or not secrets.compare_digest(flow["state"], state)
                or not isinstance(code, str) or not 1 <= len(code) <= 2000):
            raise FigmaError("figma_invalid_callback", 400)
        flow["status"] = "exchanging"
        try:
            result = await self.request("POST", "/token", data={
                "grant_type": "authorization_code", "client_id": flow["client_id"],
                "redirect_uri": self.callback, "code": code, "code_verifier": flow["verifier"],
                **({"resource": self.resource} if self.resource else {})})
            if self.pending.get(owner) is not flow:
                raise FigmaError("figma_authorization_cancelled", 409)
            token, lifetime = result.get("access_token"), result.get("expires_in")
            if (not isinstance(token, str) or not re.fullmatch(r"[\x21-\x7e]{10,4096}", token)
                    or str(result.get("token_type", "")).lower() != "bearer"
                    or isinstance(lifetime, bool) or not isinstance(lifetime, (int, float))
                    or not math.isfinite(lifetime) or lifetime < 60):
                raise FigmaError("figma_invalid_callback", 502)
            paired = await self.bridge.connect(owner, token, oauth_expires=time.time() + min(lifetime, 90 * 86400))
            if self.pending.get(owner) is not flow:
                connection = self.bridge.connections.get(owner)
                if connection and connection["id"] == paired["connection_id"]:
                    self.bridge.connections.pop(owner, None)
                raise FigmaError("figma_authorization_cancelled", 409)
            return paired
        finally:
            if self.pending.get(owner) is flow:
                self.pending.pop(owner, None)
