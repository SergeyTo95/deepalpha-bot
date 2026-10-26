import asyncio
from types import SimpleNamespace
import pytest
import edge_tts
from aiohttp import web, ClientSession, ClientConnectionError, ClientResponseError
from aiohttp.test_utils import TestServer
from desktop import voice_routes as routes

@pytest.mark.parametrize('data', [None, {}, {'text':'','voice':'speechkit:jane','rate':1},
    {'text':'... —','voice':'speechkit:jane','rate':1},
    {'text':'hello','voice':'speechkit:unknown','rate':1},
    {'text':'hello','voice':'speechkit:jane','rate':True},
    {'text':'hello','voice':'speechkit:jane','rate':float('nan')},
    {'text':'x'*501,'voice':'speechkit:jane','rate':1}])
def test_invalid_speech(data):
    with pytest.raises((ValueError, TypeError)):
        routes.validate(data)

def test_voice_routes_auth_config_cancellation_and_errors(monkeypatch):
    async def scenario():
        calls=[]
        async def provider(fields):
            calls.append(fields)
            if fields['text']=='fail':
                raise RuntimeError('provider secret must not leak')
            return b'fixture-mp3'
        monkeypatch.setattr(routes, 'synthesize', provider)
        monkeypatch.delenv('VELIA_WEB_TTS_PROVIDER', raising=False)
        monkeypatch.setenv('VELIA_SPEECHKIT_API_KEY', 'server-only')
        async def session_for(request):
            return SimpleNamespace(user_id=7) if request.headers.get('X-Test-Session') else None
        app=web.Application()
        routes.setup_voice_routes(app, session_for=session_for,
            same_origin=lambda r:r.headers.get('Origin')=='https://velia.example' and r.headers.get('X-Velia-Request')=='1',
            json_response=lambda d,status=200:web.json_response(d,status=status))
        async with TestServer(app) as server, ClientSession() as client:
            catalog=server.make_url('/web-api/v1/voice/voices');speech=server.make_url('/web-api/v1/voice/speech')
            headers={'Origin':'https://velia.example','X-Velia-Request':'1','X-Test-Session':'yes'}
            data={'text':'Привет!','voice':'speechkit:jane','rate':1}
            assert (await client.get(catalog)).status==401
            assert (await (await client.get(catalog,headers=headers)).json())=={'voices':[]}
            assert (await client.post(speech,json=data)).status==403
            assert (await client.post(speech,json=data,headers={**headers,'X-Test-Session':''})).status==401
            assert (await client.post(speech,json=data,headers=headers)).status==503
            assert not calls
            monkeypatch.setenv('VELIA_WEB_TTS_PROVIDER','speechkit')
            assert len((await (await client.get(catalog,headers=headers)).json())['voices'])==3
            response=await client.post(speech,json=data,headers=headers)
            assert response.status==200 and await response.read()==b'fixture-mp3'
            assert response.headers['Cache-Control']=='no-store'
            assert calls==[{'text':'Привет!','voice':'jane','speed':'1','lang':'ru-RU','format':'mp3'}]
            assert (await client.post(speech,json={**data,'voice':'speechkit:other'},headers=headers)).status==400
            response=await client.post(speech,json={**data,'text':'fail'},headers=headers)
            assert response.status==503 and 'secret' not in await response.text()
            for _ in range(38):
                assert (await client.post(speech,json=data,headers=headers)).status==200
            assert (await client.post(speech,json=data,headers=headers)).status==429
    asyncio.run(scenario())

def test_neural_catalog_has_three_distinct_female_speakers_and_no_paid_route(monkeypatch):
    monkeypatch.setenv('VELIA_WEB_TTS_PROVIDER','edge')
    monkeypatch.delenv('VELIA_SPEECHKIT_API_KEY',raising=False)
    catalog=routes.voice_catalog()
    assert len({v['voiceURI'] for v in catalog})==3
    assert all(v['gender']=='female' for v in catalog)
    seen=[]
    class Speaker:
        def __init__(self,text,voice,**kwargs):seen.append((text,voice,kwargs))
        async def stream(self):
            yield {'type':'WordBoundary','text':'ignored'}
            yield {'type':'audio','data':seen[-1][1].encode()}
    monkeypatch.setattr(edge_tts,'Communicate',Speaker)
    async def run():
        for voice in catalog:
            fields=routes.validate({'text':'Привет!','voice':voice['voiceURI'],'rate':1.2})
            assert await routes.synthesize(fields)==voice['voiceURI'].split(':',1)[1].encode()
        assert len({v[1] for v in seen})==3
        assert all(v[2]['rate']=='+20%' for v in seen)
    asyncio.run(run())
    for voice in ['edge:ru-RU-DmitryNeural','edge:ru-RU-DariyaNeural','speechkit:jane','https://evil.example/voice']:
        with pytest.raises(ValueError):routes.validate({'text':'Привет','voice':voice,'rate':1})

