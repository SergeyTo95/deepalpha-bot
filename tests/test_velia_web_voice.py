import asyncio
from types import SimpleNamespace
import pytest
import edge_tts
from aiohttp import web, ClientSession, ClientConnectionError, ClientResponseError
from aiohttp.test_utils import TestServer
from aiohttp import ClientPayloadError
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


def streaming_app():
    async def session_for(request):
        return SimpleNamespace(user_id=7) if request.headers.get('X-Test-Session') else None
    app=web.Application()
    routes.setup_voice_routes(app,session_for=session_for,
        same_origin=lambda r:r.headers.get('Origin')=='https://velia.example' and r.headers.get('X-Velia-Request')=='1',
        json_response=lambda data,status=200:web.json_response(data,status=status))
    return app

STREAM_HEADERS={'Origin':'https://velia.example','X-Velia-Request':'1','X-Test-Session':'yes'}
STREAM_DATA={'text':'Привет!','voice':'edge:en-US-AvaMultilingualNeural','rate':1}

def test_recognition_diagnostics_require_auth_origin_and_fixed_codes_without_speech(monkeypatch, caplog):
    async def scenario():
        async with TestServer(streaming_app()) as server, ClientSession() as client:
            endpoint = server.make_url('/web-api/v1/voice/events')
            data = {'event': 'recognition_error', 'code': 'network', 'attempt': 1}
            assert (await client.post(endpoint, json=data)).status == 403
            assert (await client.post(endpoint, json=data, headers={**STREAM_HEADERS, 'X-Test-Session': ''})).status == 401
            for bad in ({**data, 'transcript': 'private speech'}, {**data, 'code': 'private speech'},
                        {**data, 'attempt': True}, {**data, 'attempt': 4}, [], None):
                assert (await client.post(endpoint, json=bad, headers=STREAM_HEADERS)).status == 400
            for _ in range(20):
                response = await client.post(endpoint, json=data, headers=STREAM_HEADERS)
                assert response.status == 200 and (await response.json())['ok'] is True
            assert (await client.post(endpoint, json=data, headers=STREAM_HEADERS)).status == 429
    asyncio.run(scenario())
    assert 'VELIA_WEB_RECOGNITION_ERROR code=network attempt=1' in caplog.text
    assert 'private speech' not in caplog.text


def test_preparation_is_authenticated_bounded_and_reused_transport_keeps_exact_fields(monkeypatch):
    from desktop import voice_transport
    calls = []
    class Pool:
        async def start(self): calls.append(('start',))
        async def close(self): calls.append(('close',))
        def prepare(self, owner): calls.append(('prepare', owner))
        async def stream(self, fields, owner, fallback):
            calls.append(('stream', owner, fields)); yield b'pooled-original-voice'
    monkeypatch.setattr(voice_transport, 'EdgeSpeechPool', Pool)
    monkeypatch.setenv('VELIA_WEB_TTS_REUSE_ENABLED', 'true')
    monkeypatch.setenv('VELIA_WEB_TTS_PROVIDER', 'edge')
    async def scenario():
        async with TestServer(streaming_app()) as server, ClientSession() as client:
            prepare = server.make_url('/web-api/v1/voice/prepare')
            assert (await client.post(prepare, json={})).status == 403
            assert (await client.post(prepare, json={}, headers={**STREAM_HEADERS, 'X-Test-Session': ''})).status == 401
            for body in ({'text': 'do not synthesize'}, [], None, {'url': 'https://invalid.example'}):
                assert (await client.post(prepare, json=body, headers=STREAM_HEADERS)).status == 400
            for _ in range(3):
                assert (await client.post(prepare, json={}, headers=STREAM_HEADERS)).status == 200
            assert calls.count(('prepare', 7)) == 1
            assert not any(c[0] == 'stream' for c in calls)
            for suffix in ('', '?stream=1'):
                response = await client.post(server.make_url('/web-api/v1/voice/speech'+suffix),
                    json=STREAM_DATA, headers=STREAM_HEADERS)
                assert response.status == 200 and await response.read() == b'pooled-original-voice'
            assert all(c[1:] == (7, routes.validate(STREAM_DATA)) for c in calls if c[0] == 'stream')
    asyncio.run(scenario())
    assert calls[0] == ('start',) and calls[-1] == ('close',)


