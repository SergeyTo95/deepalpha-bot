"""Authenticated CRM reads and reviewed edits; no communication-provider writes."""
import asyncio
import logging
import os
from aiohttp import web
from desktop.crm import CrmError, initial, fields, local_today, query
from desktop.crm_store import CrmStore
from velia_desktop_routes import AuthenticationUnavailable

logger=logging.getLogger(__name__)


def setup_crm_routes(app,*,session_for,same_origin,json_response,store=None):
    dsn=os.getenv('VELIA_WEB_GUEST_DATABASE_URL')
    key=os.getenv('VELIA_WEB_SESSION_KEY')
    if store is None and dsn and key:store=CrmStore(dsn,session_key=key)
    async def lifecycle(application):
        if store:await asyncio.to_thread(store.initialize)
        yield
    app.cleanup_ctx.append(lifecycle)

    async def handle(request):
        if not same_origin(request):return json_response({'ok':False,'error':'invalid_origin'},403)
        try:
            session=await session_for(request)
            if not session:return json_response({'ok':False,'error':'unauthorized'},401)
            data=await request.clone(client_max_size=64*1024).json()
            fields(data,(),('query','edit','revision','operation_id','time_zone','confirmed'))
            time_zone=data.get('time_zone','UTC')
            today=local_today(time_zone).isoformat()
            action=request.match_info['action']
            if action=='context':
                current=await asyncio.to_thread(store.load,session.user_id) if store else {'revision':'','document':initial()}
                doc=current['document']
                return json_response({'ok':True,'configured':store is not None,'revision':current['revision'],'today':today,'time_zone':time_zone,
                    'counts':{name:len(doc[name]) for name in ('contacts','interactions','tasks','deals')},
                    'contacts':[{'id':c['id'],'name':c['name'],'company':c['company'],'roles':c['roles']} for c in doc['contacts'][:20]]})
            if store is None:raise CrmError('crm_storage_unavailable',503)
            if action=='query':
                current=await asyncio.to_thread(store.load,session.user_id)
                result=query(current['document'],data.get('query'),time_zone=time_zone)
                return json_response({'ok':True,'revision':current['revision'],'result':result})
            if not isinstance(data.get('revision'),str) or len(data['revision'])>64:raise CrmError('crm_invalid_revision')
            doc,change=await asyncio.to_thread(store.preview,session.user_id,data.get('edit'),data['revision'],data.get('operation_id'),time_zone=time_zone)
            # Query validation must happen before a commit so malformed display options cannot leave an ambiguous write.
            result=query(doc,data.get('query'),time_zone=time_zone)
            response={'revision':data['revision'],'change':change,'result':result}
            if action=='mutate':
                if data.get('confirmed') is not True:raise CrmError('crm_confirmation_required',409)
                saved,_=await asyncio.to_thread(store.commit,session.user_id,data.get('edit'),data['revision'],data.get('operation_id'),time_zone=time_zone)
                response.update(saved)
            return json_response({'ok':True,**response})
        except CrmError as exc:return json_response({'ok':False,'error':exc.code},exc.status)
        except AuthenticationUnavailable:return json_response({'ok':False,'error':'authentication_unavailable'},503)
        except web.HTTPRequestEntityTooLarge:return json_response({'ok':False,'error':'crm_request_too_large'},413)
        except (ValueError,TypeError,UnicodeDecodeError):return json_response({'ok':False,'error':'crm_invalid_data'},400)
        except Exception:
            logger.error('VELIA_CRM_STORAGE_FAILED')
            return json_response({'ok':False,'error':'crm_storage_unavailable'},503)
    app.router.add_post('/web-api/v1/crm/{action:context|query|preview|mutate}',handle)
