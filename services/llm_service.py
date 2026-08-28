import os
import uuid
from typing import Optional

from services.gemini_gateway import call_gemini

LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def _payload(prompt: str, max_tokens: int) -> dict:
    return {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7},
    }


def _call(prompt: str, *, feature: str, origin: str, max_tokens: int, user_id=None, chat_id=None,
          is_background: bool = False, request_id: Optional[str] = None, cycle_id=None, job_id=None,
          max_attempts: Optional[int] = None, **_ignored) -> str:
    # _ignored intentionally absorbs legacy budget_checked/admin_override without bypassing the gateway.
    result = call_gemini(
        feature=feature,
        origin=origin,
        model=GEMINI_MODEL,
        payload=_payload(prompt, max_tokens),
        user_id=user_id,
        chat_id=chat_id,
        is_background=is_background,
        request_id=request_id or uuid.uuid4().hex,
        cycle_id=cycle_id,
        job_id=job_id,
        max_attempts=max_attempts if max_attempts is not None else int(os.getenv("GEMINI_DEFAULT_MAX_ATTEMPTS", "1")),
        timeout=LLM_TIMEOUT,
    )
    return result.get("text", "") if result.get("ok") else ""


def generate_text(prompt: str, **kwargs) -> str:
    return _call(prompt, feature=kwargs.pop("feature", "summary_agent"), origin=kwargs.pop("origin", "summary_agent"), max_tokens=512, **kwargs)


def generate_decision_text(prompt: str, **kwargs) -> str:
    return _call(prompt, feature=kwargs.pop("feature", "decision_agent"), origin=kwargs.pop("origin", "decision_agent"), max_tokens=1024, **kwargs)


def generate_news_text(prompt: str, **kwargs) -> str:
    return _call(prompt, feature=kwargs.pop("feature", "news_agent"), origin=kwargs.pop("origin", "news_agent"), max_tokens=768, **kwargs)
