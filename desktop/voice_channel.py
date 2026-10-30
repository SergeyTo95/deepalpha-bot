"""One authenticated, bounded speech channel per answer."""
import asyncio
import struct
from contextlib import aclosing, suppress

from aiohttp import ClientError, WSMsgType, web
from velia_desktop_routes import AuthenticationUnavailable


def setup_voice_channel(app, *, origin, session_for, json_response, validate,
                        audio_source, reserve, slots, enabled):
    owners = {}

    async def channel(request):
        if (request.headers.get('Origin') != origin
                or request.headers.get('Sec-Fetch-Site', 'same-origin') != 'same-origin'
                or 'velia-voice-v1' not in request.headers.get('Sec-WebSocket-Protocol', '').split(', ')):
            return json_response({'error': 'invalid_origin'}, 403)
        try:
            session = await session_for(request)
        except (AuthenticationUnavailable, ClientError, OSError, TimeoutError):
            return json_response({'error': 'authentication_unavailable'}, 503)
        if not session:
            return json_response({'error': 'unauthorized'}, 401)
        if not enabled():
            return json_response({'error': 'tts_not_configured'}, 503)
        owner = session.user_id
        if sum(owners.values()) >= 16 or owners.get(owner, 0) >= 2:
            return json_response({'error': 'tts_rate_limit'}, 429)
        owners[owner] = owners.get(owner, 0) + 1
        socket = web.WebSocketResponse(protocols=('velia-voice-v1',), max_msg_size=8192,
            heartbeat=15, receive_timeout=30)
        jobs = {}
        # Synthesis runs ahead of playback, serially on the warm provider socket.
        # Opening a second provider connection would add a fresh handshake.
        admission = asyncio.Semaphore(1)
        last_id = 0

        async def synthesize(identifier, fields):
            started = False
            acquired = False
            try:
                async with admission:
                    async with asyncio.timeout(5):
                        await slots.acquire()
                        acquired = True
                    async with aclosing(audio_source(fields, owner)) as source:
                        size = 0
                        async for audio in source:
                            size += len(audio)
                            if size > 2 * 1024 * 1024:
                                raise RuntimeError('tts_unavailable')
                            if not started:
                                await socket.send_json({'type': 'start', 'id': identifier})
                                started = True
                            await socket.send_bytes(struct.pack('!I', identifier) + audio)
                        if not started:
                            raise RuntimeError('tts_unavailable')
                    await socket.send_json({'type': 'end', 'id': identifier})
            except asyncio.CancelledError:
                raise
            except (ClientError, RuntimeError, OSError, TimeoutError):
                if not socket.closed:
                    with suppress(ClientError, OSError, RuntimeError):
                        await socket.send_json({'type': 'error', 'id': identifier,
                            'code': 'tts_unavailable', 'partial': started})
            finally:
                if acquired:
                    slots.release()
                jobs.pop(identifier, None)

        try:
            await socket.prepare(request)
            async with asyncio.timeout(180):
                async for message in socket:
                    if message.type != WSMsgType.TEXT:
                        break
                    try:
                        data = message.json()
                        if not isinstance(data, dict):
                            raise ValueError()
                        identifier = data.get('id')
                        if type(identifier) is not int or not 1 <= identifier <= 1000000:
                            raise ValueError()
                        if data.get('type') == 'cancel' and set(data) == {'type', 'id'}:
                            task = jobs.pop(identifier, None)
                            if task:
                                task.cancel()
                            continue
                        if set(data) != {'type', 'id', 'text', 'voice', 'rate'} or data['type'] != 'speak' or identifier <= last_id:
                            raise ValueError()
                        fields = validate({key: data[key] for key in ('text', 'voice', 'rate')})
                        last_id = identifier
                        if len(jobs) >= 3 or not reserve(owner):
                            await socket.send_json({'type': 'error', 'id': identifier,
                                'code': 'tts_rate_limit', 'partial': False})
                            continue
                        jobs[identifier] = asyncio.create_task(synthesize(identifier, fields))
                    except (ValueError, TypeError, KeyError):
                        await socket.close(code=1008, message=b'invalid_speech_request')
                        break
        except (TimeoutError, ClientError, OSError):
            pass
        finally:
            pending = list(jobs.values())
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            owners[owner] -= 1
            if not owners[owner]:
                del owners[owner]
            await socket.close()
        return socket

    app.router.add_get('/web-api/v1/voice/stream', channel)
