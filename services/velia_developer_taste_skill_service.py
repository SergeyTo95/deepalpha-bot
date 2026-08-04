"""Compact design-quality layer for VELIA Coding Agent.

This module is a curated MIT-licensed adaptation of concepts from
https://github.com/Leonxlnx/taste-skill at commit
`e988add20dab0fa97d7a76781c48961c8184288e`.

It intentionally does not vendor the upstream image assets or inject the full
skill text into model prompts. The goal is to preserve the useful design audit,
brief inference, platform consistency, and anti-template rules while keeping
VELIA prompts and inference costs bounded.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List


UPSTREAM_REPOSITORY = "Leonxlnx/taste-skill"
UPSTREAM_COMMIT = "e988add20dab0fa97d7a76781c48961c8184288e"
ADAPTATION_VERSION = "velia-design-taste-v1"

_UI_TEXT_RE = re.compile(
    r"(?:\b(?:ui|ux|frontend|front-end|screen|layout|theme|design|landing|page|"
    r"component|css|scss|tailwind|compose|swiftui|react\s+native|flutter|mobile\s+app|"
    r"interface|responsive|animation|typography|dashboard)\b|"
    r"(?:интерфейс|экран|дизайн|в[её]рстк|стил|компонент|лендинг|страниц|макет|тем[ау]|"
    r"анимац|типограф|адаптив|дашборд|мобильн(?:ое|ого)\s+приложен))",
    re.IGNORECASE,
)
_PATH_UI_RE = re.compile(
    r"(?:^|/)(?:ui|screens?|components?|pages?|layouts?|theme|styles?|design|presentation)(?:/|$)|"
    r"\.(?:tsx|jsx|css|scss|sass|less|html|vue|svelte)$|"
    r"(?:Screen|Activity|Fragment|Composable|View|Theme)\.(?:kt|java|swift)$",
    re.IGNORECASE,
)
_REDESIGN_RE = re.compile(
    r"(?:\b(?:redesign|restyle|refresh|polish|moderni[sz]e|revamp|audit)\b|"
    r"(?:редизайн|переработ|обнови\s+(?:дизайн|интерфейс)|улучши\s+(?:дизайн|интерфейс)|"
    r"осовремени|аудит\s+(?:дизайн|интерфейс)))",
    re.IGNORECASE,
)
_ANDROID_RE = re.compile(r"(?:\bandroid\b|jetpack\s+compose|material\s*3|\.kt\b)", re.IGNORECASE)
_IOS_RE = re.compile(r"(?:\bios\b|swiftui|uikit|\.swift\b)", re.IGNORECASE)
_CROSS_MOBILE_RE = re.compile(r"(?:react\s+native|flutter|cross[- ]platform)", re.IGNORECASE)
_DASHBOARD_RE = re.compile(r"(?:\bdashboard\b|analytics|admin|таблиц|аналитик|админ)", re.IGNORECASE)
_MINIMAL_RE = re.compile(r"(?:minimal|clean|calm|linear[- ]style|минимал|чист|спокойн)", re.IGNORECASE)
_PREMIUM_RE = re.compile(r"(?:premium|luxury|apple[- ]?y|high[- ]end|дорог|премиал|вау)", re.IGNORECASE)
_PLAYFUL_RE = re.compile(r"(?:playful|experimental|awwwards|bold|creative|игрив|эксперимент|креатив)", re.IGNORECASE)
_ACCESSIBILITY_RE = re.compile(r"(?:accessib|a11y|public[- ]sector|regulated|доступност|госсектор|регулируем)", re.IGNORECASE)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return _env_bool("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", True)


def _clamp(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(10, number))


def _path_text(paths: Iterable[str]) -> str:
    return "\n".join(str(path or "") for path in paths)


def _active_for(goal: str, paths: Iterable[str]) -> bool:
    text = str(goal or "")
    path_text = _path_text(paths)
    return bool(_UI_TEXT_RE.search(text) or _PATH_UI_RE.search(path_text))


def classify(goal: str, paths: Iterable[str] = ()) -> Dict[str, Any]:
    text = str(goal or "").strip()
    path_values = [str(path or "") for path in paths]
    combined = f"{text}\n{_path_text(path_values)}"
    if not enabled() or not _active_for(text, path_values):
        return {
            "active": False,
            "version": ADAPTATION_VERSION,
            "source": f"{UPSTREAM_REPOSITORY}@{UPSTREAM_COMMIT}",
        }

    redesign = bool(_REDESIGN_RE.search(combined))
    dashboard = bool(_DASHBOARD_RE.search(combined))
    if _ANDROID_RE.search(combined):
        mode = "mobile-android-redesign" if redesign else "mobile-android"
        platform = "android"
        system = "existing Android UI stack; prefer Material 3/Jetpack Compose conventions when already present"
        variance, motion, density = 6, 4, 5
    elif _IOS_RE.search(combined):
        mode = "mobile-ios-redesign" if redesign else "mobile-ios"
        platform = "ios"
        system = "existing iOS UI stack; preserve SwiftUI/UIKit conventions"
        variance, motion, density = 6, 4, 4
    elif _CROSS_MOBILE_RE.search(combined):
        mode = "mobile-cross-platform-redesign" if redesign else "mobile-cross-platform"
        platform = "cross-platform-mobile"
        system = "existing cross-platform component system with one coherent mobile navigation model"
        variance, motion, density = 6, 4, 4
    elif dashboard:
        mode = "product-dashboard-redesign" if redesign else "product-dashboard"
        platform = "web"
        system = "existing product design system; prioritize hierarchy, states, and data readability"
        variance, motion, density = 5, 3, 7
    else:
        mode = "web-redesign" if redesign else "web-new-ui"
        platform = "web"
        system = "existing frontend stack and design system; add dependencies only after verification"
        variance, motion, density = (6, 4, 4) if redesign else (7, 5, 4)

    vibe = "balanced product"
    if _MINIMAL_RE.search(combined):
        vibe = "restrained and minimal"
        variance -= 1
        motion -= 1
        density -= 1
    if _PREMIUM_RE.search(combined):
        vibe = "premium and deliberate"
        variance += 1
        motion += 1
        density -= 1
    if _PLAYFUL_RE.search(combined):
        vibe = "expressive and art-directed"
        variance += 2
        motion += 2
    if _ACCESSIBILITY_RE.search(combined):
        vibe = "trust-first and accessibility-led"
        variance = min(variance, 4)
        motion = min(motion, 3)
        density = max(density, 4)

    variance = _clamp(variance, 6)
    motion = _clamp(motion, 4)
    density = _clamp(density, 4)
    task_kind = "redesign" if redesign else "new interface work"
    design_read = (
        f"{task_kind} for {platform}, with a {vibe} visual language; "
        f"use {system}."
    )
    return {
        "active": True,
        "version": ADAPTATION_VERSION,
        "source": f"{UPSTREAM_REPOSITORY}@{UPSTREAM_COMMIT}",
        "mode": mode,
        "platform": platform,
        "audit_first": redesign,
        "design_read": design_read[:500],
        "design_variance": variance,
        "motion_intensity": motion,
        "visual_density": density,
        "system": system[:300],
    }


def normalize_design(raw: Any, fallback: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(fallback, dict) or not fallback.get("active"):
        return {}
    value = raw if isinstance(raw, dict) else {}
    return {
        "active": True,
        "version": ADAPTATION_VERSION,
        "source": str(fallback.get("source") or f"{UPSTREAM_REPOSITORY}@{UPSTREAM_COMMIT}"),
        "mode": str(value.get("mode") or fallback.get("mode") or "web-new-ui")[:80],
        "platform": str(value.get("platform") or fallback.get("platform") or "web")[:80],
        "audit_first": bool(fallback.get("audit_first")),
        "read": str(value.get("read") or value.get("design_read") or fallback.get("design_read") or "")[:600],
        "system": str(value.get("system") or fallback.get("system") or "existing project stack")[:400],
        "variance": _clamp(value.get("variance"), int(fallback.get("design_variance") or 6)),
        "motion": _clamp(value.get("motion"), int(fallback.get("motion_intensity") or 4)),
        "density": _clamp(value.get("density"), int(fallback.get("visual_density") or 4)),
    }


def preflight_checks(profile: Dict[str, Any]) -> List[str]:
    if not isinstance(profile, dict) or not profile.get("active"):
        return []
    checks = [
        "Verify every imported UI dependency exists in the project dependency manifest.",
        "Verify responsive behavior, overflow, and touch targets at small and large viewports.",
        "Verify visible focus states, semantic labels, readable contrast, and meaningful image alt text.",
        "Verify loading, empty, error, disabled, pressed, and selected states affected by the change.",
        "Verify motion uses transform/opacity where possible and respects reduced-motion preferences.",
    ]
    platform = str(profile.get("platform") or "")
    if platform == "android":
        checks.append("Verify Android safe areas/insets, back navigation, and Material interaction states.")
    elif platform == "ios":
        checks.append("Verify iOS safe areas, navigation hierarchy, and native sheet/tab behavior.")
    elif platform == "cross-platform-mobile":
        checks.append("Verify one coherent mobile navigation model and platform-safe spacing on both targets.")
    else:
        checks.append("Verify full-height sections use stable dynamic viewport units and no horizontal overflow.")
    if profile.get("audit_first"):
        checks.append("Verify the redesign preserves existing behavior and does not replace the framework or styling stack.")
    return checks[:8]


def planning_guidance(profile: Dict[str, Any]) -> str:
    if not isinstance(profile, dict) or not profile.get("active"):
        return ""
    checks = preflight_checks(profile)
    audit = (
        "Start with an audit step: identify the existing framework, styling system, tokens, navigation, assets, "
        "dependencies, repeated components, and weak/missing states. Apply targeted improvements; do not rewrite from scratch."
        if profile.get("audit_first")
        else "Inspect the existing stack, dependencies, brand assets, and reusable components before proposing implementation steps."
    )
    platform = str(profile.get("platform") or "web")
    mobile_rule = ""
    if platform in {"android", "ios", "cross-platform-mobile"}:
        mobile_rule = (
            "Keep the result app-native: preserve safe areas, readable type, coherent navigation, touch targets, and state logic. "
            "Do not squeeze a desktop website into a phone layout or mix iOS and Android patterns."
        )
    return f"""VELIA DESIGN TASTE (curated MIT adaptation; no extra model call)
