"""Authenticated connection management. OAuth completion is session-bound."""
import asyncio
from aiohttp import web
from services import velia_connection_catalog_service as catalog
from services import velia_notion_connection_service as notion


def setup_connection_routes(app,routes_module):
    prefix='/mobile-api/v1/agent/connectors'
    async def handle(request):
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth=routes_module._require_mobile_auth(request)
        if not auth:
            return routes_module._json_response({'ok':False,'error':'unauthorized'},status=401)
        user_id=int(auth['user_id']);action=request.match_info.get('action','')
        try:
            if request.path==prefix:
                value={'connections':await asyncio.to_thread(catalog.list_connections,user_id)}
            elif action=='connect' and request.method=='POST':
                value=await asyncio.to_thread(notion.create_authorization_url,user_id)
            elif action=='complete' and request.method=='POST':
                data=await request.json()
                if not isinstance(data,dict) or set(data)-{'state','code'}:
                    raise notion.ConnectionError('invalid_request')
                value=await asyncio.to_thread(notion.complete_connection,user_id,data.get('state'),data.get('code'))
            elif request.method=='DELETE':
                value=await asyncio.to_thread(notion.disconnect,user_id)
            else:
                raise notion.ConnectionError('not_found',404)
            return routes_module._json_response({'ok':True,**value})
        except notion.ConnectionError as error:
            return routes_module._json_response({'ok':False,'error':error.code},status=error.status)
        except (ValueError,TypeError):
            return routes_module._json_response({'ok':False,'error':'invalid_request'},status=400)
        except Exception:
            return routes_module._json_response({'ok':False,'error':'connection_unavailable'},status=503)
    app.router.add_get(prefix,handle)
    app.router.add_post(prefix+'/notion/{action:connect|complete}',handle)
    app.router.add_delete(prefix+'/notion',handle)
