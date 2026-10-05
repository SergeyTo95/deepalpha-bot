"""Owner-admin reverse proxy for the isolated VELIA gateway.

The browser stays on the gateway origin while Velyon Core remains the authority
for owner authentication, CSRF checks, research jobs and downloadable datasets.
No Desktop/Web session is translated into an admin session.
"""
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientTimeout, web

_ALLOWED_METHODS = {"GET", "HEAD", "POST"}
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}
_MAX_REQUEST_BODY = 256 * 1024


def _request_headers(request: web.Request) -> dict[str, str]:
    allowed = {
        "accept", "accept-language", "content-type", "cookie", "referer",
        "user-agent", "x-request-id",
    }
    return {name: value for name, value in request.headers.items()
            if name.lower() in allowed}


def _redirect_location(value: str, upstream_origin: str) -> str:
    if not value:
        return value
    parsed = urlsplit(value)
    if not parsed.scheme and not parsed.netloc:
        return value
    upstream = urlsplit(upstream_origin)
    if parsed.scheme == upstream.scheme and parsed.netloc == upstream.netloc:
        return parsed.path + (("?" + parsed.query) if parsed.query else "")
    raise ValueError("external_admin_redirect")


def _secure_cookie(value: str) -> str:
    return value if "secure" in {part.strip().lower() for part in value.split(";")} else value + "; Secure"


def setup_owner_admin_proxy(app: web.Application, *, upstream_origin: str, client_key) -> None:
    upstream_origin = upstream_origin.rstrip("/")

    async def proxy(request: web.Request) -> web.StreamResponse:
        if request.method not in _ALLOWED_METHODS:
            raise web.HTTPMethodNotAllowed(request.method, sorted(_ALLOWED_METHODS))
        body = bytearray()
        if request.can_read_body:
            async for chunk in request.content.iter_chunked(8192):
                body.extend(chunk)
                if len(body) > _MAX_REQUEST_BODY:
                    raise web.HTTPRequestEntityTooLarge(max_size=_MAX_REQUEST_BODY, actual_size=len(body))
        target = upstream_origin + request.rel_url.path_qs
        try:
            async with app[client_key].request(
                request.method, target, data=bytes(body) if body else None,
                headers=_request_headers(request), allow_redirects=False,
                timeout=ClientTimeout(total=45, sock_read=40),
            ) as upstream:
                headers = {}
                for name, value in upstream.headers.items():
                    lower = name.lower()
                    if lower in _HOP_BY_HOP or lower in {"content-length", "set-cookie", "location"}:
                        continue
                    headers[name] = value
                if upstream.headers.get("Location"):
                    try:
                        headers["Location"] = _redirect_location(upstream.headers["Location"], upstream_origin)
                    except ValueError:
                        return web.Response(text="Admin redirect rejected", status=502,
                                            headers={"Cache-Control": "no-store"})
                response = web.StreamResponse(status=upstream.status, reason=upstream.reason, headers=headers)
                for cookie in upstream.headers.getall("Set-Cookie", []):
                    response.headers.add("Set-Cookie", _secure_cookie(cookie))
                response.headers["Cache-Control"] = upstream.headers.get("Cache-Control", "no-store")
                await response.prepare(request)
                if request.method != "HEAD":
                    async for chunk in upstream.content.iter_chunked(65536):
                        await response.write(chunk)
                await response.write_eof()
                return response
        except (ClientError, TimeoutError, OSError):
            return web.Response(text="VELIA Core admin is temporarily unavailable", status=503,
                                headers={"Cache-Control": "no-store"})

    app.router.add_route("*", "/admin", proxy)
    app.router.add_route("*", "/admin/{tail:.*}", proxy)
