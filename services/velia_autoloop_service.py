"""Account-authenticated Autoloop controls. No browser session is needed to tick."""
import asyncio
import logging
import os
import uuid

from velia_autoloop import LoopStore, ID, KEY, validate_rule
from velia_background_jobs import JobError

LOG = logging.getLogger(__name__)


def enabled():
    return os.getenv('VELIA_AUTOLOOP_ENABLED', 'true').lower() in {'1', 'true', 'yes', 'on'}


def authorize(user, config):
    from services import velia_background_chat_service as background
    background.allowed(user, config['model'])
    if not background.chat.get_conversation(user, config['conversation_id']):
        raise JobError('conversation_not_found', 404)
    if config.get('project_id'):
        from services import velia_developer_project_service as projects
        try:
            project = projects.get_project(user, config['project_id'])
        except projects.DeveloperProjectError:
            raise JobError('autoloop_project_required', 404) from None
        if project.get('archived'):
            raise JobError('autoloop_project_required', 409)
        if config.get('repository_full_name') and config['repository_full_name'] != project.get('repository_full_name'):
            raise JobError('autoloop_project_required', 409)
        return project
    return None


def question(cur, job, role, text):
    from services import velia_background_chat_service as background
    if not background.owned(cur, job['user_id'], job['conversation_id']):
        raise JobError('conversation_not_found', 404)
    hop = job['payload']['autoloop']['hops']
    mid = str(uuid.uuid5(uuid.UUID(job['id']), f'autoloop:{hop}:{role}'))
    cur.execute('''INSERT INTO velia_messages
        (message_id,conversation_id,user_id,role,content,status,request_id,provider,model,estimated_cost_usd,finish_reason)
        VALUES (%s,%s,%s,%s,%s,'completed',%s,%s,%s,0,'stop') ON CONFLICT(message_id) DO NOTHING''',
        (mid, job['conversation_id'], job['user_id'], role, text, job['id'], 'velia-'+job['model'], 'velia-'+job['model']))
    cur.execute('UPDATE velia_conversations SET updated_at=NOW() WHERE conversation_id=%s AND user_id=%s',
        (job['conversation_id'], job['user_id']))


def execute_coding(job, jobs):
    """The existing selected-model superagent alone plans/executes approved code."""
    from services import velia_superagent_chat_service as superagent
    from services import velia_background_chat_service as background
    from services import velia_developer_chat_runtime_patch as developer
    from services import velia_developer_coding_service as coding
    cfg = job['payload']['autoloop']
    project = authorize(job['user_id'], {**cfg, 'model': job['model'], 'conversation_id': job['conversation_id']})
    active = coding.active_job(job['user_id'], job['conversation_id'])
    if active and cfg['hops'] == 0:
        raise JobError('background_task_active', 409)
    if active and str(active['project_id']) != str(project['id']):
        raise JobError('autoloop_project_required', 409)
    if job['payload'].get('coding_inflight'):
        # A process may have died after a GitHub write. Never replay that turn.
        raise JobError('autoloop_execution_uncertain', 409)
    developer._bind_project(job['user_id'], job['conversation_id'], project['id'])
    if not jobs.checkpoint(job, {'coding_inflight': True}):
        raise JobError('background_task_cancelled', 409)
    result = superagent.route_free(background.chat, user_id=job['user_id'], conversation_id=job['conversation_id'],
        content=job['payload'].get('autoloop_answer') or job['payload']['content'], chat_mode=job['model'],
        request_id=str(uuid.uuid5(uuid.UUID(job['id']), f"coding-round:{cfg['hops']}")))
    if not result or not result.get('ok'):
        raise JobError('developer_superagent_unavailable', 503)
    active = coding.active_job(job['user_id'], job['conversation_id'])
    if active and active.get('status') == 'planned':
        jobs.wait(job, str(result['text'])[:12000])
        return None
    return result


