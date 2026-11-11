"""Session authenticated financial calculations and explicit snapshot persistence."""
import asyncio
import logging
import os
from aiohttp import web
from desktop.finance import FinanceError, analyze, fields, today, zone
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
            fields(data, (), ('snapshot', 'reserve_months', 'revision', 'confirmed', 'query', 'edit', 'time_zone', 'operation_id'))
            action = request.match_info['action']
            time_zone = data.get('time_zone', 'UTC')
            zone(time_zone)
            query = data.get('query')
            if query is not None and not isinstance(query, dict):
                raise FinanceError('finance_invalid_period')
            query = {**(query or {})}
            query.setdefault('time_zone', time_zone)
            if action == 'context':
                saved = await asyncio.to_thread(store.load, session.user_id) if store else {'revision': '', 'snapshot': None}
                snapshot = saved['snapshot']
                return json_response({'ok': True, 'configured': store is not None, 'revision': saved['revision'],
                    'today': today(time_zone).isoformat(), 'time_zone': time_zone,
                    'accounts': [{k: a[k] for k in ('id', 'project', 'currency')} for a in snapshot['accounts']] if snapshot else [],
                    'assets': [{**{k: a[k] for k in ('id', 'project', 'asset_type', 'name', 'symbol', 'unit')},
                                'identity': a.get('identity', {})}
                               for a in snapshot.get('assets', [])[:20]] if snapshot else [],
                    'asset_count': len(snapshot.get('assets', [])) if snapshot else 0,
                    'period_start': snapshot['period_start'] if snapshot else None,
                    'period_end': snapshot['period_end'] if snapshot else None,
                    'transaction_count': len(snapshot['transactions']) if snapshot else 0})
            if action == 'analyze' and data.get('snapshot') is not None:
                result = analyze(data['snapshot'], reserve_months=data.get('reserve_months', 6), query=query)
                return json_response({'ok': True, 'report': result, 'saved': False})
            if store is None:
                raise FinanceError('finance_storage_unavailable', 503)
            if action == 'load':
                result = await asyncio.to_thread(store.load, session.user_id)
            elif action == 'analyze':
                saved = await asyncio.to_thread(store.load, session.user_id)
                if saved['snapshot'] is None:
                    raise FinanceError('finance_snapshot_required', 409)
                result = {'report': analyze(saved['snapshot'], reserve_months=data.get('reserve_months', 6), query=query), 'saved': True}
            elif action in ('preview', 'mutate'):
                if not isinstance(data.get('revision'), str) or len(data['revision']) > 64:
                    raise FinanceError('finance_invalid_revision')
                # Validate query/reserve options before committing an accounting edit.
                snapshot, change = await asyncio.to_thread(store.preview_edit, session.user_id, data.get('edit'),
                    data['revision'], time_zone=time_zone, operation_id=data.get('operation_id'))
                report = analyze(snapshot, reserve_months=data.get('reserve_months', 6), query=query)
                result = {'change': change, 'report': report, 'revision': data['revision']}
                if action == 'mutate':
                    if data.get('confirmed') is not True:
                        raise FinanceError('finance_confirmation_required', 409)
                    saved, _ = await asyncio.to_thread(store.apply_edit, session.user_id, data.get('edit'),
                        data['revision'], time_zone=time_zone, operation_id=data.get('operation_id'))
                    result.update(saved)
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
    app.router.add_post('/web-api/v1/finance/{action:context|load|analyze|preview|mutate|save|delete}', handle)
