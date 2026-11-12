"""Bounded opaque relay; only the account authority verifies and fulfills billing."""
import asyncio
import base64
import re
from aiohttp import web
from velia_desktop_routes import AuthenticationUnavailable

PATH = '/web-api/v1/billing/creem/webhook'
MAX_BODY = 256 * 1024


def setup_creem_webhook_relay(app, *, upstream, json_response):
    async def receive(request):
        signature = request.headers.get('creem-signature', '')
        if not re.fullmatch('[a-fA-F0-9]{64}', signature):
            return json_response({'ok': False, 'error': 'invalid_signature'}, 401)
        if request.headers.get('Content-Encoding', 'identity').lower() != 'identity':
            return json_response({'ok': False, 'error': 'unsupported_encoding'}, 415)
        try:
            raw = await asyncio.wait_for(request.clone(client_max_size=MAX_BODY).read(), 10)
            status, result = await upstream('POST', '/mobile-api/v1/economy/creem/webhook',
                data={'raw_body': base64.b64encode(raw).decode('ascii'), 'signature': signature})
            # Never forward arbitrary account or provider responses from this public route.
            safe = {'ok': bool(status == 200 and result.get('ok'))}
            if not safe['ok']:
                safe['error'] = 'billing_delivery_not_processed'
            return json_response(safe, status)
        except web.HTTPRequestEntityTooLarge:
            return json_response({'ok': False, 'error': 'billing_body_too_large'}, 413)
        except (AuthenticationUnavailable, asyncio.TimeoutError):
            return json_response({'ok': False, 'error': 'billing_delivery_unavailable'}, 503)
    app.router.add_post(PATH, receive)
