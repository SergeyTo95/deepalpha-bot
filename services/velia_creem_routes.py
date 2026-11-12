"""Authenticated checkout and signed raw webhook bridge in the account authority."""
import asyncio
import base64
import binascii
import json
import logging
import os
from aiohttp import web
from services.velia_creem_billing import BillingError, BillingService, BillingStore, CreemAPI, MAX_BODY

logger = logging.getLogger(__name__)


def setup_creem_routes(app, *, require_auth, api_available, billing=None):
    billing = billing or BillingService(BillingStore(), CreemAPI(os.getenv('VELIA_CREEM_API_KEY', '')))
    slots = asyncio.Semaphore(4)

    def response(data, status=200):
        return web.json_response(data, status=status, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    async def handle(request):
        action = request.match_info['action']
        try:
            if action == 'webhook':
                if request.method != 'POST':
                    return response({'ok': False, 'error': 'not_found'}, 404)
                # The gateway transports exact bytes as base64, without parsing
                # or reserializing signed JSON. No account/session is trusted here.
                data = await request.clone(client_max_size=MAX_BODY*2).json()
                if not isinstance(data, dict) or set(data) != {'raw_body','signature'}:
                    raise BillingError('invalid_webhook_envelope')
                if not isinstance(data['raw_body'], str) or len(data['raw_body']) > (MAX_BODY+2)//3*4:
                    raise BillingError('billing_body_too_large', 413)
                raw = base64.b64decode(data['raw_body'], validate=True)
                args = (raw, data['signature'], os.getenv('VELIA_CREEM_WEBHOOK_SECRET', ''))
                callback = billing.webhook
            else:
                if not api_available():
                    raise BillingError('velia_mobile_api_disabled', 503)
                auth = require_auth(request)
                if not auth:
                    raise BillingError('unauthorized', 401)
                user = int(auth['user_id'])
                if request.method == 'GET' and action == 'catalog':
                    callback, args = billing.catalog, ()
                elif request.method == 'GET' and action == 'account':
                    callback, args = billing.account, (user,)
                elif request.method == 'POST' and action in {'checkout','portal'}:
                    data = await request.clone(client_max_size=4096).json()
                    if not isinstance(data, dict) or set(data) != ({'plan','attempt'} if action == 'checkout' else set()):
                        raise BillingError('invalid_billing_request')
                    callback, args = (billing.checkout, (user, data['plan'], data['attempt'])) if action == 'checkout' else (billing.portal, (user,))
                else:
                    raise BillingError('not_found', 404)
            async with slots:
                result = await asyncio.to_thread(callback, *args)
            return response(result)
        except BillingError as error:
            return response({'ok': False, 'error': error.code}, error.status)
        except web.HTTPRequestEntityTooLarge:
            return response({'ok': False, 'error': 'billing_body_too_large'}, 413)
        except (ValueError, TypeError, binascii.Error, json.JSONDecodeError):
            return response({'ok': False, 'error': 'invalid_billing_request'}, 400)
        except Exception:
            # Driver/provider exception text and payloads can contain keys or PII.
            logger.warning('Creem billing temporarily unavailable')
            return response({'ok': False, 'error': 'billing_temporarily_unavailable'}, 503)

    app.router.add_route('*', '/mobile-api/v1/economy/creem/{action:catalog|account|checkout|portal|webhook}', handle)
    return billing
