"""Owner-admin reverse proxy for the isolated VELIA gateway.

The browser stays on the gateway origin while Velyon Core remains the authority
for owner authentication, CSRF checks, research jobs and downloadable datasets.
No Desktop/Web session is translated into an admin session.
"""
from http.cookies import CookieError, SimpleCookie
from urllib.parse import unquote, urlsplit

from aiohttp import ClientError, ClientTimeout, web

_ALLOWED_METHODS = {"GET", "HEAD", "POST"}
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}
_MAX_REQUEST_BODY = 256 * 1024
_ADMIN_SESSION = "velia_admin_session"
_ADMIN_COOKIES = (_ADMIN_SESSION, "velia_admin_csrf")


def _request_headers(request: web.Request) -> dict[str, str]:
    allowed = {
        "accept", "accept-language", "content-type", "referer",
        "user-agent", "x-request-id",
    }
    headers = {name: value for name, value in request.headers.items()
               if name.lower() in allowed}
    cookies = SimpleCookie({name: request.cookies[name] for name in _ADMIN_COOKIES if name in request.cookies})
    if cookies:
        headers["Cookie"] = "; ".join(item.OutputString() for item in cookies.values())
    return headers


def _redirect_location(value: str, upstream_origin: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        upstream = urlsplit(upstream_origin)
        if (parsed.scheme, parsed.netloc) != (upstream.scheme, upstream.netloc):
            raise ValueError("external_admin_redirect")
    path = unquote(parsed.path)
    if (path != "/admin" and not path.startswith("/admin/")) or (
        any(ord(char) < 32 or ord(char) == 127 or char == "\\" for char in path)
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        raise ValueError("invalid_admin_redirect")
    return parsed.path + (("?" + parsed.query) if parsed.query else "")


def _admin_response_cookie(value: str):
    cookies = SimpleCookie()
    try:
        cookies.load(value)
    except CookieError:
        return None
    if len(cookies) != 1:
        return None
    name, cookie = next(iter(cookies.items()))
    if name not in _ADMIN_COOKIES:
        return None
    cookie["path"] = "/admin"
    cookie["domain"] = ""
    cookie["secure"] = True
    cookie["samesite"] = "Strict"
    if name == _ADMIN_SESSION:
        cookie["httponly"] = True
    return cookie.OutputString()


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
                    # aiohttp auto-decompresses upstream response bodies by default.
                    # Never forward the original Content-Encoding for an already
                    # decompressed body or browsers can render a blank page.
                    if lower in _HOP_BY_HOP or lower in {
                        "content-length", "content-encoding", "set-cookie", "location",
                    }:
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
                    safe_cookie = _admin_response_cookie(cookie)
                    if safe_cookie:
                        response.headers.add("Set-Cookie", safe_cookie)
                response.headers["Cache-Control"] = "private, no-store"
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
