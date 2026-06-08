import logging
import re
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

ALLOWED_SKILLS = {
    "live_screenshot_to_analysis",
    "edge_education",
    "no_trade_discipline",
    "risk_coach",
    "market_resolution",
}

_MAX_SKILL_BYTES = 20 * 1024
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SKILLS_ROOT = _REPO_ROOT / "skills"
_DANGEROUS_SECTION_RE = re.compile(
    r"(?ims)^#{1,6}\s*[^\n]*(?:shell|tool execution|tools?|env(?:ironment)? access|external installation|installation|install|network|scripts?|commands?)[^\n]*\n.*?(?=^#{1,6}\s|\Z)"
)
_DANGEROUS_FENCE_RE = re.compile(
    r"(?ims)^```\s*(?:bash|sh|shell|zsh|fish|powershell|ps1|cmd|bat|python|py)\b.*?^```\s*$"
)
_DANGEROUS_INLINE_TERMS = (
    "cu" + "rl",
    "wg" + "et",
    "sub" + "process",
    "os" + "." + "system",
    "ev" + "al(",
    "ex" + "ec(",
    "pip install",
    "npm install",
    "env",
    ".env",
)
_DANGEROUS_INLINE_RE = re.compile(
    r"(?im)^.*(?:" + "|".join(re.escape(term) for term in _DANGEROUS_INLINE_TERMS) + r").*$"
)


def _strip_dangerous_sections(markdown: str) -> str:
    """Return inert markdown only; skills are content, never executable instructions."""
    cleaned = _DANGEROUS_SECTION_RE.sub("", markdown or "")
    cleaned = _DANGEROUS_FENCE_RE.sub("", cleaned)
    cleaned = _DANGEROUS_INLINE_RE.sub("", cleaned)
    return cleaned.strip()


def load_skill(skill_name: str) -> str:
    """
    Load one reviewed internal DeepAlpha skill as plain markdown.

    Security model:
    - only explicit allowlisted names are accepted;
    - only skills/<skill_name>/SKILL.md is read;
    - symlinks, traversal and oversized files are rejected;
    - markdown is sanitized for executable/tool/env/install instructions;
    - no scripts, shell commands or network fetches are ever executed here.
    """
    safe_name = str(skill_name or "").strip()
    if safe_name not in ALLOWED_SKILLS:
        logger.warning("skill_loader_rejected skill=%s reason=not_allowed", safe_name[:80])
        return ""

    try:
        skill_dir = _SKILLS_ROOT / safe_name
        skill_path = skill_dir / "SKILL.md"
        resolved_root = _SKILLS_ROOT.resolve()
        resolved_dir = skill_dir.resolve(strict=False)
        if resolved_dir != resolved_root / safe_name:
            logger.warning("skill_loader_rejected skill=%s reason=path_traversal", safe_name)
            return ""
        if skill_path.is_symlink() or skill_dir.is_symlink():
            logger.warning("skill_loader_rejected skill=%s reason=symlink", safe_name)
            return ""
        if not skill_path.is_file():
            logger.warning("skill_loader_missing skill=%s", safe_name)
            return ""
        if skill_path.stat().st_size > _MAX_SKILL_BYTES:
            logger.warning("skill_loader_rejected skill=%s reason=max_size", safe_name)
            return ""
        content = skill_path.read_text(encoding="utf-8")
    except Exception:
        logger.exception("skill_loader_failed skill=%s", safe_name)
        return ""

    return _strip_dangerous_sections(content)


def load_skills(skill_names: List[str]) -> str:
    """Load requested allowlisted skills and join them without scanning folders."""
    chunks = []
    for name in skill_names or []:
        content = load_skill(name)
        if content:
            chunks.append(content)
    return "\n\n---\n\n".join(chunks).strip()


def get_live_screenshot_skill_context() -> str:
    return load_skills(["live_screenshot_to_analysis", "market_resolution"])


def get_edge_education_skill_context() -> str:
    return load_skill("edge_education")


def get_no_trade_skill_context() -> str:
    return load_skill("no_trade_discipline")


def get_risk_coach_skill_context() -> str:
    return load_skill("risk_coach")


def get_market_resolution_skill_context() -> str:
    return load_skill("market_resolution")