def setup_routes(app, routes, jobs, loops, wake):
    from aiohttp import web

    async def handle(request):
        try:
            from services import velia_background_chat_service as background
            if not enabled() or not background.enabled() or not routes._mobile_api_available():
                raise JobError('autoloop_unavailable', 503)
            auth = routes._require_mobile_auth(request)
            if not auth:
                raise JobError('unauthorized', 401)
            user = int(auth['user_id'])
            rid = request.match_info.get('rule_id')
            if rid and not ID.fullmatch(rid):
                raise JobError('autoloop_rule_not_found', 404)
            if request.method == 'GET':
                items = await asyncio.to_thread(loops.listing, user)
                visible = []
                for item in items:
                    if await asyncio.to_thread(background.chat.get_conversation, user, item['conversation_id']):
                        visible.append(item)
                return routes._json_response({'ok': True, 'rules': visible})
            data = await routes._read_json(request)
            if not isinstance(data, dict):
                raise JobError('invalid_request')
            action = request.match_info.get('action')
            if not rid:
                cfg = validate_rule(data)
                project = await asyncio.to_thread(authorize, user, cfg)
                rule = await asyncio.to_thread(loops.create, user, data,
                    repository_full_name=project.get('repository_full_name') if project else None)
                def announce():
                    job = {'id': rule['id'], 'user_id': user, 'conversation_id': rule['conversation_id'],
                        'model': rule['model'], 'payload': {'autoloop': {'hops': 0}}}
                    cadence = f"каждые {rule['interval_minutes']} мин." if rule['interval_minutes'] else 'по событиям'
                    with jobs.transaction() as cur:
                        question(cur, job, 'user', f"Повторяющееся поручение ({cadence}): {rule['instruction']}")
                        question(cur, job, 'assistant', f"Поручение сохранено: {cadence}, до {rule['max_runs_per_day']} запусков в сутки. Управлять повторами можно в карточке. Вкладку можно закрыть.")
                await asyncio.to_thread(announce)
                return routes._json_response({'ok': True, 'rule': rule}, status=201)
            # All owner controls recheck the conversation, including after deletion.
            rule = next((r for r in await asyncio.to_thread(loops.listing, user) if r['id'] == rid), None)
            if not rule or not await asyncio.to_thread(background.chat.get_conversation, user, rule['conversation_id']):
                raise JobError('autoloop_rule_not_found', 404)
            if action in {'enable', 'disable'}:
                if data:
                    raise JobError('invalid_request')
                value = await asyncio.to_thread(loops.toggle, user, rid, action == 'enable')
                wake.set()
                return routes._json_response({'ok': True, 'rule': value})
            if action == 'events':
                value = await asyncio.to_thread(loops.emit, user, rid, data)
                wake.set()
                return routes._json_response({'ok': True, 'event': value}, status=202)
            if action == 'github-secret':
                if data:
                    raise JobError('invalid_request')
                value = await asyncio.to_thread(loops.webhook_secret, user, rid)
                return routes._json_response({'ok': True, 'secret': value})
            raise JobError('invalid_request')
        except JobError as exc:
            return routes._json_response({'ok': False, 'error': exc.code}, status=exc.status)
        except (ValueError, TypeError, UnicodeError):
            return routes._json_response({'ok': False, 'error': 'invalid_request'}, status=400)
        except Exception:
            LOG.exception('VELIA_AUTOLOOP_REQUEST_FAILED')
            return routes._json_response({'ok': False, 'error': 'autoloop_unavailable'}, status=503)

    async def github(request):
        try:
            from services import velia_background_chat_service as background
            if not enabled() or not background.enabled() or not routes._mobile_api_available():
                raise JobError('autoloop_unavailable', 503)
            raw = bytearray()
            async for chunk in request.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw) > 128000:
                    raise JobError('invalid_request', 413)
            value = await asyncio.to_thread(loops.github, request.match_info['rule_id'], bytes(raw),
                request.headers.get('X-Hub-Signature-256', ''), request.headers.get('X-GitHub-Delivery', ''), request.headers.get('X-GitHub-Event', ''))
            wake.set()
            return routes._json_response({'ok': True, 'event': value}, status=202)
        except JobError as exc:
            return routes._json_response({'ok': False, 'error': exc.code}, status=exc.status)
        except Exception:
            LOG.exception('VELIA_AUTOLOOP_WEBHOOK_FAILED')
            return routes._json_response({'ok': False, 'error': 'autoloop_unavailable'}, status=503)

    app.router.add_get('/mobile-api/v1/autoloop/rules', handle)
    app.router.add_post('/mobile-api/v1/autoloop/rules', handle)
    app.router.add_post('/mobile-api/v1/autoloop/rules/{rule_id}/{action}', handle)
    app.router.add_post('/mobile-api/v1/autoloop/github/{rule_id}', github)