def test_voice_latency_accepts_only_bounded_timings_without_speech(monkeypatch,caplog):
    async def scenario():
        async with TestServer(streaming_app()) as server, ClientSession() as client:
            endpoint=server.make_url('/web-api/v1/voice/events')
            data={'event':'voice_latency','model':'flash','clock':'media',
                'recognition_ms':100,'first_text_ms':1500,'first_audio_ms':1800}
            with caplog.at_level('INFO',logger='desktop.voice_routes'):
                assert (await client.post(endpoint,json=data,headers=STREAM_HEADERS)).status==200
            for bad in ({**data,'text':'private speech'}, {**data,'first_text_ms':True},
                        {**data,'first_audio_ms':180001}, {**data,'first_text_ms':2000},
                        {**data,'clock':'private speech'}, {**data,'model':'private provider'}):
                assert (await client.post(endpoint,json=bad,headers=STREAM_HEADERS)).status==400
    asyncio.run(scenario())
    assert 'VELIA_WEB_VOICE_LATENCY model=flash clock=media recognition_ms=100 first_text_ms=1500 first_audio_ms=1800' in caplog.text
    assert 'private speech' not in caplog.text

def test_mp3_first_chunk_reaches_browser_before_provider_finishes(monkeypatch):
    monkeypatch.setenv('VELIA_WEB_TTS_PROVIDER','edge')
    async def run():
        release,finished=asyncio.Event(),asyncio.Event()
        class Speaker:
            def __init__(self,*a,**k):pass
            async def stream(self):
                yield {'type':'audio','data':b'first-mp3'}
                await release.wait()
                yield {'type':'audio','data':b'last-mp3'}
                finished.set()
        monkeypatch.setattr(edge_tts,'Communicate',Speaker)
        async with TestServer(streaming_app()) as server, ClientSession() as client:
            response=await client.post(server.make_url('/web-api/v1/voice/speech?stream=1'),headers=STREAM_HEADERS,json=STREAM_DATA)
            assert response.status==200 and response.headers['X-Velia-Audio-Stream']=='1'
            assert response.headers['Cache-Control']=='no-store' and response.headers['X-Accel-Buffering']=='no'
            assert await asyncio.wait_for(response.content.readexactly(9),1)==b'first-mp3'
            assert not finished.is_set()
            release.set();assert await response.read()==b'last-mp3';assert finished.is_set()
    asyncio.run(run())

def test_stream_auth_and_validation_reject_before_any_provider_call(monkeypatch):
    monkeypatch.setenv('VELIA_WEB_TTS_PROVIDER','edge')
    monkeypatch.setattr(edge_tts,'Communicate',lambda *a,**k:pytest.fail('rejected request called provider'))
    async def run():
        async with TestServer(streaming_app()) as server,ClientSession() as client:
            url=server.make_url('/web-api/v1/voice/speech?stream=1')
            assert (await client.post(url,json=STREAM_DATA)).status==403
            assert (await client.post(url,json=STREAM_DATA,headers={**STREAM_HEADERS,'X-Test-Session':''})).status==401
            assert (await client.post(url,json={**STREAM_DATA,'voice':'edge:other'},headers=STREAM_HEADERS)).status==400
    asyncio.run(run())

def test_disconnect_cancels_provider_and_releases_stream_slots(monkeypatch):
    monkeypatch.setenv('VELIA_WEB_TTS_PROVIDER','edge')
    async def run():
        closed=[]
        class Speaker:
            def __init__(self,*a,**k):self.closed=asyncio.Event();closed.append(self.closed)
            async def stream(self):
                try:
                    yield {'type':'audio','data':b'mp3'}
                    await asyncio.Event().wait()
                finally:self.closed.set()
        monkeypatch.setattr(edge_tts,'Communicate',Speaker)
        async with TestServer(streaming_app()) as server,ClientSession() as client:
            # More requests than the four available slots must still succeed.
            for _ in range(5):
                response=await client.post(server.make_url('/web-api/v1/voice/speech?stream=1'),headers=STREAM_HEADERS,json=STREAM_DATA)
                assert response.status==200
                assert await response.content.readexactly(3)==b'mp3'
                response.close()
                await asyncio.wait_for(closed[-1].wait(),1)
    asyncio.run(run())

def test_partial_audio_failure_is_not_retried_or_reported_as_complete(monkeypatch):
    monkeypatch.setenv('VELIA_WEB_TTS_PROVIDER','edge')
    async def run():
        release=asyncio.Event();calls=[]
        class Speaker:
            def __init__(self,*a,**k):calls.append(1)
            async def stream(self):
                yield {'type':'audio','data':b'mp3'}
                await release.wait()
                raise ClientConnectionError('private provider details')
        monkeypatch.setattr(edge_tts,'Communicate',Speaker)
        async with TestServer(streaming_app()) as server,ClientSession() as client:
            response=await client.post(server.make_url('/web-api/v1/voice/speech?stream=1'),headers=STREAM_HEADERS,json=STREAM_DATA)
            assert await response.content.readexactly(3)==b'mp3'
            release.set()
            with pytest.raises(ClientPayloadError):await response.read()
            assert calls==[1]
    asyncio.run(run())
