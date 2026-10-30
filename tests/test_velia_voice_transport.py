import asyncio
from contextlib import aclosing
from types import SimpleNamespace

import pytest
from aiohttp import WSMsgType
from edge_tts import Communicate
from edge_tts.communicate import mkssml

from desktop.voice_transport import EdgeSpeechPool


FIELDS = {"text": "Привет & проверка <голоса>!", "edge_voice": "ru-RU-SvetlanaNeural", "edge_rate": "+0%"}


class Socket:
    def __init__(self, *, failure=None, gate=None):
        self.closed = False
        self.failure, self.gate = failure, gate
        self.frames, self.sent, self.ids = [], [], []

    async def close(self):
        self.closed = True

    async def send_str(self, text):
        self.sent.append(text)
        if "Path:ssml\r\n" not in text:
            return
        request_id = text.split("X-RequestId:", 1)[1].split("\r\n", 1)[0]
        self.ids.append(request_id)
        if self.failure == "closed":
            self.frames.append(SimpleNamespace(type=WSMsgType.CLOSED, data=None))
            return
        identifier = "stale-request" if self.failure == "mismatch" else request_id
        self.frames.append(SimpleNamespace(type=WSMsgType.TEXT,
            data=f"X-RequestId:{identifier}\r\nPath:turn.start\r\n\r\n{{}}"))
        headers = f"X-RequestId:{identifier}\r\nContent-Type:audio/mpeg\r\nPath:audio\r\n".encode()
        data = len(headers).to_bytes(2, 'big') + headers + b"fixture-mp3"
        self.frames.append(SimpleNamespace(type=WSMsgType.BINARY, data=data))
        if self.failure == "partial":
            self.frames.append(SimpleNamespace(type=WSMsgType.CLOSED, data=None))
        else:
            self.frames.append(SimpleNamespace(type=WSMsgType.TEXT,
                data=f"X-RequestId:{identifier}\r\nPath:turn.end\r\n\r\n{{}}"))

    async def receive(self):
        if self.gate:
            await self.gate.wait()
        return self.frames.pop(0)


async def no_fallback():
    pytest.fail("unexpected fallback")
    yield b""


async def collect(pool, owner=7, fields=FIELDS, fallback=no_fallback):
    async with aclosing(pool.stream(fields, owner, fallback)) as source:
        return b"".join([part async for part in source])


def test_warm_connection_is_reused_for_two_exact_voice_turns_without_synthesizing_a_dummy():
    async def scenario():
        opened = []
        async def connect():
            sock = Socket(); opened.append(sock); return sock
        pool = EdgeSpeechPool(connect=connect)
        try:
            pool.prepare(7)
            await pool.warming[7]
            assert len(opened) == 1 and opened[0].sent == []
            for _ in range(2):
                assert await collect(pool) == b"fixture-mp3"
            assert len(opened) == 1 and len(set(opened[0].ids)) == 2
            reference = Communicate(FIELDS['text'], FIELDS['edge_voice'], rate=FIELDS['edge_rate'])
            ssml = mkssml(reference.tts_config, list(reference.texts)[0])
            assert all(frame.endswith(ssml) for frame in opened[0].sent if 'Path:ssml\r\n' in frame)
            assert all('audio-24khz-48kbitrate-mono-mp3' in frame for frame in opened[0].sent if 'speech.config' in frame)
        finally:
            await pool.close()
        assert all(sock.closed for sock in opened)
    asyncio.run(scenario())


def test_different_accounts_and_parallel_segments_never_share_an_active_socket():
    async def scenario():
        opened = []; gate = asyncio.Event()
        async def connect():
            sock = Socket(gate=gate); opened.append(sock); return sock
        pool = EdgeSpeechPool(connect=connect)
        try:
            tasks = [asyncio.create_task(collect(pool, owner)) for owner in (7, 7, 8)]
            while len(opened) < 3:
                await asyncio.sleep(0)
            assert len(pool.connections) == 3
            gate.set()
            assert await asyncio.gather(*tasks) == [b'fixture-mp3'] * 3
            assert sorted(c.owner for c in pool.connections) == [7, 7, 8]
        finally:
            await pool.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['closed', 'mismatch'])