Design read: {profile.get('design_read')}
Dials: VARIANCE={profile.get('design_variance')}/10, MOTION={profile.get('motion_intensity')}/10, DENSITY={profile.get('visual_density')}/10.
Foundation: {profile.get('system')}
Planning rules:
- {audit}
- Preserve functionality, public contracts, current framework, and current styling architecture unless the request explicitly requires migration.
- Avoid generic AI defaults: automatic purple/blue glow, centered hero plus three equal cards, excessive pills/glass, random gradients, tiny mobile text, and decorative motion without purpose.
- Choose one coherent visual system. Reuse existing tokens/components first. Check dependency manifests before naming or importing a package.
- Plan typography, spacing, hierarchy, interaction states, responsiveness, accessibility, and content together; visual polish is not a separate afterthought.
- {mobile_rule or 'Use semantic layout, stable dynamic viewport units, clear hierarchy, and intentional responsive behavior.'}
- Add concrete checks: {json_list(checks)}
Return a `design` object in the plan JSON with mode, platform, read, system, variance, motion, and density.
""".strip()


def execution_guidance(profile: Dict[str, Any], step: Dict[str, Any]) -> str:
    if not isinstance(profile, dict) or not profile.get("active"):
        return ""
    checks = preflight_checks(profile)
    platform = str(profile.get("platform") or "web")
    platform_rule = {
        "android": "Follow the repository's Android/Compose/Material patterns, window insets, back behavior, and minimum touch targets.",
        "ios": "Follow the repository's SwiftUI/UIKit patterns, safe areas, native navigation, sheets, and controls.",
        "cross-platform-mobile": "Keep one coherent mobile component and navigation model; avoid platform-pattern mixing.",
    }.get(platform, "Use semantic markup/layout, stable responsive containers, and avoid horizontal overflow.")
    return f"""DESIGN EXECUTION GUARD
