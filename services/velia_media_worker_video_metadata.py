"""Read track metadata from the worker's ordinary (non-fragmented) MP4 output.

The backend preserves the downloaded bytes. No decoder, subprocess, external URL
or system ffmpeg dependency is needed in the Railway API process. GPU-side
acceptance remains responsible for decoding, audiovisual quality and sync.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class VideoMetadata:
    duration_seconds: float
    width: int
    height: int
    fps: float
    has_audio: bool


def _boxes(data: memoryview) -> Iterator[tuple[bytes, memoryview]]:
    offset = 0
    count = 0
    while offset < len(data):
        count += 1
        if count > 4096 or len(data) - offset < 8:
            raise ValueError("invalid MP4 box header")
        size, kind = struct.unpack_from(">I4s", data, offset)
        header = 8
        if size == 1:
            if len(data) - offset < 16:
                raise ValueError("truncated extended MP4 box")
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header = 16
        elif size == 0:
            size = len(data) - offset
        if size < header or size > len(data) - offset:
            raise ValueError("invalid MP4 box size")
        yield kind, data[offset + header:offset + size]
        offset += size


def _child(data: memoryview, kind: bytes) -> memoryview:
    found = [body for tag, body in _boxes(data) if tag == kind]
    if len(found) != 1:
        raise ValueError("missing or duplicate MP4 box")
    return found[0]


def _duration(mdhd: memoryview) -> float:
    if len(mdhd) < 24:
        raise ValueError("truncated media header")
    if mdhd[0] == 0:
        timescale, duration = struct.unpack_from(">II", mdhd, 12)
        unknown = 2**32 - 1
    elif mdhd[0] == 1 and len(mdhd) >= 36:
        timescale = struct.unpack_from(">I", mdhd, 20)[0]
        duration = struct.unpack_from(">Q", mdhd, 24)[0]
        unknown = 2**64 - 1
    else:
        raise ValueError("unsupported media header")
    if not timescale or not duration or duration == unknown:
        raise ValueError("invalid track duration")
    return duration / timescale


def inspect_mp4(content: bytes, *, expected_duration: int) -> VideoMetadata:
    """Reject truncated containers and infer audio from populated sound tracks.

    Merely finding the string 'soun' (or trusting the selected model) would label
    silent clips incorrectly. Follow moov/trak/mdia/hdlr and populated sample
    tables; a header-only ftyp file is never a completed video.
    """
    root = list(_boxes(memoryview(content)))
    if sum(tag == b"ftyp" for tag, _ in root) != 1:
        raise ValueError("missing MP4 file type")
    movies = [body for tag, body in root if tag == b"moov"]
    if len(movies) != 1 or not any(tag == b"mdat" and len(body) for tag, body in root):
        raise ValueError("missing MP4 movie or media data")
    video_tracks = []
    audio_durations = []
    for kind, track in _boxes(movies[0]):
        if kind != b"trak":
            continue
        mdia = _child(track, b"mdia")
        handler = _child(mdia, b"hdlr")
        if len(handler) < 12:
            raise ValueError("truncated MP4 handler")
        media_kind = bytes(handler[8:12])
        if media_kind not in {b"vide", b"soun"}:
            continue
        duration = _duration(_child(mdia, b"mdhd"))
        table = _child(_child(mdia, b"minf"), b"stbl")
        sizes = _child(table, b"stsz")
        if len(sizes) < 12:
            raise ValueError("truncated sample table")
        sample_size, samples = struct.unpack_from(">II", sizes, 4)
        if not samples or (not sample_size and len(sizes) != 12 + 4 * samples):
            raise ValueError("empty or truncated sample table")
        if media_kind == b"soun":
            audio_durations.append(duration)
            continue
        tkhd = _child(track, b"tkhd")
        if len(tkhd) < 84 or tkhd[0] not in (0, 1):
            raise ValueError("invalid video track header")
        width, height = (value >> 16 for value in struct.unpack_from(">II", tkhd, len(tkhd) - 8))
        fps = samples / duration
        if min(width, height) < 16 or max(width, height) > 8192 or not 1 <= fps <= 240:
            raise ValueError("invalid video dimensions or frame rate")
        video_tracks.append((duration, width, height, fps))
    if len(video_tracks) != 1:
        raise ValueError("expected exactly one populated video track")
    duration, width, height, fps = video_tracks[0]
    # H3 normalizes to its own frame lattice; allow rounding, not short previews.
    if not math.isfinite(duration) or abs(duration - expected_duration) > 0.75:
        raise ValueError("video duration differs from requested duration")
    if len(audio_durations) > 1 or any(value + 0.25 < duration for value in audio_durations):
        raise ValueError("invalid or truncated soundtrack")
    return VideoMetadata(duration, width, height, fps, bool(audio_durations))
