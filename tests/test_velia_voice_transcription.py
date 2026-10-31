import asyncio
from types import SimpleNamespace
import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from desktop import voice_transcription as stt

@pytest.mark.parametrize('mime,data', [('audio/mp4',b'\0\0\0\x18ftypM4A '),('audio/webm;codecs=opus',b'\x1aE\xdf\xa3audio'),('audio/ogg',b'OggSaudio')])
def test_supported_audio_signatures(mime,data):
    assert stt.audio_type(data,mime)==mime.split(';')[0]

@pytest.mark.parametrize('mime,data',[('audio/mp4',b'not audio'),('audio/webm',b'OggS'),('text/plain',b'OggS'),('audio/mp4',b'ftyp')])
def test_invalid_audio_signatures(mime,data):
    with pytest.raises(ValueError):
        stt.audio_type(data,mime)

def test_free_account_guard(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','secret')
    for value in ['', 'false', 'paid']:
        monkeypatch.setenv('VELIA_VOICE_GROQ_FREE_ACCOUNT',value)
        assert not stt.enabled()
    monkeypatch.setenv('VELIA_VOICE_GROQ_FREE_ACCOUNT','true')
    assert stt.enabled()
    monkeypatch.delenv('GROQ_API_KEY')
    assert not stt.enabled()

def test_authenticated_bounded_transcription_rejects_origin_bad_audio_and_overload(monkeypatch,caplog):
    async def scenario():
        calls=[]
        async def transcribe(data,mime,language):
            calls.append((data,mime,language))
            return 'Нет, 125,5 рублей'
        monkeypatch.setattr(stt,'transcribe',transcribe)
        monkeypatch.setenv('GROQ_API_KEY','private key must not appear')
        monkeypatch.setenv('VELIA_VOICE_GROQ_FREE_ACCOUNT','true')
        async def session_for(request):
            return SimpleNamespace(user_id=7) if request.headers.get('X-Test-Session') else None
        app=web.Application(client_max_size=16*1024)
        stt.setup_transcription(app,session_for=session_for,
            same_origin=lambda r:r.headers.get('Origin')=='https://velia.example' and r.headers.get('X-Velia-Request')=='1',
            json_response=lambda d,status=200:web.json_response(d,status=status,headers={'Cache-Control':'no-store'}))
        async with TestServer(app) as server, ClientSession() as client:
            url=server.make_url('/web-api/v1/voice/transcribe?language=ru')
            headers={'Origin':'https://velia.example','X-Velia-Request':'1','X-Test-Session':'yes','Content-Type':'audio/mp4'}
            data=b'\0\0\0\x18ftypM4A '+b'audio'*5000
            assert (await client.post(url,data=data)).status==403
            assert (await client.post(url,data=data,headers={**headers,'X-Test-Session':''})).status==401
            monkeypatch.setenv('VELIA_VOICE_GROQ_FREE_ACCOUNT','false')
            assert (await client.post(url,data=data,headers=headers)).status==503
            assert not calls
            monkeypatch.setenv('VELIA_VOICE_GROQ_FREE_ACCOUNT','true')
            assert (await client.post(server.make_url('/web-api/v1/voice/transcribe?language=ru%0Ainjected'),data=data,headers=headers)).status==400
            assert (await client.post(url,data=data,headers={**headers,'Content-Type':'text/plain'})).status==400
            assert (await client.post(url,data=b'not audio',headers=headers)).status==400
            assert (await client.post(url,data=data+b'x'*stt.MAX_AUDIO,headers=headers)).status==413
            response=await client.post(url,data=data,headers=headers)
            assert response.status==200 and await response.json()=={'text':'Нет, 125,5 рублей'}
            assert response.headers['Cache-Control']=='no-store'
            assert calls==[(data,'audio/mp4','ru')]
            for _ in range(5):
                assert (await client.post(url,data=data,headers=headers)).status==200
            assert (await client.post(url,data=data,headers=headers)).status==429
        assert '125,5' not in caplog.text and 'private key' not in caplog.text
    asyncio.run(scenario())

def test_cancellation_releases_capacity_without_replaying_the_provider(monkeypatch):
    async def scenario():
        entered=asyncio.Event();calls=[]
        async def blocked(*args):
            calls.append(args);entered.set();await asyncio.Event().wait()
        monkeypatch.setattr(stt,'transcribe',blocked)
        monkeypatch.setattr(stt,'enabled',lambda:True)
        async def session_for(request):return SimpleNamespace(user_id=7)
        app=web.Application()
        stt.setup_transcription(app,session_for=session_for,same_origin=lambda r:True,
            json_response=lambda d,status=200:web.json_response(d,status=status))
        handler=next(route.handler for route in app.router.routes())
        class Content:
            async def iter_chunked(self,size):yield b'\0\0\0\x18ftypM4A '
        request=SimpleNamespace(query={'language':'ru'},content_type='audio/mp4',content=Content())
        task=asyncio.create_task(handler(request));await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        monkeypatch.setattr(stt,'transcribe',lambda *args:asyncio.sleep(0,result='да'))
        response=await handler(request)
        assert response.status==200 and len(calls)==1
    asyncio.run(scenario())

def test_provider_multipart_fixed_model_url_no_redirects_and_bounded_results(monkeypatch):
    async def scenario():
        seen=[]
        class Content:
            async def iter_chunked(self,size):yield b'{"text":"hello"}'
        class Response:
            status=200;content=Content()
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
        class Client:
            def __init__(self,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            def post(self,url,**kwargs):seen.append((url,kwargs));return Response()
        monkeypatch.setenv('GROQ_API_KEY','server-secret')
        monkeypatch.setattr(stt,'ClientSession',Client)
        assert await stt.transcribe(b'OggS','audio/ogg','tr')=='hello'
        url,request=seen[0]
        assert url=='https://api.groq.com/openai/v1/audio/transcriptions'
        assert request['allow_redirects'] is False
        fields={field[0]['name']:field[2] for field in request['data']._fields}
        assert fields['model']=='whisper-large-v3-turbo' and fields['language']=='tr'
        assert fields['file']==b'OggS' and 'url' not in fields
        Response.status=502
        with pytest.raises(RuntimeError):await stt.transcribe(b'OggS','audio/ogg','tr')
        Response.status=429
        with pytest.raises(stt.TranscriptionLimited):await stt.transcribe(b'OggS','audio/ogg','tr')
        Response.status=200
        async def too_large(self,size):yield b'x'*65537
        monkeypatch.setattr(Content,'iter_chunked',too_large)
        with pytest.raises(RuntimeError):await stt.transcribe(b'OggS','audio/ogg','tr')
    asyncio.run(scenario())
