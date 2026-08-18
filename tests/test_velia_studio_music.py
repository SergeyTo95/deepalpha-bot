from __future__ import annotations

import hashlib
import io
import wave
from types import SimpleNamespace

import pytest

import services.velia_studio_generation_service as generation_service
import services.velia_studio_music_duration_client as duration_client
import services.velia_studio_music_prompt_service as prompt_service
import services.velia_studio_service as studio_service
from services.velia_media_worker_client import MediaWorkerArtifact, MediaWorkerError
from services.velia_music_service import MUSIC_ATTRIBUTION, inspect_music_wav


def _wav(seconds: int = 1, sample_rate: int = 32000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\0\0\0\0" * sample_rate * seconds)
    return buffer.getvalue()


def test_music_wav_validation_accepts_exact_contract() -> None:
    raw = _wav(2)
    metadata = inspect_music_wav(raw)
    assert metadata["duration_seconds"] == 2.0
    assert metadata["sample_rate_hz"] == 32000
    assert metadata["channels"] == 2
    assert metadata["sha256"] == hashlib.sha256(raw).hexdigest()


def test_music_wav_validation_accepts_native_music3_sample_rate() -> None:
    raw = _wav(2, sample_rate=44100)
    metadata = inspect_music_wav(raw)
    assert metadata["duration_seconds"] == 2.0
    assert metadata["sample_rate_hz"] == 44100
    assert metadata["channels"] == 2


def test_music_wav_validation_rejects_mono() -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(32000)
        audio.writeframes(b"\0\0" * 32000)
    with pytest.raises(ValueError, match="music_artifact_format_invalid"):
        inspect_music_wav(buffer.getvalue())


def test_music_attribution_discloses_ai_generation() -> None:
    assert MUSIC_ATTRIBUTION == "MiniMax-Music3 · AI-generated"


def test_submit_music_job_preserves_contract(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        duration_client,
        "submit_job",
        lambda **kwargs: captured.update(kwargs) or {
            "job_id": "music-job-1", "status": "queued", "progress_percent": 0
        },
    )
    result = duration_client.submit_studio_music_job(
        prompt="Genre: synth-pop", lyrics="[Verse]\nПривет", instrumental=False,
        request_id="generation-1", duration_seconds=30,
    )
    assert captured["kind"] == "music"
    assert captured["payload"]["duration_seconds"] == 30
    assert captured["payload"]["lyrics"] == "[Verse]\nПривет"
    assert result["job_id"] == "music-job-1"


def test_poll_music_job_validates_and_returns_wav(monkeypatch) -> None:
    raw = _wav(1)
    monkeypatch.setattr(
        duration_client,
        "get_job_status",
        lambda **kwargs: {"job_id": "music-job-1", "status": "succeeded", "artifact": {}},
    )
    monkeypatch.setattr(
        duration_client,
        "artifact_from_job",
        lambda **kwargs: MediaWorkerArtifact(
            job_id="music-job-1", artifact_id="artifact-1", media_type="audio/wav",
            size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), content=raw,
        ),
    )
    result = duration_client.poll_studio_music_job(
        job_id="music-job-1", request_id="generation-1", duration_seconds=30
    )
    assert result["status"] == "succeeded"
    assert result["generated"]["audio_bytes"] == raw


def test_custom_lyrics_are_preserved_and_prompt_is_rewritten(monkeypatch) -> None:
    def generate_music_text(_prompt, *, feature, **_kwargs):
        assert feature == "studio_music_prompt"
        return "Genre: cinematic rock. BPM: 110. Vocals: powerful male lead."

    monkeypatch.setattr(
        "services.llm_service.generate_music_text", generate_music_text
    )
    lyrics = "[Verse]\nМой точный текст\n\n[Chorus]\nНе меняй меня"
    normalized = prompt_service.normalize_music_request(
        prompt="Русский рок про космос", lyrics_mode="custom", lyrics=lyrics,
        duration_seconds=60, user_id=7, generation_id="generation-1", session_id="session-1",
    )
    assert normalized.lyrics == lyrics
    assert normalized.instrumental is False
    assert normalized.prompt.startswith("Genre: cinematic rock")


