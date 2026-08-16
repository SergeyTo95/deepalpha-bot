from __future__ import annotations

import logging
import os
import re
from typing import Optional


logger = logging.getLogger(__name__)

_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
_LEADING_LABEL_RE = re.compile(
    r"^(?:english\s+)?(?:video\s+)?prompt\s*:\s*",
    flags=re.IGNORECASE,
)


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _rewrite_instruction(prompt: str) -> str:
    return (
        "Rewrite the user request as one production-ready English prompt for a "
        "text-to-video model. Return only the final English prompt, without a label, "
        "quotes, Markdown, or explanation. Preserve every requested subject, species, "
        "count, action, location, era, and visual style. Do not replace animals with "
        "people. Make the main subjects immediately recognizable and visible in a "
        "well-lit medium-wide or wide shot. Describe one coherent five-second shot "
        "with clear foreground action, natural motion, stable anatomy, sharp detail, "
        "balanced exposure, and no text or logos. If the request is not English, "
        "translate it accurately into English first.\n\n"
        f"User request:\n{prompt.strip()}"
    )


def _clean_rewrite(value: str) -> str:
    cleaned = str(value or "").strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = cleaned[3:-3].strip()
        if cleaned.lower().startswith("text\n"):
            cleaned = cleaned[5:].strip()
    cleaned = _LEADING_LABEL_RE.sub("", cleaned).strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()
    return " ".join(cleaned.split())[:4000]


def rewrite_studio_video_prompt(
    prompt: str,
    *,
    user_id: int,
    generation_id: str,
    session_id: Optional[str] = None,
) -> str:
    """Translate/expand a Studio prompt for the English-first GPU model.

    Rewriting is deliberately fail-open: provider configuration, quota, or network
    failures must not prevent an already accepted video request from reaching the
    media worker.
    """
    source = str(prompt or "").strip()
    if not source or not _env_enabled("VELIA_STUDIO_VIDEO_PROMPT_REWRITE_ENABLED", True):
        return source

    try:
        from services import llm_service

        rewritten = llm_service.generate_text(
            _rewrite_instruction(source),
            feature="studio_video_prompt",
            user_id=int(user_id),
            is_background=False,
            request_id=str(generation_id),
            cycle_id=str(session_id or generation_id),
            job_id=str(generation_id),
        )
    except Exception as exc:
        logger.warning(
            "VELIA_STUDIO_VIDEO_PROMPT_REWRITE_FAILED generation_id=%s error_type=%s",
            str(generation_id)[:80],
            type(exc).__name__,
        )
        return source

    cleaned = _clean_rewrite(rewritten)
    if len(cleaned) < 12:
        logger.warning(
            "VELIA_STUDIO_VIDEO_PROMPT_REWRITE_EMPTY generation_id=%s",
            str(generation_id)[:80],
        )
        return source

    logger.info(
        "VELIA_STUDIO_VIDEO_PROMPT_REWRITTEN generation_id=%s source_chars=%s output_chars=%s source_cyrillic=%s output_cyrillic=%s",
        str(generation_id)[:80],
        len(source),
        len(cleaned),
        bool(_CYRILLIC_RE.search(source)),
        bool(_CYRILLIC_RE.search(cleaned)),
    )
    return cleaned
