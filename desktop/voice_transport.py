"""Bounded, account-scoped Edge speech connections; no audio/text cache."""
import asyncio
import json
import logging
import time
from contextlib import aclosing
from dataclasses import dataclass

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar, TCPConnector, WSMsgType
from edge_tts import Communicate
from edge_tts.communicate import (connect_id, date_to_string, get_headers_and_data,
                                 mkssml, ssml_headers_plus_data, _SSL_CTX)
from edge_tts.constants import WSS_HEADERS, WSS_URL, SEC_MS_GEC_VERSION
from edge_tts.drm import DRM

LOG = logging.getLogger(__name__)


@dataclass(eq=False)
class Connection:
    owner: int
    socket: object = None
    busy: bool = True
    expiry: object = None


class EdgeSpeechPool:
    def __init__(self, *, limit=8, idle_seconds=45, connect=None):
        self.limit, self.idle_seconds = limit, idle_seconds
        self.connections = []
        self.warming = {}
        self.tasks = set()
        self.client = None
        self.closed = False
        self.connect = connect or self._connect

    async def start(self):
        self.client = ClientSession(trust_env=True, cookie_jar=DummyCookieJar(),
            connector=TCPConnector(limit=self.limit, ttl_dns_cache=300),
            timeout=ClientTimeout(total=None, sock_connect=5, sock_read=10))

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _connect(self):
        # Fixed provider, certificate verification and the pinned library's
        # protocol/voice/MP3 format. No client-controlled URL or credentials.
        return await self.client.ws_connect(
            f"{WSS_URL}&ConnectionId={connect_id()}&Sec-MS-GEC={DRM.generate_sec_ms_gec()}"
            f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}",
            compress=15, headers=DRM.headers_with_muid(WSS_HEADERS), ssl=_SSL_CTX,
            max_msg_size=2 * 1024 * 1024)

    async def _discard(self, connection):
        if connection.expiry:
            connection.expiry.cancel()
        if connection in self.connections:
            self.connections.remove(connection)
        if connection.socket is not None:
            await connection.socket.close()

    def _expire(self, connection):
        if not connection.busy:
            connection.busy = True
            self._spawn(self._discard(connection))

    async def _acquire(self, owner):
        if self.closed:
            raise RuntimeError("tts_pool_closed")
        for connection in list(self.connections):
            if not connection.busy and connection.socket.closed:
                await self._discard(connection)
            elif connection.owner == owner and not connection.busy:
                connection.busy = True
                connection.expiry.cancel()
                return connection, True
        evicted = None
        if len(self.connections) >= self.limit:
            # Evict an idle socket rather than share it with a different owner.
            idle = next((c for c in self.connections if not c.busy), None)
            if idle is None:
                raise RuntimeError("tts_pool_busy")
            idle.busy = True
            self.connections.remove(idle)
            if idle.expiry:
                idle.expiry.cancel()
            evicted = idle
        connection = Connection(owner)
        self.connections.append(connection)
        try:
            if evicted:
                await self._discard(evicted)
            async with asyncio.timeout(8):
                connection.socket = await self.connect()
            if self.closed:
                raise RuntimeError("tts_pool_closed")
            return connection, False
        except BaseException:
            await self._discard(connection)
            raise

    def _release(self, connection):
        connection.busy = False
        connection.expiry = asyncio.get_running_loop().call_later(
            self.idle_seconds, self._expire, connection)

    def prepare(self, owner):
        if (self.closed or owner in self.warming or len(self.warming) >= self.limit
                or any(c.owner == owner for c in self.connections)):
            return
        async def warm():
            started = time.monotonic()
            try:
                connection, _ = await self._acquire(owner)
                self._release(connection)
                LOG.info("VELIA_WEB_TTS_PREPARED connect_ms=%d", round((time.monotonic()-started)*1000))
            except (ClientError, OSError, RuntimeError, TimeoutError) as exc:
                # Preparation is best effort and sends no synthesis request.
                LOG.info("VELIA_WEB_TTS_PREPARE_SKIPPED kind=%s", type(exc).__name__)
            finally:
                self.warming.pop(owner, None)
        self.warming[owner] = self._spawn(warm())

    async def stream(self, fields, owner, fallback):
        connection = None
        complete = False
        emitted = 0
        started, first = time.monotonic(), None
        try:
            warm = self.warming.get(owner)
            if warm:
                await asyncio.shield(warm)
            async with asyncio.timeout(20):
                connection, reused = await self._acquire(owner)
                speaker = Communicate(fields["text"], fields["edge_voice"], rate=fields["edge_rate"])
                texts = list(speaker.texts)
                if len(texts) != 1:
                    raise RuntimeError("tts_invalid_segment")
                request_id = connect_id()
                audio_config = {"context": {"synthesis": {"audio": {
                    "metadataoptions": {"sentenceBoundaryEnabled": "true", "wordBoundaryEnabled": "false"},
                    "outputFormat": "audio-24khz-48kbitrate-mono-mp3"}}}}
                await connection.socket.send_str(f"X-Timestamp:{date_to_string()}\r\n"
                    "Content-Type:application/json; charset=utf-8\r\nPath:speech.config\r\n\r\n"
                    + json.dumps(audio_config) + "\r\n")
                await connection.socket.send_str(ssml_headers_plus_data(
                    request_id, date_to_string(), mkssml(speaker.tts_config, texts[0])))
                frames = 0
            # End the deadline scope before yielding: HTTP streaming may
            # request the next audio frame from a different task.
            deadline = started + 20
            while True:
                async with asyncio.timeout(max(0, deadline-time.monotonic())):
                    incoming = await connection.socket.receive()
                frames += 1
                if frames > 10000:
                    raise RuntimeError("tts_invalid_frames")
                if incoming.type == WSMsgType.TEXT:
                    data = incoming.data.encode()
                    if len(data) > 65536 or b"\r\n\r\n" not in data:
                        raise RuntimeError("tts_invalid_frame")
                    headers, _ = get_headers_and_data(data, data.index(b"\r\n\r\n"))
                elif incoming.type == WSMsgType.BINARY:
                    data = incoming.data
                    if len(data) < 2:
                        raise RuntimeError("tts_invalid_audio")
                    length = int.from_bytes(data[:2], "big")
                    if length < 2 or length + 2 > len(data):
                        raise RuntimeError("tts_invalid_audio")
                    header_bytes = data[2:2+length].rstrip(b"\r\n")
                    headers = dict(line.split(b":", 1) for line in header_bytes.split(b"\r\n"))
                    audio = data[2+length:]
                else:
                    raise RuntimeError("tts_socket_closed")
                if headers.get(b"X-RequestId") != request_id.encode():
                    raise RuntimeError("tts_request_mismatch")
                path = headers.get(b"Path")
                if incoming.type == WSMsgType.BINARY:
                    if path != b"audio" or headers.get(b"Content-Type") not in (b"audio/mpeg", None):
                        raise RuntimeError("tts_invalid_audio")
                    if headers.get(b"Content-Type") is None and not audio:
                        continue
                    if not audio or headers.get(b"Content-Type") is None:
                        raise RuntimeError("tts_invalid_audio")
                    emitted += len(audio)
                    if emitted > 2 * 1024 * 1024:
                        raise RuntimeError("tts_audio_too_large")
                    first = first or time.monotonic()
                    yield audio
                elif path == b"turn.end":
                    if not emitted:
                        raise RuntimeError("tts_empty_audio")
                    complete = True
                    LOG.info("VELIA_WEB_TTS_POOL_TIMING reused=%s first_audio_ms=%d total_ms=%d",
                        reused, round((first-started)*1000), round((time.monotonic()-started)*1000))
                    break
                elif path not in (b"turn.start", b"response", b"audio.metadata"):
                    raise RuntimeError("tts_invalid_path")
        except (ClientError, OSError, RuntimeError, TimeoutError, ValueError, TypeError) as exc:
            if emitted:
                raise RuntimeError("tts_unavailable") from None
            LOG.info("VELIA_WEB_TTS_POOL_FALLBACK kind=%s", type(exc).__name__)
            async with aclosing(fallback()) as source:
                async for audio in source:
                    yield audio
        finally:
            if connection is not None:
                if complete and not self.closed:
                    self._release(connection)
                else:
                    await self._discard(connection)

    async def close(self):
        self.closed = True
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        for connection in list(self.connections):
            await self._discard(connection)
        if self.client is not None:
            await self.client.close()