def test_auto_lyrics_use_request_language(monkeypatch) -> None:
    calls = []
    def generate_music_text(_prompt, *, feature, **_kwargs):
        calls.append(feature)
        if feature == "studio_music_lyrics":
            return "[Verse]\nНад городом свет\n[Chorus]\nМы летим"
        return "Genre: electronic pop. BPM: 118. Bright female vocals."
    monkeypatch.setattr("services.llm_service.generate_music_text", generate_music_text)
    normalized = prompt_service.normalize_music_request(
        prompt="песня на русском про ночной город", lyrics_mode="auto", lyrics="",
        duration_seconds=30, user_id=7, generation_id="generation-1", session_id="session-1",
    )
    assert "Над городом" in normalized.lyrics
    assert calls == ["studio_music_lyrics", "studio_music_prompt"]


def test_instrumental_does_not_call_lyrics_generator(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.llm_service.generate_music_text",
        lambda _prompt, *, feature, **_kwargs: "Genre: ambient. No vocals.",
    )
    normalized = prompt_service.normalize_music_request(
        prompt="спокойный эмбиент", lyrics_mode="instrumental", lyrics="ignored",
        duration_seconds=30, user_id=7, generation_id="generation-1", session_id="session-1",
    )
    assert normalized.instrumental is True
    assert normalized.lyrics == ""


def test_generation_service_dispatches_music(monkeypatch) -> None:
    monkeypatch.setattr(studio_service, "_ensure_schema", lambda: None)
    monkeypatch.setattr(studio_service, "studio_enabled", lambda: True)
    monkeypatch.setattr(studio_service, "get_session", lambda *_args: {"mode": "music"})
    monkeypatch.setattr(studio_service, "_prompt", lambda value: str(value).strip())
    monkeypatch.setattr(studio_service, "_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr(studio_service, "_reference_ids", lambda values: list(values or []))
    monkeypatch.setattr(generation_service, "self_hosted_music_active", lambda: True)
    captured = {}
    monkeypatch.setattr(
        generation_service,
        "generate_self_hosted_studio_music_turn",
        lambda **kwargs: captured.update(kwargs) or {"generation": {"id": "music-1"}},
    )
    result = generation_service.generate_studio_turn(
        user_id=7, session_id="session-1", prompt="русский рок",
        client_request_id="client-1", duration_seconds=60,
        lyrics_mode="custom", lyrics="[Verse]\nТекст",
    )
    assert captured["duration_seconds"] == 60
    assert captured["lyrics_mode"] == "custom"
    assert captured["lyrics"] == "[Verse]\nТекст"
    assert result["generation"]["id"] == "music-1"


def test_completed_music_media_falls_back_to_generation_id(monkeypatch) -> None:
    lookups = []

    def lookup(request_id: str, user_id: int):
        lookups.append((request_id, user_id))
        if request_id == "generation-1":
            return {
                "id": "music-1",
                "content_url": "/api/mobile/music/music-1/content?signed=1",
                "mime_type": "audio/wav",
            }
        return None

    monkeypatch.setattr(studio_service, "music_metadata_for_request", lookup)
    media = studio_service._generation_media(
        "music", "stale-output-id", "generation-1", 7
    )

    assert media is not None
    assert media["content_url"].startswith("/api/mobile/music/")
    assert lookups == [("stale-output-id", 7), ("generation-1", 7)]


def test_completed_music_media_uses_generation_id_when_output_is_blank(monkeypatch) -> None:
    monkeypatch.setattr(
        studio_service,
        "music_metadata_for_request",
        lambda request_id, user_id: {
            "id": "music-1",
            "content_url": f"/api/mobile/music/{request_id}/content?user_id={user_id}",
            "mime_type": "audio/wav",
        },
    )

    media = studio_service._generation_media("music", "", "generation-1", 7)

    assert media is not None
    assert "generation-1" in media["content_url"]


@pytest.mark.parametrize("duration", [5, 10, 20, 301])
def test_music_rejects_unsupported_duration(duration: int) -> None:
    with pytest.raises(MediaWorkerError, match="studio_music_duration_not_supported"):
        duration_client.submit_studio_music_job(
            prompt="ambient", lyrics="", instrumental=True,
            request_id="generation-1", duration_seconds=duration,
        )
