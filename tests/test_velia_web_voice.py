import asyncio
from types import SimpleNamespace
import pytest
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from desktop import voice_routes as routes

@pytest.mark.parametrize('data', [None, {}, {'text':'','voice':'speechkit:jane','rate':1},
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
