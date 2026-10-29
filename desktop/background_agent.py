"""Private bridge from durable account jobs to the existing browser service."""
import hmac
import json
import os
import re
import time

from aiohttp import web

ID = re.compile(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z')


def setup_background_agent(app, run):
    seen = {}
    async def handle(request):
        key = os.getenv('VELIA_BACKGROUND_AGENT_KEY', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', key) or not hmac.compare_digest(
                request.headers.get('Authorization', '').encode('utf-8'), ('Bearer '+key).encode('utf-8')):
            return web.json_response({'ok': False, 'error': 'unauthorized'}, status=401)
        raw = bytearray()
        async for chunk in request.content.iter_chunked(8192):
            raw.extend(chunk)
            if len(raw) > 20000:
                return web.json_response({'ok': False, 'error': 'invalid_request'}, status=413)
        try:
            data = json.loads(raw)
            valid = (isinstance(data, dict) and set(data) == {'user_id','conversation_id','job_id','prompt'}
                and type(data['user_id']) is int and 0 < data['user_id'] < 10**19
                and all(isinstance(data[k], str) and ID.fullmatch(data[k]) for k in ('conversation_id','job_id'))
                and isinstance(data['prompt'], str) and 0 < len(data['prompt'].encode('utf-8')) <= 11000)
        except (ValueError, TypeError, UnicodeError):
            valid = False
        if not valid:
            return web.json_response({'ok': False, 'error': 'invalid_request'}, status=400)
        job = data['job_id']
        if job in seen:
            return web.json_response({'ok': False, 'error': 'background_agent_already_dispatched'}, status=409)
        if len(seen) >= 2048:
            for old, created in list(seen.items()):
                if time.monotonic()-created > 86400:
                    del seen[old]
            if len(seen) >= 2048:
                return web.json_response({'ok': False, 'error': 'background_agent_busy'}, status=429)
        seen[job] = time.monotonic()
        status, result = await run(data['user_id'], data['conversation_id'], data['prompt'])
        return web.json_response(result, status=status, headers={'Cache-Control':'no-store'})
    app.router.add_post('/internal/background-agent/run', handle)
