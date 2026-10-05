import asyncio
import gzip

from aiohttp import ClientSession, DummyCookieJar, web
from aiohttp.test_utils import TestServer

from desktop.admin_proxy import setup_owner_admin_proxy


def test_admin_proxy_keeps_owner_auth_on_core_and_gateway_origin():
    async def scenario():
        seen = []
        async def handler(request):
            seen.append((request.method, request.path_qs, request.headers.get("Cookie", ""), await request.read()))
            if request.path == "/admin/research":
                raise web.HTTPFound("/admin/login")
            if request.path == "/admin/login" and request.method == "POST":
                response = web.HTTPFound("/admin/research")
                response.set_cookie("velia_admin_session", "owner-session", path="/admin",
                                    httponly=True, samesite="Strict")
                return response
            if request.path == "/admin/research/dataset.jsonl":
                return web.Response(text='{"prompt":"x"}\n', content_type="application/x-ndjson",
                                    headers={"Content-Disposition": 'attachment; filename="velia-flash-train.jsonl"'})
            raise web.HTTPNotFound()

        core = web.Application()
        core.router.add_route("*", "/admin/{tail:.*}", handler)
        key = web.AppKey("admin_proxy_test_client", ClientSession)
        gateway = web.Application()
        async def client_lifecycle(app):
            async with ClientSession(cookie_jar=DummyCookieJar()) as client:
                app[key] = client
                yield
        gateway.cleanup_ctx.append(client_lifecycle)

        async with TestServer(core) as core_server:
            setup_owner_admin_proxy(gateway, upstream_origin=str(core_server.make_url("/")).rstrip("/"), client_key=key)
            async with TestServer(gateway) as gateway_server, ClientSession(cookie_jar=DummyCookieJar()) as client:
                async with client.get(gateway_server.make_url("/admin/research"), allow_redirects=False) as response:
                    assert response.status == 302
                    assert response.headers["Location"] == "/admin/login"
                async with client.post(gateway_server.make_url("/admin/login"),
                                       data={"code": "ABCD-EFGH-IJKL-MNOP"}, allow_redirects=False) as response:
                    assert response.status == 302
                    assert response.headers["Location"] == "/admin/research"
                    cookie = response.headers.getall("Set-Cookie")[0]
                    assert "velia_admin_session=owner-session" in cookie
                    assert "Path=/admin" in cookie
                    assert "Secure" in cookie
                async with client.get(gateway_server.make_url("/admin/research/dataset.jsonl"),
                                      headers={"Cookie": "velia_admin_session=owner-session"}) as response:
                    assert response.status == 200
                    assert response.headers["Content-Disposition"] == 'attachment; filename="velia-flash-train.jsonl"'
                    assert await response.text() == '{"prompt":"x"}\n'
                async with client.put(gateway_server.make_url("/admin/research"), data=b"x") as response:
                    assert response.status == 405
        assert any(path == "/admin/research/dataset.jsonl" and "owner-session" in cookie
                   for _, path, cookie, _ in seen)

    asyncio.run(scenario())


def test_admin_proxy_rejects_external_redirects():
    async def scenario():
        async def external(request):
            raise web.HTTPFound("https://example.com/not-core")
        core = web.Application()
        core.router.add_get("/admin/research", external)
        key = web.AppKey("admin_proxy_external_client", ClientSession)
        gateway = web.Application()
        async def lifecycle(app):
            async with ClientSession(cookie_jar=DummyCookieJar()) as client:
                app[key] = client
                yield
        gateway.cleanup_ctx.append(lifecycle)
        async with TestServer(core) as core_server:
            setup_owner_admin_proxy(gateway, upstream_origin=str(core_server.make_url("/")).rstrip("/"), client_key=key)
            async with TestServer(gateway) as gateway_server, ClientSession() as client:
                async with client.get(gateway_server.make_url("/admin/research"), allow_redirects=False) as response:
                    assert response.status == 502
                    assert "rejected" in await response.text()
    asyncio.run(scenario())


def test_admin_proxy_drops_content_encoding_after_upstream_auto_decompression():
    async def scenario():
        html = b"<!doctype html><html><body>VELIA Control Center</body></html>"

        async def compressed_login(request):
            return web.Response(
                body=gzip.compress(html),
                headers={
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Encoding": "gzip",
                    "Cache-Control": "no-store",
                },
            )

        core = web.Application()
        core.router.add_get("/admin/login", compressed_login)
        key = web.AppKey("admin_proxy_compression_client", ClientSession)
        gateway = web.Application()

        async def lifecycle(app):
            async with ClientSession(cookie_jar=DummyCookieJar()) as client:
                app[key] = client
                yield

        gateway.cleanup_ctx.append(lifecycle)
        async with TestServer(core) as core_server:
            setup_owner_admin_proxy(
                gateway,
                upstream_origin=str(core_server.make_url("/")).rstrip("/"),
                client_key=key,
            )
            async with TestServer(gateway) as gateway_server, ClientSession() as client:
                async with client.get(gateway_server.make_url("/admin/login")) as response:
                    assert response.status == 200
                    assert "Content-Encoding" not in response.headers
                    assert await response.read() == html

    asyncio.run(scenario())