Mode: {profile.get('mode')}; read: {profile.get('read') or profile.get('design_read')}.
Dials: VARIANCE={profile.get('variance') or profile.get('design_variance')}, MOTION={profile.get('motion') or profile.get('motion_intensity')}, DENSITY={profile.get('density') or profile.get('visual_density')}.
- Work only inside this task's allowed files and preserve the existing visual system unless the plan explicitly changes it.
- {platform_rule}
- Reuse verified dependencies and components; never hallucinate an import. Do not add a library merely to achieve a small CSS or native-layout effect.
- Remove template-looking repetition, but do not sacrifice clarity, accessibility, performance, or product consistency for novelty.
- Implement complete interaction states affected by this task. Motion must be purposeful and reduced-motion safe.
- Before returning the patch, self-check: {json_list(checks)}
""".strip()


def format_summary(profile: Dict[str, Any], *, russian: bool = True) -> List[str]:
    if not isinstance(profile, dict) or not profile.get("active"):
        return []
    read = str(profile.get("read") or profile.get("design_read") or "")
    variance = profile.get("variance") or profile.get("design_variance")
    motion = profile.get("motion") or profile.get("motion_intensity")
    density = profile.get("density") or profile.get("visual_density")
    if russian:
        return [
            "### Design direction",
            read,
            f"Режим: `{profile.get('mode')}` · вариативность {variance}/10 · движение {motion}/10 · плотность {density}/10",
        ]
    return [
        "### Design direction",
        read,
        f"Mode: `{profile.get('mode')}` · variance {variance}/10 · motion {motion}/10 · density {density}/10",
    ]


def json_list(values: Iterable[str]) -> str:
    return "[" + "; ".join(str(value) for value in values if str(value).strip()) + "]"
