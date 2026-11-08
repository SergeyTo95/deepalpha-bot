import asyncio
import logging
import os
from aiohttp import web, ClientSession
from services import velia_pulse_service as pulse
from services import velia_telegram_config as telegram

logger=logging.getLogger(__name__)

def telegram_available():
    try:return telegram.enabled() and telegram.bot_username().lower()==telegram.configured_username().lower() and bool(telegram.dedicated_token()) and bool(telegram.web_url())
    except ValueError:return False

async def _deliver(session,notice):
    # A send attempt is claimed before network IO. Unknown results are not resent.
    from services.velia_telegram_runtime import Runtime,TelegramError
    from db.database import is_user_banned
    if not telegram_available():return 'failed'
    if await asyncio.to_thread(is_user_banned,notice['user_id']):return 'failed'
    text='Велия · Пульс\n'+(notice['title'] if notice['preview'] else 'Есть напоминание. Подробности и действия — в Велии.')
    url=telegram.web_url().split('#')[0].split('?')[0]+'?pulse='+notice['id']
    try:
        await Runtime(telegram.dedicated_token(),session).send(notice['user_id'],text,reply_markup={'inline_keyboard':[[{'text':'Открыть Пульс','url':url}]]})
        return 'sent'
    except TelegramError as error:return 'unknown' if not error.code else 'failed'

async def lifecycle(app):
    async def timers():
        while True:
            try:await asyncio.to_thread(pulse.tick)
            except asyncio.CancelledError:raise
            except Exception:logger.warning('VELIA_PULSE_TICK_UNAVAILABLE')
            await asyncio.sleep(15)
    async def deliveries(session):
        async def send(notice):
            try:
                result=await _deliver(session,notice)
                await asyncio.to_thread(pulse.delivery_result,notice['user_id'],notice['id'],result)
            except asyncio.CancelledError:raise
            except Exception:logger.warning('VELIA_PULSE_DELIVERY_UNCERTAIN')
        while True:
            notices=[]
            try:
                if telegram_available():
                    for _ in range(4):
                        notice=await asyncio.to_thread(pulse.claim_delivery)
                        if not notice:break
                        notices.append(notice)
                    await asyncio.gather(*(send(notice) for notice in notices))
            except asyncio.CancelledError:raise
            except Exception:logger.warning('VELIA_PULSE_OUTBOX_UNAVAILABLE')
            await asyncio.sleep(2 if notices else 15)
    tasks=[]
    async with ClientSession(trust_env=False) as session:
        if pulse.enabled() and os.getenv('RAILWAY_ENVIRONMENT_NAME','production').lower() in ('production','prod'):
            tasks=[asyncio.create_task(timers()),asyncio.create_task(deliveries(session))]
        try:yield
        finally:
            for task in tasks:task.cancel()
            if tasks:await asyncio.gather(*tasks,return_exceptions=True)

def setup_pulse_routes(app,module):
    slots=asyncio.Semaphore(8)
    async def handle(request):
        def reply(value,status=200):
            response=module._json_response(value,status=status);response.headers['Cache-Control']='no-store';return response
        if not module._mobile_api_available():return module._disabled_response()
        auth=module._require_mobile_auth(request)
        if not auth:return reply({'ok':False,'error':'unauthorized'},401)
        if slots.locked():return reply({'ok':False,'error':'pulse_busy'},429)
        try:
            async with slots:
                user=int(auth['user_id']);path=request.match_info.get('tail','');data={}
                if request.method!='GET':
                    if request.content_length and request.content_length>16384:raise pulse.PulseError('pulse_invalid',413)
                    data=await request.json()
                    if not isinstance(data,dict):raise pulse.PulseError('pulse_invalid')
                    data.pop('account',None)
                if path=='settings':
                    if data.get('telegram') and not telegram_available():raise pulse.PulseError('pulse_telegram_unavailable',409)
                    value=await asyncio.to_thread(pulse.settings,user,data if request.method=='PATCH' else None);result={'settings':value}
                elif path=='':
                    if request.method=='POST':result={'item':await asyncio.to_thread(pulse.create,user,data)}
                    else:result=await asyncio.to_thread(pulse.listing,user)
                elif path.endswith('/read'):
                    await asyncio.to_thread(pulse.read_notification,user,path.split('/')[1]);result={}
                elif request.method=='DELETE':await asyncio.to_thread(pulse.delete,user,path);result={}
                else:result={'item':await asyncio.to_thread(pulse.action,user,path,data)}
                return reply({'ok':True,**result,'telegram_available':telegram_available(),'telegram_username':telegram.configured_username()})
        except pulse.PulseError as error:return reply({'ok':False,'error':error.code},error.status)
        except (ValueError,TypeError):return reply({'ok':False,'error':'pulse_invalid'},400)
        except Exception:return reply({'ok':False,'error':'pulse_unavailable'},503)
    app.router.add_get('/mobile-api/v1/pulse',handle);app.router.add_post('/mobile-api/v1/pulse',handle)
    for method in ('GET','PATCH'):app.router.add_route(method,'/mobile-api/v1/pulse/{tail:settings}',handle)
    app.router.add_post('/mobile-api/v1/pulse/{tail:notifications/[a-f0-9-]{36}/read}',handle)
    for method in ('POST','DELETE'):app.router.add_route(method,'/mobile-api/v1/pulse/{tail:[a-f0-9-]{36}}',handle)
    app.cleanup_ctx.append(lifecycle)
