"""First-party player. Resolve permission before releasing game settings or exports."""
import asyncio
from html import escape
import json
import os
import re

from aiohttp import ClientError, ClientTimeout, web

from desktop.game_missions import saved_game_spec
from desktop.mission_schema import MissionError
from velia_desktop_routes import AuthenticationUnavailable

ASSET_ORIGIN = 'https://velia-game-preview-production.up.railway.app'
ASSETS = {
    'index.html': ('/', 'text/html', 30000),
    'game.mjs': ('/game.mjs', 'text/javascript', 30000),
    'style.css': ('/style.css', 'text/css', 30000),
    'phaser.min.js': ('/phaser.min.js', 'text/javascript', 2 * 1024 * 1024),
    'download': ('/download', 'application/zip', 2 * 1024 * 1024),
}
POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
          "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'")
HEADERS = {
    'Content-Security-Policy': POLICY,
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
    'X-Robots-Tag': 'noindex, nofollow',
    'Cache-Control': 'private, no-store',
}
POLL_ACCESS = """
let checking = false;
async function checkAccess() {
  if (checking) return;
  checking = true;
  try {
    const response = await fetch('./access', {
      credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(8000),
    });
    if (response.status !== 204) throw Error('Game access ended');
    document.body.hidden = false;
  } catch {
    document.body.replaceChildren();
    window.location.reload();
  } finally {
    checking = false;
  }
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) checkAccess();
});
window.addEventListener('pagehide', () => { document.body.hidden = true; });
window.addEventListener('pageshow', checkAccess);
setInterval(checkAccess, 10000);
"""
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def notice(title, *, detail='', status=404, retry=None):
    links = '<p><a href="/" target="_blank" rel="noopener">Открыть Велию</a></p>'
    if retry:
        links += f'<p><a href="{escape(retry, quote=True)}">Проверить доступ</a></p>'
    body = ('<!doctype html><html lang="ru"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<link rel="stylesheet" href="/game/style.css">'
            f'<title>{escape(title)}</title><main><h1>{escape(title)}</h1>{detail}{links}</main></html>')
    return web.Response(text=body, status=status, content_type='text/html', headers=HEADERS)


