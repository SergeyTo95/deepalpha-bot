import asyncio
import struct
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, WSServerHandshakeError, WSMsgType, web
from aiohttp.test_utils import TestServer

from desktop.voice_channel import setup_voice_channel

HEADERS = {'Origin': 'https://velia.example', 'X-Test-Session': '7'}
FIELDS = {'type': 'speak', 'id': 1, 'text': 'Привет!', 'voice': 'same-voice', 'rate': 1}


def application(source=None, reserve=None):
    auth = []; seen = []; slots = asyncio.Semaphore(4)
    async def session_for(request):
        auth.append(True)
        value = request.headers.get('X-Test-Session')
        return SimpleNamespace(user_id=int(value)) if value else None
    def validate(data):
        if (set(data) != {'text', 'voice', 'rate'} or data['voice'] != 'same-voice'
                or not isinstance(data['text'], str) or not 1 <= len(data['text']) <= 500):
            raise ValueError()
        return data
    async def default_source(fields, owner):
        seen.append((fields, owner));yield fields['text'].encode()
    app = web.Application()
    setup_voice_channel(app, origin='https://velia.example', session_for=session_for,
        json_response=lambda data,status=200:web.json_response(data,status=status), validate=validate,
        audio_source=source or default_source, reserve=reserve or (lambda owner: True), slots=slots, enabled=lambda:True)
    return app, auth, seen, slots


def test_one_authentication_covers_multiple_ordered_segments_without_mixing_audio_ids():
    async def scenario():
        app,auth,seen,slots=application()
        async with TestServer(app) as server, ClientSession() as client:
            async with client.ws_connect(server.make_url('/web-api/v1/voice/stream'),
                    headers=HEADERS, protocols=('velia-voice-v1',)) as ws:
                await ws.send_json(FIELDS)
                await ws.send_json({**FIELDS,'id':2,'text':'Следующая фраза.'})
                audio={};finished=set()
                while len(finished)<2:
                    message=await ws.receive(timeout=2)
                    if message.type==WSMsgType.BINARY:
                        identifier=struct.unpack('!I',message.data[:4])[0]
                        audio[identifier]=audio.get(identifier,b'')+message.data[4:]
                    else:
                        data=message.json()
                        if data['type']=='end':finished.add(data['id'])
                assert audio=={1:'Привет!'.encode(),2:'Следующая фраза.'.encode()}
                assert len(auth)==1 and [owner for _,owner in seen]==[7,7]
        assert slots._value==4
    asyncio.run(scenario())


def test_cross_origin_and_unauthenticated_connections_are_rejected_before_synthesis():
    async def scenario():
        app,auth,seen,_=application()
        async with TestServer(app) as server, ClientSession() as client:
            for headers,status in (({**HEADERS,'Origin':'https://attacker.example'},403),
                                   ({'Origin':'https://velia.example'},401)):
                with pytest.raises(WSServerHandshakeError) as failure:
                    await client.ws_connect(server.make_url('/web-api/v1/voice/stream'),headers=headers,protocols=('velia-voice-v1',))
                assert failure.value.status==status
        assert seen==[] and len(auth)==1
    asyncio.run(scenario())


def test_cancel_and_disconnect_release_provider_tasks_and_synthesis_slots():
    async def scenario():
        ended=asyncio.Event()
        async def source(fields,owner):
            try:
                yield b'first-audio'
                await asyncio.Event().wait()
            finally:ended.set()
        app,_,_,slots=application(source=source)
        async with TestServer(app) as server, ClientSession() as client:
            async with client.ws_connect(server.make_url('/web-api/v1/voice/stream'),headers=HEADERS,protocols=('velia-voice-v1',)) as ws:
                await ws.send_json(FIELDS)
                assert (await ws.receive_json())['type']=='start'
                assert (await ws.receive()).type==WSMsgType.BINARY
                await ws.send_json({'type':'cancel','id':1})
                await asyncio.wait_for(ended.wait(),1)
        assert slots._value==4
    asyncio.run(scenario())


def test_rate_limit_and_invalid_payloads_do_not_call_provider():
    async def scenario():
        app,_,seen,_=application(reserve=lambda owner:False)
        async with TestServer(app) as server, ClientSession() as client:
            async with client.ws_connect(server.make_url('/web-api/v1/voice/stream'),headers=HEADERS,protocols=('velia-voice-v1',)) as ws:
                await ws.send_json(FIELDS)
                assert (await ws.receive_json())['code']=='tts_rate_limit'
                await ws.send_json({**FIELDS,'id':2,'user_id':99})
                message=await ws.receive()
                assert message.type==WSMsgType.CLOSE and ws.close_code==1008
        assert seen==[]
    asyncio.run(scenario())


def test_partial_provider_failure_has_an_explicit_error_and_never_reports_end():
    async def scenario():
        async def source(fields,owner):
            yield b'partial'
            raise RuntimeError('private provider details')
        app,_,_,slots=application(source=source)
        async with TestServer(app) as server, ClientSession() as client:
            async with client.ws_connect(server.make_url('/web-api/v1/voice/stream'),headers=HEADERS,protocols=('velia-voice-v1',)) as ws:
                await ws.send_json(FIELDS)
                assert (await ws.receive_json())['type']=='start'
                assert (await ws.receive()).type==WSMsgType.BINARY
                assert await ws.receive_json()=={'type':'error','id':1,'code':'tts_unavailable','partial':True}
        assert slots._value==4
    asyncio.run(scenario())
