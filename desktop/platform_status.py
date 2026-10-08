"""Read-only availability observations over existing account-scoped APIs."""
import asyncio
from datetime import datetime, timezone
from aiohttp import web
from velia_desktop_routes import AuthenticationUnavailable

DOMAINS = (
    ('plugins', 'plugins'), ('research', 'research/status'),
    ('media', 'studio/status'), ('tools', 'agent/status'),
    ('specialists', 'agents/status'), ('software', 'developer/autopilot/status'),
)


def readiness(domain, data):
    """Use declared prerequisites; never infer execution from HTTP success."""
    missing = []
    if domain == 'software':
        for field in ('worker_enabled', 'coding_enabled', 'write_enabled', 'worker_ready'):
            if data.get(field) is False:
                missing.append(field)
    elif domain == 'plugins':
        plugins = data.get('plugins')
        if isinstance(plugins, dict):
            for name in ('weather', 'web_search', 'research', 'image_generation', 'file_analyst', 'deepalpha_markets'):
                item = plugins.get(name)
                if isinstance(item, dict) and item.get('available') is False:
                    missing.append(name + '_unavailable')
    return missing


async def observe_platform(upstream, token, *, timeout=3):
    slots = asyncio.Semaphore(2)

    async def probe(domain, path):
        async with slots:
            missing = []
            try:
                status, data = await asyncio.wait_for(
                    upstream('GET', '/mobile-api/v1/' + path, token=token, data=None), timeout)
                if status in {401, 403}:
                    state = 'access_denied'
                elif status == 404:
                    state = 'not_exposed'
                elif status != 200 or not isinstance(data, dict) or data.get('ok') is not True:
                    state = 'unavailable'
                elif data.get('enabled') is False or data.get('available') is False:
                    state = 'disabled'
                else:
                    missing = readiness(domain, data)
                    state = 'partial' if missing else 'responding'
            except asyncio.TimeoutError:
                state = 'timeout'
            except Exception:
                # Never expose provider errors, credentials or account content.
                state = 'unavailable'
            return {'domain': domain, 'state': state, 'missing_prerequisites': missing, 'execution_verified': False}

    observations = await asyncio.gather(*(probe(*item) for item in DOMAINS))
    return {'ok': True, 'observed_at': datetime.now(timezone.utc).isoformat(),
            'observations': observations, 'task_execution_performed': False}


def setup_platform_status(app, *, session_for, upstream, json_response):
    async def status(request):
        if request.query:
            return json_response({'ok': False, 'error': 'invalid_request'}, 400)
        try:
            session = await session_for(request)
        except AuthenticationUnavailable:
            return json_response({'ok': False, 'error': 'feature_unavailable'}, 503)
        if not session:
            return json_response({'ok': False, 'error': 'unauthorized'}, 401)
        return json_response(await observe_platform(upstream, session.access), 200)

    app.router.add_get('/web-api/v1/platform/status', status)
