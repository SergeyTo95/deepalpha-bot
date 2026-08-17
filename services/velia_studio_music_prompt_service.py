from __future__ import annotations

import logging
import re
from dataclasses import dataclass


logger = logging.getLogger(__name__)
_LYRICS_MODES = {"auto", "custom", "instrumental"}


@dataclass(frozen=True)
class NormalizedMusicRequest:
    prompt: str
    lyrics: str
    instrumental: bool
    lyrics_mode: str


def _clean_prompt(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip().strip('"\'')
    return cleaned[:12000]


def _clean_generated_lyrics(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
        if text.lower().startswith("text\n"):
            text = text[5:].strip()
    text = re.sub(r"^(?:lyrics|текст песни)\s*:\s*", "", text, flags=re.I)
    return text[:20000].strip()


def _rewrite_prompt(source: str, *, user_id: int, generation_id: str, session_id: str) -> str:
    instruction = (
        "Rewrite the request as a concise production prompt for MiniMax-Music3. "
        "Return only the final English music-description prompt. Preserve the requested "
        "genre, mood, era, language of vocals, instruments, tempo, vocal type and theme. "
        "Specify a coherent arrangement, clean studio-quality mix and natural vocals. "
        "Do not write lyrics and do not add commentary. Request:\n" + source
    )
    try:
        from services import llm_service

        rewritten = llm_service.generate_music_text(
            instruction,
            feature="studio_music_prompt",
            user_id=int(user_id),
            request_id=generation_id,
            cycle_id=session_id,
            job_id=generation_id,
        )
    except Exception as exc:
        logger.warning(
            "VELIA_STUDIO_MUSIC_PROMPT_REWRITE_FAILED generation_id=%s error_type=%s",
            generation_id[:80], type(exc).__name__,
        )
        return source
    cleaned = _clean_prompt(rewritten)
    if len(cleaned) < 8:
        return source
    logger.info(
        "VELIA_STUDIO_MUSIC_PROMPT_REWRITTEN generation_id=%s source_chars=%s output_chars=%s",
        generation_id[:80], len(source), len(cleaned),
    )
    return cleaned


def _generate_lyrics(
    source: str,
    *,
    duration_seconds: int,
    user_id: int,
    generation_id: str,
    session_id: str,
) -> str:
    instruction = (
        "Write original singable song lyrics for the request below. Return only lyrics. "
        "Use the language explicitly requested by the user; otherwise use the language "
        "of the user request. Use section tags such as [Verse], [Chorus], [Bridge]. "
        "Fit approximately {duration} seconds, keep a memorable chorus, natural rhyme "
        "and avoid copyrighted lyrics or imitation of a living artist. Request:\n{source}"
    ).format(duration=int(duration_seconds), source=source)
    try:
        from services import llm_service

        generated = llm_service.generate_music_text(
            instruction,
            feature="studio_music_lyrics",
            user_id=int(user_id),
            request_id=generation_id,
            cycle_id=session_id,
            job_id=generation_id,
        )
    except Exception as exc:
        logger.warning(
            "VELIA_STUDIO_MUSIC_LYRICS_FAILED generation_id=%s error_type=%s",
            generation_id[:80], type(exc).__name__,
        )
        return ""
    lyrics = _clean_generated_lyrics(generated)
    if lyrics:
        logger.info(
            "VELIA_STUDIO_MUSIC_LYRICS_GENERATED generation_id=%s chars=%s",
            generation_id[:80], len(lyrics),
        )
    return lyrics


def normalize_music_request(
    *,
    prompt: str,
    lyrics_mode: str,
    lyrics: str,
    duration_seconds: int,
    user_id: int,
    generation_id: str,
    session_id: str,
) -> NormalizedMusicRequest:
    source = _clean_prompt(prompt)
    mode = str(lyrics_mode or "auto").strip().lower()
    if mode not in _LYRICS_MODES:
        raise ValueError("studio_music_lyrics_mode_invalid")
    if mode == "instrumental":
        final_lyrics = ""
        instrumental = True
    elif mode == "custom":
        # Custom lyrics are user content: preserve line breaks and wording exactly
        # apart from harmless outer whitespace.
        final_lyrics = str(lyrics or "").strip()[:20000]
        if not final_lyrics:
            raise ValueError("studio_music_lyrics_required")
        instrumental = False
    else:
        final_lyrics = _generate_lyrics(
            source,
            duration_seconds=duration_seconds,
            user_id=user_id,
            generation_id=generation_id,
            session_id=session_id,
        )
        if not final_lyrics:
            raise ValueError("studio_music_lyrics_generation_failed")
        instrumental = False
    return NormalizedMusicRequest(
        prompt=_rewrite_prompt(
            source,
            user_id=user_id,
            generation_id=generation_id,
            session_id=session_id,
        ),
        lyrics=final_lyrics,
        instrumental=instrumental,
        lyrics_mode=mode,
    )
