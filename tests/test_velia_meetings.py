import asyncio
import io
import json
import shutil
from types import SimpleNamespace
import wave
import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from desktop import meeting_routes as meetings


def wav(seconds=1):
    out=io.BytesIO()
    with wave.open(out,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(b'\0\0'*8000*seconds)
    return out.getvalue()


def test_signatures_and_real_duration_probe():
    async def scenario():
        data=wav(2);meetings.signature(data,'audio/wav')
        if shutil.which('ffprobe'):assert await meetings.audio_duration(data,'audio/wav')==2
        for mime,data in [('audio/mpeg',b'ID3audio'),('audio/mpeg',b'\xff\xfbaudio'),('audio/flac',b'fLaCaudio'),('audio/mp4',b'\0\0\0\x18ftypM4A '),('audio/webm',b'\x1aE\xdf\xa3audio'),('audio/ogg',b'OggSaudio')]:meetings.signature(data,mime)
        for mime in meetings.FORMATS:
            with pytest.raises(ValueError):meetings.signature(b'<html>not audio</html>',mime)
    asyncio.run(scenario())


def test_audio_auth_origin_size_duration_language_and_provider_budget(monkeypatch,caplog):
    async def scenario():
        calls=[]
        async def session(request):return SimpleNamespace(user_id=int(request.headers['X-User'])) if request.headers.get('X-User') else None
        async def duration(data,mime):return 1201 if data.endswith(b'long') else 2
        async def transcribe(data,mime,language,**kwargs):calls.append((data,mime,language,kwargs));return 'Анна: пришлю отчёт.'
        monkeypatch.setattr(meetings,'audio_duration',duration);monkeypatch.setattr(meetings.stt,'transcribe',transcribe);monkeypatch.setattr(meetings.stt,'enabled',lambda:True)
        app=web.Application();meetings.setup_meeting_routes(app,session_for=session,same_origin=lambda r:r.headers.get('Origin')=='https://velia.example',json_response=lambda data,status=200:web.json_response(data,status=status,headers={'Cache-Control':'no-store'}))
        async with TestServer(app) as server,ClientSession() as client:
            async def post(data=b'ID3audio',mime='audio/mpeg',user='7',origin='https://velia.example',query=''):
                headers={'Content-Type':mime,'Origin':origin,**({'X-User':user} if user else {})}
                response=await client.post(server.make_url('/web-api/v1/meetings/transcribe'+query),data=data,headers=headers)
                return response.status,await response.json(),response.headers
            assert (await post(user=None))[0]==401
            assert (await post(origin='https://evil.example'))[0]==403
            assert (await post(mime='text/plain'))[0]==400
            assert (await post(query='?language=ru%0Asecret'))[0]==400
            assert (await post(data=b'bad audio'))[0]==400
            assert (await post(data=b'ID3'+b'x'*meetings.MAX_AUDIO))[0]==413
            assert (await post(data=b'ID3long'))[1]['error']=='meeting_audio_too_long'
            assert not calls
            status,result,headers=await post(query='?language=ru');assert status==200
            assert headers['Cache-Control']=='no-store' and result['speaker_identification'] is False and result['warning']=='transcription_requires_review'
            assert calls[0][2]=='ru' and calls[0][3]=={'timeout':90,'max_text':30000}
            assert (await post())[0]==200
            assert (await post())[0]==429
            assert (await post(user='8'))[0]==200
            monkeypatch.setattr(meetings.stt,'enabled',lambda:False)
            assert (await post(user='9'))[1]['error']=='meeting_transcription_unavailable'
        assert 'Анна' not in caplog.text and 'audio' not in caplog.text
    asyncio.run(scenario())


def test_cancellation_releases_meeting_slot_without_provider_retry(monkeypatch):
    async def scenario():
        entered=asyncio.Event();calls=[]
        async def transcribe(*args,**kwargs):calls.append(1);entered.set();await asyncio.Event().wait()
        monkeypatch.setattr(meetings.stt,'enabled',lambda:True);monkeypatch.setattr(meetings.stt,'transcribe',transcribe)
        monkeypatch.setattr(meetings,'audio_duration',lambda *a:asyncio.sleep(0,result=2))
        async def session(request):return SimpleNamespace(user_id=7)
        app=web.Application();meetings.setup_meeting_routes(app,session_for=session,same_origin=lambda r:True,json_response=lambda data,status=200:web.json_response(data,status=status))
        handler=next(route.handler for route in app.router.routes())
        class Content:
            async def iter_chunked(self,size):yield b'ID3audio'
        request=SimpleNamespace(query={},content_type='audio/mpeg',content=Content())
        task=asyncio.create_task(handler(request));await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        monkeypatch.setattr(meetings.stt,'transcribe',lambda *a,**kw:asyncio.sleep(0,result='да'))
        assert (await handler(request)).status==200 and len(calls)==1
    asyncio.run(scenario())


def test_meeting_planner_selected_models_and_static_assets(monkeypatch,tmp_path):
    from test_velia_web_chat import fixture,headers,login
    from desktop.guest_store import GuestStore
    async def scenario():
        async with fixture(monkeypatch,quantum_api=True,features={'chat_quantum':True},with_search=True,guest_store=GuestStore(sqlite_path=tmp_path/'quota.db')) as (server,client,state):
            url=server.make_url('/web-api/v1/meetings/plan')
            body={'model':'velia-flash','messages':[{'role':'user','content':'Подготовь протокол встречи'}],'stream':True,'web_search':False}
            assert (await client.post(url,headers=headers(),json=body)).status==401
            cookie,_,_=await login(server,client)
            for model in ['velia-flash','velia-quantum','velia-pro']:
                response=await client.post(url,headers=headers(cookie),json={**body,'model':model})
                assert response.status==200 and '[DONE]' in await response.text()
            assert len(state['payloads'])==3 and not state['search_queries'] and not state['intent_payloads']
            assert state['payloads'][0]['max_tokens']==1536
            for asset in ['meetings.mjs','meeting-ui.mjs']:
                assert (await client.get(server.make_url('/web/'+asset))).status==200
    asyncio.run(scenario())
