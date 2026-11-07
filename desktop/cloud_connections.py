"""User-owned Railway and Vercel MCP connections using their OAuth servers."""
import time

from aiohttp import ClientError, ClientSession, ClientTimeout

from desktop.figma_connector import FigmaConnector, FigmaError
from desktop.figma_oauth import FigmaOAuth
from services.velia_structured_output import strict_json_loads

PROVIDERS = {
    "railway": {"name": "Railway", "endpoint": "https://mcp.railway.com",
        "issuer": "https://backboard.railway.com", "metadata": "https://backboard.railway.com/.well-known/openid-configuration",
        "authorize": "https://backboard.railway.com/oauth/auth", "register": "https://backboard.railway.com/oauth/register",
        "token": "https://backboard.railway.com/oauth/token", "scope": "openid workspace:member"},
    "vercel": {"name": "Vercel", "endpoint": "https://mcp.vercel.com/",
        "issuer": "https://vercel.com", "metadata": "https://vercel.com/.well-known/oauth-authorization-server",
        "authorize": "https://vercel.com/oauth/authorize", "token": "https://api.vercel.com/login/oauth/token",
        "scope": "openid", "cimd": True},
}


class CloudOAuth(FigmaOAuth):
    """Reuse session binding, PKCE and one-time callback consumption."""
    def __init__(self, bridge, origin, provider, request=None):
        super().__init__(bridge, origin, request)
        self.provider = provider
        self.settings = PROVIDERS[provider]
        self.callback = origin.rstrip("/") + "/web-api/v1/connections/" + provider + "/callback"
        self.client_metadata_url = origin.rstrip("/") + "/.well-known/velia/" + provider + ".json"
        self.authorization_endpoint = self.settings["authorize"]
        self.resource = self.settings["endpoint"]
        self.request = request or self.http

    async def http(self, method, path, *, data=None):
        paths = {"/.well-known/oauth-authorization-server": self.settings["metadata"],
                 "/oauth/register": self.settings.get("register"), "/token": self.settings["token"]}
        url = paths.get(path)
        if not url:
            raise FigmaError("connection_unavailable", 503)
        try:
            async with ClientSession(timeout=ClientTimeout(total=20), trust_env=False) as client:
                async with client.request(method, url, headers={"User-Agent": "VELIA/1.0", "Accept": "application/json"},
                        **({"data": data} if path == "/token" else {"json": data}), allow_redirects=False) as response:
                    if response.status not in {200, 201}:
                        raise FigmaError("connection_authorization_failed", 503)
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > 64000:
                            raise FigmaError("connection_protocol_error", 502)
                        chunks.append(chunk)
                    result = strict_json_loads(b"".join(chunks).decode())
                    if not isinstance(result, dict):
                        raise FigmaError("connection_protocol_error", 502)
                    return result
        except (ClientError, OSError, TimeoutError, ValueError, UnicodeError):
            raise FigmaError("connection_unavailable", 503) from None

    def metadata(self):
        return {"client_id": self.client_metadata_url, "client_name": "VELIA",
                "client_uri": self.client_metadata_url.split("/.well-known/", 1)[0],
                "redirect_uris": [self.callback], "grant_types": ["authorization_code"],
                "response_types": ["code"], "token_endpoint_auth_method": "none"}

    async def register(self):
        async with self.registration_lock:
            if self.registration and self.registration["expires"] > time.monotonic():
                return self.registration
            metadata = await self.request("GET", "/.well-known/oauth-authorization-server")
            expected = {"issuer": self.settings["issuer"], "authorization_endpoint": self.settings["authorize"],
                        "token_endpoint": self.settings["token"]}
            if any(metadata.get(key) != value for key, value in expected.items()) or "S256" not in metadata.get("code_challenge_methods_supported", []):
                raise FigmaError("connection_protocol_error", 502)
            if self.settings.get("cimd"):
                if metadata.get("client_id_metadata_document_supported") is not True:
                    raise FigmaError("connection_authorization_unavailable", 503)
                client_id = self.client_metadata_url
            else:
                if metadata.get("registration_endpoint") != self.settings["register"]:
                    raise FigmaError("connection_protocol_error", 502)
                result = await self.request("POST", "/oauth/register", data={
                    "client_name": "VELIA", "redirect_uris": [self.callback], "response_types": ["code"],
                    "grant_types": ["authorization_code"], "token_endpoint_auth_method": "none"})
                client_id = result.get("client_id")
                if (not isinstance(client_id, str) or not client_id or len(client_id) > 500
                        or result.get("token_endpoint_auth_method") != "none" or result.get("redirect_uris") != [self.callback]):
                    raise FigmaError("connection_protocol_error", 502)
            self.registration = {"client_id": client_id, "scope": self.settings["scope"], "expires": time.monotonic() + 86400}
            return self.registration


def create_cloud_connections(origin):
    connections = {}
    for provider, settings in PROVIDERS.items():
        bridge = FigmaConnector(endpoint=settings["endpoint"], pair_required=False)
        oauth = CloudOAuth(bridge, origin, provider)
        bridge.oauth = oauth
        connections[provider] = bridge
    return connections