def test_stale_or_wrong_request_frames_fall_back_before_audio_and_discard_socket(failure):
    async def scenario():
        sock = Socket(failure=failure); fallbacks = []
        async def connect(): return sock
        async def fallback():
            fallbacks.append(True); yield b'original-voice'
        pool = EdgeSpeechPool(connect=connect)
        try:
            assert await collect(pool, fallback=fallback) == b'original-voice'
            assert fallbacks == [True] and sock.closed and pool.connections == []
        finally:
            await pool.close()
    asyncio.run(scenario())


def test_partial_audio_is_never_replayed_or_returned_to_the_pool():
    async def scenario():
        sock = Socket(failure='partial')
        async def connect(): return sock
        pool = EdgeSpeechPool(connect=connect)
        try:
            async with aclosing(pool.stream(FIELDS, 7, no_fallback)) as source:
                assert await anext(source) == b'fixture-mp3'
                with pytest.raises(RuntimeError, match='tts_unavailable'):
                    await anext(source)
            assert sock.closed and pool.connections == []
        finally:
            await pool.close()
    asyncio.run(scenario())


def test_disconnect_destroys_inflight_socket_and_idle_expiry_frees_warmed_sockets():
    async def scenario():
        opened = []
        async def connect():
            sock = Socket(); opened.append(sock); return sock
        pool = EdgeSpeechPool(connect=connect, idle_seconds=.01)
        try:
            source = pool.stream(FIELDS, 7, no_fallback)
            assert await anext(source) == b'fixture-mp3'
            await source.aclose()
            assert opened[0].closed and pool.connections == []
            pool.prepare(7); await pool.warming[7]
            await asyncio.sleep(.03)
            assert opened[1].closed and pool.connections == []
        finally:
            await pool.close()
    asyncio.run(scenario())


def test_bounded_pool_replaces_idle_owners_but_does_not_exceed_capacity():
    async def scenario():
        opened = []
        async def connect():
            sock = Socket(); opened.append(sock); return sock
        pool = EdgeSpeechPool(connect=connect, limit=1)
        try:
            await collect(pool, 7)
            await collect(pool, 8)
            assert opened[0].closed
            assert len(pool.connections) == 1 and pool.connections[0].owner == 8
        finally:
            await pool.close()
    asyncio.run(scenario())


def test_transport_can_be_advanced_by_different_http_tasks():
    async def scenario():
        async def connect(): return Socket()
        pool = EdgeSpeechPool(connect=connect)
        try:
            source = pool.stream(FIELDS, 7, no_fallback)
            assert await asyncio.create_task(anext(source)) == b'fixture-mp3'
            with pytest.raises(StopAsyncIteration):
                await asyncio.create_task(anext(source))
            assert not pool.connections[0].busy
        finally:
            await pool.close()
    asyncio.run(scenario())


def test_first_speech_joins_pending_preparation_without_opening_a_duplicate_socket():
    async def scenario():
        gate = asyncio.Event(); opened = []
        async def connect():
            await gate.wait()
            sock = Socket(); opened.append(sock); return sock
        pool = EdgeSpeechPool(connect=connect)
        try:
            pool.prepare(7)
            pending = asyncio.create_task(collect(pool))
            await asyncio.sleep(0)
            gate.set()
            assert await pending == b'fixture-mp3'
            assert len(opened) == 1
        finally:
            await pool.close()
    asyncio.run(scenario())


def test_busy_capacity_falls_back_without_opening_more_pooled_sockets():
    async def scenario():
        gate = asyncio.Event(); opened = []
        async def connect():
            sock = Socket(gate=gate); opened.append(sock); return sock
        async def fallback(): yield b'original-capacity-path'
        pool = EdgeSpeechPool(connect=connect, limit=1)
        first = asyncio.create_task(collect(pool))
        try:
            while not opened:
                await asyncio.sleep(0)
            assert await collect(pool, 8, fallback=fallback) == b'original-capacity-path'
            assert len(opened) == len(pool.connections) == 1
            gate.set()
            assert await first == b'fixture-mp3'
        finally:
            first.cancel(); await asyncio.gather(first, return_exceptions=True)
            await pool.close()
    asyncio.run(scenario())


def test_shutdown_cancels_pending_warmup_without_leaving_background_tasks():
    async def scenario():
        gate = asyncio.Event()
        async def connect():
            await gate.wait()
            return Socket()
        pool = EdgeSpeechPool(connect=connect)
        pool.prepare(7); await asyncio.sleep(0)
        await pool.close()
        assert pool.connections == [] and pool.warming == {} and pool.tasks == set()
    asyncio.run(scenario())