@pytest.mark.parametrize('kind',['empty','large','failure'])
def test_neural_empty_oversized_and_provider_errors_are_safe(monkeypatch,kind):
    monkeypatch.setenv('VELIA_WEB_TTS_PROVIDER','edge')
    class Speaker:
        def __init__(self,*args,**kwargs):pass
        async def stream(self):
            if kind=='failure':raise Exception('provider secret must never leak')
            if kind=='large':yield {'type':'audio','data':b'x'*(2*1024*1024+1)}
            yield {'type':'WordBoundary'}
    monkeypatch.setattr(edge_tts,'Communicate',Speaker)
    with pytest.raises(RuntimeError,match='^tts_unavailable$'):
        asyncio.run(routes.synthesize(routes.validate({'text':'Привет','voice':routes.EDGE_VOICES[0]['voiceURI'],'rate':1})))

def test_empty_audio_retries_once_with_same_voice_without_switching_or_replaying_partial_audio(monkeypatch):
    monkeypatch.setenv('VELIA_WEB_TTS_PROVIDER','edge')
    calls=[]
    class Speaker:
        def __init__(self,text,voice,**kwargs):calls.append((text,voice,kwargs))
        async def stream(self):
            if len(calls)==1:raise edge_tts.exceptions.NoAudioReceived('private provider details')
            yield {'type':'audio','data':b'recovered-audio'}
    monkeypatch.setattr(edge_tts,'Communicate',Speaker)
    data=routes.validate({'text':'Привет','voice':routes.EDGE_VOICES[2]['voiceURI'],'rate':1})
    assert asyncio.run(routes.synthesize(data))==b'recovered-audio'
    assert len(calls)==2 and calls[0]==calls[1]
    calls.clear()
    class Partial(Speaker):
        async def stream(self):
            yield {'type':'audio','data':b'partial'}
            raise edge_tts.exceptions.NoAudioReceived('private provider details')
    monkeypatch.setattr(edge_tts,'Communicate',Partial)
    with pytest.raises(RuntimeError,match='^tts_unavailable$'):asyncio.run(routes.synthesize(data))
    assert len(calls)==1

@pytest.mark.parametrize('error',[
    ClientConnectionError('private network details'),
    asyncio.TimeoutError(),
    edge_tts.exceptions.WebSocketError('private websocket details'),
    ClientResponseError(None,(),status=503,message='private upstream details'),
])
def test_transient_edge_errors_retry_once_before_audio_with_same_speaker(monkeypatch,error):
    monkeypatch.setenv('VELIA_WEB_TTS_PROVIDER','edge')
    calls=[]
    class Speaker:
        def __init__(self,*args,**kwargs):calls.append((args,kwargs))
        async def stream(self):
            if len(calls)==1:raise error
            yield {'type':'audio','data':b'recovered-audio'}
    monkeypatch.setattr(edge_tts,'Communicate',Speaker)
    fields=routes.validate({'text':'Привет','voice':routes.EDGE_VOICES[2]['voiceURI'],'rate':1})
    assert asyncio.run(routes.synthesize(fields))==b'recovered-audio'
    assert len(calls)==2 and calls[0]==calls[1]
    calls.clear()
    class Unavailable(Speaker):
        async def stream(self):
            raise error
            yield
    monkeypatch.setattr(edge_tts,'Communicate',Unavailable)
    with pytest.raises(RuntimeError,match='^tts_unavailable$'):asyncio.run(routes.synthesize(fields))
    assert len(calls)==2
    calls.clear()
    class Partial(Speaker):
        async def stream(self):
            yield {'type':'audio','data':b'partial'}
            raise error
    monkeypatch.setattr(edge_tts,'Communicate',Partial)
    with pytest.raises(RuntimeError,match='^tts_unavailable$'):asyncio.run(routes.synthesize(fields))
    assert len(calls)==1

@pytest.mark.parametrize('status',[400,401,403,429])
def test_permanent_or_throttled_edge_errors_do_not_retry(monkeypatch,status):
    monkeypatch.setenv('VELIA_WEB_TTS_PROVIDER','edge')
    calls=[]
    class Speaker:
        def __init__(self,*args,**kwargs):calls.append(1)
        async def stream(self):
            raise ClientResponseError(None,(),status=status,message='private upstream details')
            yield
    monkeypatch.setattr(edge_tts,'Communicate',Speaker)
    fields=routes.validate({'text':'Привет','voice':routes.EDGE_VOICES[2]['voiceURI'],'rate':1})
    with pytest.raises(RuntimeError,match='^tts_unavailable$'):asyncio.run(routes.synthesize(fields))
    assert calls==[1]
