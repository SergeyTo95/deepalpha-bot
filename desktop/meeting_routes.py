"""Bounded meeting audio uploads through the existing opt-in transcription account."""
import asyncio
from collections import deque
import json
import math
from pathlib import Path
import re
import tempfile
import time
from aiohttp import web, ClientError
from desktop import voice_transcription as stt
from velia_desktop_routes import AuthenticationUnavailable

MAX_AUDIO=15*1024*1024
MAX_SECONDS=1200
FORMATS={'audio/mpeg':('mp3','mp3'),'audio/wav':('wav','wav'),'audio/flac':('flac','flac'),
         'audio/mp4':('m4a','mov'),'audio/ogg':('ogg','ogg'),'audio/webm':('webm','matroska')}


def signature(data,mime):
    if mime=='audio/mpeg' and (data.startswith(b'ID3') or len(data)>2 and data[0]==255 and data[1]&224==224):return
    if mime=='audio/wav' and data[:4]==b'RIFF' and data[8:12]==b'WAVE':return
    if mime=='audio/flac' and data.startswith(b'fLaC'):return
    stt.audio_type(data,mime)


async def audio_duration(data,mime):
    extension,demuxer=FORMATS[mime]
    with tempfile.TemporaryDirectory(prefix='velia-meeting-') as directory:
        path=Path(directory)/('recording.'+extension)
        path.write_bytes(data)
        process=await asyncio.create_subprocess_exec('ffprobe','-v','error','-protocol_whitelist','file',
            '-f',demuxer,'-show_entries','format=duration:stream=codec_type','-of','json',str(path),
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            async with asyncio.timeout(10):out,_=await process.communicate()
        finally:
            if process.returncode is None:
                process.kill();await process.wait()
        try:
            info=json.loads(out);duration=float(info['format']['duration'])
            if process.returncode or not math.isfinite(duration) or duration<=0 or not any(s.get('codec_type')=='audio' for s in info.get('streams',[])):raise ValueError()
            return duration
        except (ValueError,KeyError,TypeError):raise ValueError('meeting_invalid_audio') from None


def setup_meeting_routes(app,*,session_for,same_origin,json_response):
    histories={};slot=asyncio.Semaphore(1)
    async def transcribe(request):
        if not same_origin(request):return json_response({'ok':False,'error':'invalid_origin'},403)
        try:
            session=await session_for(request)
            if not session:return json_response({'ok':False,'error':'unauthorized'},401)
            if not stt.enabled():return json_response({'ok':False,'error':'meeting_transcription_unavailable'},503)
            mime=request.content_type;language=request.query.get('language','')
            if mime not in FORMATS or language and not re.fullmatch('[a-z]{2}',language):raise ValueError()
            now=time.monotonic()
            for owner in list(histories):
                if histories[owner][-1]<=now-3600:del histories[owner]
            history=histories.get(session.user_id,deque())
            while history and history[0]<=now-3600:history.popleft()
            if slot.locked() or len(history)>=2:return json_response({'ok':False,'error':'meeting_rate_limit'},429)
            async with slot,asyncio.timeout(110):
                audio=bytearray()
                async for part in request.content.iter_chunked(65536):
                    audio.extend(part)
                    if len(audio)>MAX_AUDIO:return json_response({'ok':False,'error':'meeting_audio_too_large'},413)
                signature(audio,mime)
                duration=await audio_duration(bytes(audio),mime)
                if duration>MAX_SECONDS:return json_response({'ok':False,'error':'meeting_audio_too_long'},400)
                history.append(time.monotonic());histories[session.user_id]=history
                text=await stt.transcribe(bytes(audio),mime,language,timeout=90,max_text=30000)
                if not text:raise ValueError()
            return json_response({'ok':True,'text':text,'duration_seconds':round(duration,2),
                                  'warning':'transcription_requires_review','speaker_identification':False})
        except AuthenticationUnavailable:return json_response({'ok':False,'error':'authentication_unavailable'},503)
        except (ValueError,UnicodeError):return json_response({'ok':False,'error':'meeting_invalid_audio'},400)
        except stt.TranscriptionLimited:return json_response({'ok':False,'error':'meeting_rate_limit'},429)
        except (ClientError,TimeoutError,RuntimeError,OSError):return json_response({'ok':False,'error':'meeting_transcription_unavailable'},503)
    app.router.add_post('/web-api/v1/meetings/transcribe',transcribe)
