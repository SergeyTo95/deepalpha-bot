"""Serve the first-party game path through a fixed, credential-free asset origin."""
from aiohttp import ClientError, ClientTimeout, web

ASSET_ORIGIN = 'https://velia-game-preview-production.up.railway.app'
ASSETS = {
    'index.html': ('/', 'text/html', 30000),
    'game.mjs': ('/game.mjs', 'text/javascript', 30000),
    'style.css': ('/style.css', 'text/css', 30000),
    'phaser.min.js': ('/phaser.min.js', 'text/javascript', 2 * 1024 * 1024),
    'download': ('/download', 'application/zip', 2 * 1024 * 1024),
}
POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
          "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'")


def setup_game_proxy(app, client_key):
    async def redirect(_request):
        raise web.HTTPPermanentRedirect('/game/')

    async def serve(request):
        name = request.match_info.get('asset', 'index.html')
        if name not in ASSETS:
            raise web.HTTPNotFound()
        path, content_type, limit = ASSETS[name]
        if name == 'download':
            spec = request.query.get('spec')
            if spec is not None and len(spec.encode('utf-8')) > 1000:
                raise web.HTTPBadRequest()
            params = {'spec': spec} if spec is not None else None
        else:
            params = None

        try:
            async with app[client_key].request(request.method, ASSET_ORIGIN + path,
                    params=params, allow_redirects=False, timeout=ClientTimeout(total=15)) as upstream:
                if upstream.status == 400 and name == 'download':
                    raise web.HTTPBadRequest()
                if upstream.status != 200 or upstream.content_type != content_type:
                    raise web.HTTPServiceUnavailable()
                body = bytearray()
                async for chunk in upstream.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > limit:
                        raise web.HTTPServiceUnavailable()
        except (ClientError, TimeoutError, OSError):
            raise web.HTTPServiceUnavailable() from None

        headers = {
            'Content-Security-Policy': POLICY,
            'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'no-store' if name == 'download' else 'public, max-age=0, must-revalidate',
        }
        if name == 'download':
            headers['Content-Disposition'] = 'attachment; filename="velia-game.zip"'
        return web.Response(body=bytes(body), headers=headers, content_type=content_type,
                            charset='utf-8' if content_type.startswith('text/') else None)

    app.router.add_get('/game', redirect)
    app.router.add_get('/game/', serve)
    app.router.add_get('/game/{asset}', serve)
