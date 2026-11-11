"""Session authenticated financial calculations and explicit snapshot persistence."""
import asyncio
import logging
import os
from aiohttp import web
from desktop.finance import FinanceError, analyze, fields
from desktop.finance_store import FinanceStore
from velia_desktop_routes import AuthenticationUnavailable

logger = logging.getLogger(__name__)


def setup_finance_routes(app, *, session_for, same_origin, json_response, store=None):
    if store is None:
        dsn = os.getenv('VELIA_WEB_GUEST_DATABASE_URL')
        secret = os.getenv('VELIA_WEB_SESSION_KEY')
        if dsn and secret:
            store = FinanceStore(dsn, session_key=secret)
    async def lifecycle(application):
        if store:
            await asyncio.to_thread(store.initialize)
        yield
    app.cleanup_ctx.append(lifecycle)

    async def handle(request):
        if not same_origin(request):
            return json_response({'ok': False, 'error': 'invalid_origin'}, 403)
        try:
            session = await session_for(request)
            if not session:
                return json_response({'ok': False, 'error': 'unauthorized'}, 401)
            data = await request.clone(client_max_size=64*1024).json()
            fields(data, (), ('snapshot', 'reserve_months', 'revision', 'confirmed'))
            action = request.match_info['action']
            if action == 'analyze' and data.get('snapshot') is not None:
                result = analyze(data['snapshot'], reserve_months=data.get('reserve_months', 6))
                return json_response({'ok': True, 'report': result, 'saved': False})
            if store is None:
                raise FinanceError('finance_storage_unavailable', 503)
            if action == 'load':
                result = await asyncio.to_thread(store.load, session.user_id)
            elif action == 'analyze':
                saved = await asyncio.to_thread(store.load, session.user_id)
                if saved['snapshot'] is None:
                    raise FinanceError('finance_snapshot_required', 409)
                result = {'report': analyze(saved['snapshot'], reserve_months=data.get('reserve_months', 6)), 'saved': True}
            elif action in ('save', 'delete'):
                if data.get('confirmed') is not True:
                    raise FinanceError('finance_confirmation_required', 409)
                if not isinstance(data.get('revision'), str) or len(data['revision']) > 64:
                    raise FinanceError('finance_invalid_revision')
                if action == 'save':
                    result = await asyncio.to_thread(store.save, session.user_id, data.get('snapshot'), data['revision'])
                else:
                    await asyncio.to_thread(store.delete, session.user_id, data['revision'])
                    result = {'deleted': True}
            return json_response({'ok': True, **result})
        except FinanceError as exc:
            return json_response({'ok': False, 'error': exc.code}, exc.status)
        except web.HTTPRequestEntityTooLarge:
            return json_response({'ok': False, 'error': 'finance_request_too_large'}, 413)
        except AuthenticationUnavailable:
            return json_response({'ok': False, 'error': 'authentication_unavailable'}, 503)
        except (ValueError, TypeError, UnicodeDecodeError):
            return json_response({'ok': False, 'error': 'finance_invalid_data'}, 400)
        except Exception:
            # Do not log account contents or exception text containing financial values.
            logger.error('VELIA_FINANCE_STORAGE_FAILED')
            return json_response({'ok': False, 'error': 'finance_storage_unavailable'}, 503)
    app.router.add_post('/web-api/v1/finance/{action:load|analyze|save|delete}', handle)
