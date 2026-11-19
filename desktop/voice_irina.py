"""Opt-in, CPU-only Russian speech for the VELIA owner."""
import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess

VOICE = {"voiceURI": "piper:irina", "name": "Ирина · быстрая", "lang": "ru-RU",
         "gender": "female", "localService": False}
MODEL_PATH = Path("/opt/velia/voice/irina.onnx")
SAMPLE_RATE = 22050
MAX_PCM_BYTES = SAMPLE_RATE * 2 * 90
LOG = logging.getLogger(__name__)


class IrinaVoice:
    def __init__(self, path=MODEL_PATH):
        self.path = Path(path)
        self.config_path = self.path.with_suffix(".onnx.json")
        self._voice = None
        self._lock = asyncio.Lock()

    def available_to(self, user_id):
        owner = os.getenv("VELIA_WEB_IRINA_OWNER_ID", "").strip()
        if type(user_id) is not int or user_id <= 0 or owner != str(user_id):
            return False
        try:
            return all(path.is_file() and os.access(path, os.R_OK)
                       for path in (self.path, self.config_path))
        except OSError as exc:
            LOG.warning("Irina voice assets unavailable (errno=%s)", exc.errno)
            return False

    def _load(self):
        from onnxruntime import InferenceSession, SessionOptions
        from piper import PiperConfig, PiperVoice

        options = SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        with self.config_path.open(encoding="utf-8") as config_file:
            config = PiperConfig.from_dict(json.load(config_file))
        session = InferenceSession(str(self.path), sess_options=options, providers=["CPUExecutionProvider"])
        return PiperVoice(session=session, config=config, use_tashkeel=False)

    def _render(self, text, rate):
        from piper import SynthesisConfig

        if self._voice is None:
            self._voice = self._load()
        pcm = bytearray()
        for chunk in self._voice.synthesize(text, syn_config=SynthesisConfig(length_scale=1 / rate)):
            if (chunk.sample_rate, chunk.sample_width, chunk.sample_channels) != (SAMPLE_RATE, 2, 1):
                raise RuntimeError("irina_audio_format")
            samples = chunk.audio_int16_bytes
            if len(pcm) + len(samples) > MAX_PCM_BYTES:
                raise RuntimeError("irina_audio_too_large")
            pcm.extend(samples)
        if not pcm:
            raise RuntimeError("irina_empty_audio")
        return subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(SAMPLE_RATE),
             "-ac", "1", "-i", "pipe:0", "-map_metadata", "-1", "-threads", "1",
             "-codec:a", "libmp3lame", "-b:a", "48k", "-f", "mp3", "pipe:1"],
            input=pcm, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=4,
        ).stdout

    async def synthesize(self, text, rate):
        # Cancelling an HTTP request must not release capacity while its CPU job is running.
        await self._lock.acquire()
        job = asyncio.get_running_loop().run_in_executor(None, self._render, text, rate)

        def release(finished):
            self._lock.release()
            if not finished.cancelled():
                finished.exception()

        job.add_done_callback(release)
        try:
            audio = await asyncio.shield(job)
        except Exception:
            raise RuntimeError("irina_synthesis_failed") from None
        if not audio:
            raise RuntimeError("irina_empty_audio")
        return audio