def setup_game_proxy(app, client_key, *, store, session_for):
    token = os.getenv('VELIA_GAME_PREVIEW_INTERNAL_TOKEN', '').strip()

    async def asset(name, spec=None):
        path, content_type, limit = ASSETS[name]
        params = {'spec': json.dumps(spec, ensure_ascii=False, separators=(',', ':'))} if spec is not None else None
        try:
            async with app[client_key].get(ASSET_ORIGIN + path, params=params,
                    headers={'X-Velia-Game-Internal': token}, allow_redirects=False,
                    timeout=ClientTimeout(total=15)) as upstream:
                if upstream.status != 200 or upstream.content_type != content_type:
                    raise web.HTTPServiceUnavailable(headers=HEADERS)
                body = bytearray()
                async for chunk in upstream.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > limit:
                        raise web.HTTPServiceUnavailable(headers=HEADERS)
                return bytes(body)
        except (ClientError, TimeoutError, OSError):
            raise web.HTTPServiceUnavailable(headers=HEADERS) from None

    async def landing(_request):
        return notice('Открой игру из Велии', status=200,
                      detail='<p>Свои игры можно открыть из карточек в чате. Для приглашения используй выданную ссылку.</p>')

    async def access_code(request):
        try:
            session = await session_for(request)
        except AuthenticationUnavailable:
            return notice('Вход временно недоступен', status=503)
        if not session:
            return notice('Войди, чтобы получить код', status=401, retry='/game/access-code')
        if store is None:
            return notice('Игры временно недоступны', status=503)
        code = store.game_recipient(session.user_id)
        return notice('Код для приглашений', status=200,
                      detail=f'<p>Передай этот код владельцу игры.</p><p><code>{code}</code></p>')

    async def static_asset(request):
        name = request.match_info['asset']
        body = await asset(name)
        return web.Response(body=body, content_type=ASSETS[name][1],
                            headers={**HEADERS, 'Cache-Control': 'public, max-age=300'})

    async def authorized_game(request):
        if store is None or request.query:
            return None
        match = request.match_info
        share_token = match.get('token')
        try:
            if share_token is None:
                session = await session_for(request)
                if not session:
                    return None
                data, _ = await asyncio.to_thread(store.read_game, session.user_id, match['mission'], match['step'])
                return data
            owner, mission, step, nonce = store.open_game_share(share_token)
            data, shares = await asyncio.to_thread(store.read_game, owner, mission, step)
            grant = shares.get(nonce)
            if not grant or grant['step'] != step:
                return None
            if grant['scope'] == 'public':
                return data
            if grant['scope'] == 'personal':
                session = await session_for(request)
                if session and grant['recipient'] == store.game_recipient(session.user_id):
                    return data
        except MissionError as exc:
            if exc.status != 404:
                raise web.HTTPServiceUnavailable(headers=HEADERS) from None
        except AuthenticationUnavailable:
            raise web.HTTPServiceUnavailable(headers=HEADERS) from None
        return None

    async def serve(request):
        name = request.match_info.get('asset', 'index.html')
        owner_view = 'token' not in request.match_info
        if name == 'download' and not owner_view:
            raise web.HTTPNotFound(headers=HEADERS)
        data = await authorized_game(request)
        if data is None:
            if name != 'index.html':
                raise web.HTTPNotFound(headers=HEADERS)
            return notice('Игра недоступна',
                          detail='<p>Если приглашение персональное, войди в нужный аккаунт и проверь доступ.</p>',
                          retry=request.path)
        if name == 'access':
            return web.Response(status=204, headers=HEADERS)
        if name == 'index.html':
            html = (await asset(name)).decode('utf-8')
            html = html.replace('./style.css', '/game/style.css').replace('./phaser.min.js', '/game/phaser.min.js')
            if not owner_view:
                html = re.sub(r'^.*id="game-download".*\n', '', html, flags=re.MULTILINE)
            body = html.encode('utf-8')
        elif name == 'game.mjs':
            spec = json.dumps(saved_game_spec(data['url']), ensure_ascii=True, separators=(',', ':'))
            source = (await asset(name)).decode('utf-8')
            source, count = re.subn(r'^const demo = .*?;$', lambda _: 'const demo = ' + spec + ';', source,
                                    flags=re.MULTILINE)
            if count != 1 or source.count('launch(loadSpec());') != 1:
                raise web.HTTPServiceUnavailable(headers=HEADERS)
            body = (source.replace('launch(loadSpec());', 'launch(demo);') + POLL_ACCESS).encode('utf-8')
        else:
            body = await asset(name, saved_game_spec(data['url']))
        headers = HEADERS
        if name == 'download':
            headers = {**HEADERS, 'Content-Disposition': 'attachment; filename="velia-game.zip"'}
        return web.Response(body=body, content_type=ASSETS[name][1], headers=headers,
                            charset='utf-8' if ASSETS[name][1].startswith('text/') else None)

    async def redirect(_request):
        raise web.HTTPPermanentRedirect('/game/')

    app.router.add_get('/game', redirect)
    app.router.add_get('/game/', landing)
    app.router.add_get('/game/access-code', access_code)
    app.router.add_get(r'/game/{asset:style\.css|phaser\.min\.js}', static_asset)
    private = '/game/project/{mission:' + UUID + '}/{step:s[1-8]}/'
    shared = r'/game/share/{token:[A-Za-z0-9_-]{150,350}}/'
    for prefix in (private, shared):
        app.router.add_get(prefix, serve)
        app.router.add_get(prefix + r'{asset:access|game\.mjs|download}', serve)
