from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from services.velia_software_factory_core_service import (
    FactoryTask,
    ProjectSpec,
    SoftwareFactoryError,
    TaskDAG,
)


ROLE_POLICIES: Dict[str, Dict[str, Any]] = {
    "architect": {
        "title": "Software Architect",
        "mission": "Define system boundaries, interfaces, data ownership, integration points and quality risks before implementation.",
        "writes_code": False,
    },
    "planner": {
        "title": "Technical Planner",
        "mission": "Turn the approved architecture and product objective into a dependency-safe execution DAG with bounded scopes.",
        "writes_code": False,
    },
    "designer": {
        "title": "Product Designer",
        "mission": "Define screens, interaction hierarchy, states, accessibility and implementation-ready UX constraints without inventing product scope.",
        "writes_code": False,
    },
    "backend": {
        "title": "Backend Engineer",
        "mission": "Implement server-side behavior, APIs, data access and integrations while preserving compatibility and security boundaries.",
        "writes_code": True,
    },
    "frontend": {
        "title": "Frontend Engineer",
        "mission": "Implement web UI, client state and accessibility while respecting existing design-system and API contracts.",
        "writes_code": True,
    },
    "android": {
        "title": "Android Engineer",
        "mission": "Implement Android/Kotlin/Compose behavior with lifecycle-safe state, resilient networking and platform conventions.",
        "writes_code": True,
    },
    "qa": {
        "title": "QA Automation Engineer",
        "mission": "Add or strengthen deterministic tests and acceptance checks that prove the requested behavior and regressions.",
        "writes_code": True,
    },
    "security": {
        "title": "Security Engineer",
        "mission": "Harden authentication, authorization, validation, secret handling and abuse boundaries without weakening existing controls.",
        "writes_code": True,
    },
    "devops": {
        "title": "DevOps Engineer",
        "mission": "Adjust CI, runtime configuration and deployment-safe automation only inside explicitly allowed repository paths.",
        "writes_code": True,
    },
    "fullstack": {
        "title": "Full-stack Engineer",
        "mission": "Implement a bounded cross-layer change when splitting frontend/backend would create unnecessary coordination overhead.",
        "writes_code": True,
    },
    "reviewer": {
        "title": "Senior Reviewer",
        "mission": "Challenge correctness, regressions, security and acceptance evidence. Repository review remains owned by Coding Autopilot.",
        "writes_code": False,
    },
}

_EXECUTION_ROLES = tuple(
    key for key, value in ROLE_POLICIES.items() if bool(value.get("writes_code"))
)
_UI_HINTS = {
    "ui", "ux", "screen", "page", "layout", "design", "frontend", "webapp", "website",
    "android", "compose", "kotlin", "mobile", "button", "form", "dashboard", "store", "shop",
}
_SECURITY_HINTS = {
    "auth", "oauth", "token", "secret", "permission", "security", "password", "session", "jwt",
}
_DEVOPS_HINTS = {
    "docker", "railway", "deploy", "deployment", "ci", "workflow", "github actions", "infra", "runtime",
}
_QA_HINTS = {"test", "tests", "pytest", "acceptance", "regression", "qa", "instrumentation"}
_ANDROID_HINTS = {"android", "kotlin", "compose", "gradle", "viewmodel", "activity", "fragment"}
_FRONTEND_HINTS = {"frontend", "react", "typescript", "javascript", "css", "html", "webapp", "web ui", "page"}
_BACKEND_HINTS = {"backend", "api", "database", "postgres", "service", "python", "fastapi", "aiohttp", "worker"}


def team_enabled() -> bool:
    raw = str(os.getenv("VELIA_SOFTWARE_FACTORY_TEAM_ENABLED", "false") or "false").strip().lower()
    return raw in {"1", "true", "yes", "on", "enabled"}


def role_catalog() -> List[Dict[str, Any]]:
    return [
        {"id": role, **dict(policy)}
        for role, policy in ROLE_POLICIES.items()
    ]


def _text(value: Any, limit: int = 6000) -> str:
    return str(value or "").strip()[:limit]


def _str_list(value: Any, *, limit: int = 50, item_limit: int = 1000) -> List[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: List[str] = []
    for raw in values:
        item = _text(raw, item_limit)
        if item and item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def _compact(value: Any, limit: int = 18000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)[:limit]


def _slug(value: Any, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", _text(value, 120).lower()).strip("-_")
    return (normalized or fallback)[:80]


def _extract_json_object(raw: str) -> Dict[str, Any]:
    text = _text(raw, 50000)
    if not text:
        raise SoftwareFactoryError("velia_factory_team_llm_empty", status=502)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise SoftwareFactoryError("velia_factory_team_json_invalid", status=502)
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception as exc:
        raise SoftwareFactoryError("velia_factory_team_json_invalid", status=502) from exc
    if not isinstance(parsed, dict):
        raise SoftwareFactoryError("velia_factory_team_json_invalid", status=502)
    return parsed


def _default_generator(feature: str, user_id: int, run_id: str, max_tokens: int) -> Callable[[str], str]:
    def generate(prompt: str) -> str:
        from services import llm_service

        return llm_service._call_gemini(  # centralized provider gateway + budget guard
            prompt,
            max_tokens=max_tokens,
            feature=feature,
            user_id=int(user_id),
            is_background=False,
            request_id=str(run_id),
            cycle_id=str(run_id),
            job_id=str(run_id),
            origin="software_factory_team",
        )

    return generate


def _contains_any(text: str, hints: Iterable[str]) -> bool:
    normalized = text.lower()
    return any(hint in normalized for hint in hints)


def infer_role(title: str, goal: str, paths: Sequence[str]) -> str:
    haystack = " ".join([title, goal, *paths]).lower()
    if _contains_any(haystack, _ANDROID_HINTS):
        return "android"
    if _contains_any(haystack, _SECURITY_HINTS):
        return "security"
    if _contains_any(haystack, _DEVOPS_HINTS):
        return "devops"
    if _contains_any(haystack, _QA_HINTS):
        return "qa"
    frontend = _contains_any(haystack, _FRONTEND_HINTS)
    backend = _contains_any(haystack, _BACKEND_HINTS)
    if frontend and backend:
        return "fullstack"
    if frontend:
        return "frontend"
    if backend:
        return "backend"
    for path in paths:
        low = path.lower()
        if "android" in low or low.endswith((".kt", ".kts")):
            return "android"
        if low.endswith((".tsx", ".ts", ".jsx", ".js", ".css", ".html")):
            return "frontend"
        if low.endswith(".py") or "service" in low or "api" in low:
            return "backend"
    return "fullstack"


def design_required(spec: ProjectSpec) -> bool:
    text = " ".join([spec.title, spec.objective, *spec.allowed_paths]).lower()
    return _contains_any(text, _UI_HINTS)


def _permitted_paths(spec: ProjectSpec) -> List[str]:
    result = list(spec.allowed_paths)
    for item in spec.deliverables:
        for path in _str_list(item.get("allowed_paths"), limit=100, item_limit=500):
            if path not in result:
                result.append(path)
    return result


def _filter_paths(raw_paths: Any, permitted: Sequence[str], blocked: Sequence[str]) -> List[str]:
    requested = _str_list(raw_paths, limit=100, item_limit=500)
    permitted_set = set(permitted)
    blocked_set = set(blocked)
    if not requested:
        return list(permitted)
    return [path for path in requested if path in permitted_set and path not in blocked_set]


def _brain_context(brain: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for raw in list(brain)[-80:]:
        if not isinstance(raw, Mapping):
            continue
        text = _text(raw.get("text"), 1400)
        if not text:
            continue
        result.append(
            {
                "kind": _text(raw.get("kind"), 80),
                "text": text,
                "source": _text(raw.get("source"), 120),
                "confidence": raw.get("confidence", 1.0),
            }
        )
    return result


def _fallback_architecture(spec: ProjectSpec, reason: str = "") -> Dict[str, Any]:
    components: List[Dict[str, Any]] = []
    if spec.deliverables:
        for index, item in enumerate(spec.deliverables[:20]):
            paths = _str_list(item.get("allowed_paths"), limit=50, item_limit=500) or list(spec.allowed_paths)
            components.append(
                {
                    "id": _slug(item.get("id"), f"component-{index + 1}"),
                    "name": _text(item.get("title") or item.get("id"), 160) or f"Component {index + 1}",
                    "responsibility": _text(item.get("goal") or item.get("objective"), 1500),
                    "owner_role": infer_role(_text(item.get("title")), _text(item.get("goal")), paths),
                    "paths": paths,
                    "interfaces": [],
                    "depends_on": _str_list(item.get("depends_on"), limit=30, item_limit=80),
                }
            )
    else:
        components.append(
            {
                "id": "implementation",
                "name": spec.title or "Implementation",
                "responsibility": spec.objective,
                "owner_role": infer_role(spec.title, spec.objective, list(spec.allowed_paths)),
                "paths": list(spec.allowed_paths),
                "interfaces": [],
                "depends_on": [],
            }
        )
    gates = list(spec.acceptance_criteria) or ["Existing repository tests and CI remain green."]
    return {
        "mode": "deterministic_fallback",
        "reason": reason,
        "summary": spec.objective,
        "components": components,
        "decisions": [],
        "risks": [],
        "quality_gates": gates,
        "assumptions": [],
    }


def _normalize_architecture(raw: Mapping[str, Any], spec: ProjectSpec) -> Dict[str, Any]:
    permitted = _permitted_paths(spec)
    blocked = list(spec.blocked_paths)
    components: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw.get("components") or []):
        if not isinstance(item, Mapping) or len(components) >= 20:
            continue
        component_id = _slug(item.get("id") or item.get("name"), f"component-{index + 1}")
        if component_id in seen:
            continue
        seen.add(component_id)
        paths = _filter_paths(item.get("paths"), permitted, blocked)
        role = _text(item.get("owner_role"), 40).lower()
        if role not in _EXECUTION_ROLES:
            role = infer_role(_text(item.get("name")), _text(item.get("responsibility")), paths)
        components.append(
            {
                "id": component_id,
                "name": _text(item.get("name"), 160) or component_id,
                "responsibility": _text(item.get("responsibility"), 1800),
                "owner_role": role,
                "paths": paths,
                "interfaces": _str_list(item.get("interfaces"), limit=20, item_limit=500),
                "depends_on": _str_list(item.get("depends_on"), limit=20, item_limit=80),
            }
        )
    if not components:
        return _fallback_architecture(spec, "architecture_components_missing")

    risks: List[Dict[str, Any]] = []
    for index, item in enumerate(raw.get("risks") or []):
        if not isinstance(item, Mapping) or len(risks) >= 20:
            continue
        risks.append(
            {
                "id": _slug(item.get("id"), f"risk-{index + 1}"),
                "severity": _text(item.get("severity"), 20).lower() or "medium",
                "description": _text(item.get("description"), 1000),
                "mitigation": _text(item.get("mitigation"), 1000),
            }
        )
    quality_gates = _str_list(raw.get("quality_gates"), limit=30, item_limit=1200)
    for gate in spec.acceptance_criteria:
        if gate not in quality_gates:
            quality_gates.append(gate)
    if not quality_gates:
        quality_gates.append("Existing repository tests and CI remain green.")
    return {
        "mode": "llm",
        "summary": _text(raw.get("summary"), 3000) or spec.objective,
        "components": components,
        "decisions": _str_list(raw.get("decisions"), limit=30, item_limit=1200),
        "risks": risks,
        "quality_gates": quality_gates,
        "assumptions": _str_list(raw.get("assumptions"), limit=30, item_limit=1200),
    }


def build_architecture_plan(
    spec: ProjectSpec,
    brain: Sequence[Mapping[str, Any]],
    *,
    repository: str,
    branch: str,
    user_id: int,
    run_id: str,
    generator: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    prompt = (
        "You are VELIA Software Factory's Software Architect. Return ONLY one valid JSON object. "
        "Do not write code. Do not widen repository write scope and do not invent external services, credentials or product requirements. "
        "Design the smallest architecture that satisfies the objective and acceptance criteria.\n\n"
        "JSON schema:\n"
        '{"summary":"...","components":[{"id":"...","name":"...","responsibility":"...","owner_role":"backend|frontend|android|qa|security|devops|fullstack","paths":["..."],"interfaces":["..."],"depends_on":["..."]}],"decisions":["..."],"risks":[{"id":"...","severity":"low|medium|high","description":"...","mitigation":"..."}],"quality_gates":["..."],"assumptions":["..."]}\n\n'
        f"Repository: {repository}\nBase branch: {branch}\n"
        f"ProjectSpec: {_compact(spec.to_dict())}\n"
        f"Project Brain: {_compact(_brain_context(brain))}"
    )
    generate = generator or _default_generator("software_factory_architect", user_id, run_id, 2200)
    try:
        return _normalize_architecture(_extract_json_object(generate(prompt)), spec)
    except Exception as exc:
        return _fallback_architecture(spec, type(exc).__name__)


def _fallback_design(spec: ProjectSpec, architecture: Mapping[str, Any], reason: str = "") -> Dict[str, Any]:
    return {
        "required": design_required(spec),
        "mode": "deterministic_fallback",
        "reason": reason,
        "visual_direction": "Follow the repository's existing design system and interaction patterns.",
        "screens": [],
        "components": [],
        "states": ["loading", "empty", "error", "success"] if design_required(spec) else [],
        "accessibility": ["Preserve keyboard/screen-reader semantics and sufficient contrast."],
        "implementation_constraints": ["Do not introduce a second design system when an existing one is present."],
    }


def build_design_brief(
    spec: ProjectSpec,
    architecture: Mapping[str, Any],
    brain: Sequence[Mapping[str, Any]],
    *,
    user_id: int,
    run_id: str,
    generator: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    if not design_required(spec):
        return {"required": False, "mode": "not_required", "screens": [], "components": [], "states": []}
    prompt = (
        "You are VELIA Software Factory's Product Designer. Return ONLY one valid JSON object. "
        "Create an implementation-ready UX brief for the requested scope. Preserve the repository's existing design language; do not invent new product features.\n\n"
        "JSON schema:\n"
        '{"visual_direction":"...","screens":[{"name":"...","purpose":"...","primary_actions":["..."],"states":["..."]}],"components":["..."],"states":["loading","empty","error","success"],"accessibility":["..."],"implementation_constraints":["..."]}\n\n'
        f"ProjectSpec: {_compact(spec.to_dict())}\nArchitecture: {_compact(architecture)}\nProject Brain: {_compact(_brain_context(brain))}"
    )
    generate = generator or _default_generator("software_factory_designer", user_id, run_id, 1800)
    try:
        raw = _extract_json_object(generate(prompt))
        screens: List[Dict[str, Any]] = []
        for item in raw.get("screens") or []:
            if isinstance(item, Mapping) and len(screens) < 20:
                screens.append(
                    {
                        "name": _text(item.get("name"), 160),
                        "purpose": _text(item.get("purpose"), 900),
                        "primary_actions": _str_list(item.get("primary_actions"), limit=15, item_limit=300),
                        "states": _str_list(item.get("states"), limit=15, item_limit=120),
                    }
                )
        return {
            "required": True,
            "mode": "llm",
            "visual_direction": _text(raw.get("visual_direction"), 1400),
            "screens": screens,
            "components": _str_list(raw.get("components"), limit=40, item_limit=400),
            "states": _str_list(raw.get("states"), limit=20, item_limit=120),
            "accessibility": _str_list(raw.get("accessibility"), limit=30, item_limit=600),
            "implementation_constraints": _str_list(raw.get("implementation_constraints"), limit=30, item_limit=800),
        }
    except Exception as exc:
        return _fallback_design(spec, architecture, type(exc).__name__)


def _role_brief(role: str) -> str:
    policy = ROLE_POLICIES.get(role) or ROLE_POLICIES["fullstack"]
    return f"Role: {policy['title']}. Responsibility: {policy['mission']}"


def _enrich_goal(
    *,
    role: str,
    title: str,
    goal: str,
    allowed_paths: Sequence[str],
    acceptance: Sequence[str],
    architecture: Mapping[str, Any],
    design: Mapping[str, Any],
) -> str:
    sections = [
        _role_brief(role),
        f"Task: {title}",
        f"Goal: {goal}",
        "Stay strictly inside these allowed paths:\n- " + "\n- ".join(allowed_paths),
    ]
    if acceptance:
        sections.append("Task acceptance criteria:\n- " + "\n- ".join(acceptance))
    architecture_summary = _text(architecture.get("summary"), 1800)
    if architecture_summary:
        sections.append("Architecture contract:\n" + architecture_summary)
    if bool(design.get("required")):
        direction = _text(design.get("visual_direction"), 1000)
        if direction:
            sections.append("Design direction:\n" + direction)
        constraints = _str_list(design.get("implementation_constraints"), limit=20, item_limit=600)
        if constraints:
            sections.append("Design constraints:\n- " + "\n- ".join(constraints))
    sections.append("Do not merge or deploy. Produce a review-ready change with evidence from relevant tests/checks.")
    return "\n\n".join(section for section in sections if section)[:12000]


def _fallback_plan(spec: ProjectSpec, architecture: Mapping[str, Any], design: Mapping[str, Any], reason: str = "") -> Dict[str, Any]:
    dag = TaskDAG.from_spec(spec)
    for task in dag.tasks.values():
        role = infer_role(task.title, task.goal, task.allowed_paths)
        acceptance = list(spec.acceptance_criteria)
        raw_goal = task.goal
        task.goal = _enrich_goal(
            role=role,
            title=task.title,
            goal=raw_goal,
            allowed_paths=task.allowed_paths,
            acceptance=acceptance,
            architecture=architecture,
            design=design,
        )
        task.result = {
            "role": role,
            "raw_goal": raw_goal,
            "acceptance_criteria": acceptance,
            "planner_mode": "deterministic_fallback",
            "planner_reason": reason,
        }
    return {
        "mode": "deterministic_fallback",
        "reason": reason,
        "tasks": dag.snapshot(),
        "parallelism": 1,
        "notes": [],
    }


def _normalize_plan(raw: Mapping[str, Any], spec: ProjectSpec, architecture: Mapping[str, Any], design: Mapping[str, Any]) -> Dict[str, Any]:
    permitted = _permitted_paths(spec)
    blocked = list(spec.blocked_paths)
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise SoftwareFactoryError("velia_factory_team_plan_empty", status=502)

    staged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_tasks[:16]):
        if not isinstance(item, Mapping):
            continue
        task_id = _slug(item.get("id"), f"task-{index + 1}")
        if task_id in seen:
            raise SoftwareFactoryError("velia_factory_team_task_duplicate", detail=task_id, status=502)
        seen.add(task_id)
        title = _text(item.get("title"), 240) or task_id
        raw_goal = _text(item.get("goal"), 5000) or title
        paths = _filter_paths(item.get("allowed_paths"), permitted, blocked)
        if not paths:
            paths = list(permitted)
        role = _text(item.get("role"), 40).lower()
        if role not in _EXECUTION_ROLES:
            role = infer_role(title, raw_goal, paths)
        acceptance = _str_list(item.get("acceptance_criteria"), limit=20, item_limit=1200)
        if not acceptance:
            acceptance = list(spec.acceptance_criteria)
        staged.append(
            {
                "id": task_id,
                "title": title,
                "raw_goal": raw_goal,
                "role": role,
                "paths": paths,
                "acceptance": acceptance,
                "depends_on": _str_list(item.get("depends_on"), limit=30, item_limit=80),
            }
        )
    if not staged:
        raise SoftwareFactoryError("velia_factory_team_plan_empty", status=502)

    valid_ids = {item["id"] for item in staged}
    tasks: List[FactoryTask] = []
    for item in staged:
        dependencies = [dep for dep in item["depends_on"] if dep in valid_ids and dep != item["id"]]
        goal = _enrich_goal(
            role=item["role"],
            title=item["title"],
            goal=item["raw_goal"],
            allowed_paths=item["paths"],
            acceptance=item["acceptance"],
            architecture=architecture,
            design=design,
        )
        tasks.append(
            FactoryTask(
                task_id=item["id"],
                title=item["title"],
                goal=goal,
                kind="coding",
                depends_on=dependencies,
                allowed_paths=item["paths"],
                result={
                    "role": item["role"],
                    "raw_goal": item["raw_goal"],
                    "acceptance_criteria": item["acceptance"],
                    "planner_mode": "llm",
                },
            )
        )
    dag = TaskDAG(tasks)
    try:
        parallelism = max(1, min(6, int(raw.get("parallelism") or 1)))
    except (TypeError, ValueError):
        parallelism = 1
    return {
        "mode": "llm",
        "tasks": dag.snapshot(),
        "parallelism": parallelism,
        "notes": _str_list(raw.get("notes"), limit=20, item_limit=1000),
    }


def build_team_plan(
    spec: ProjectSpec,
    architecture: Mapping[str, Any],
    design: Mapping[str, Any],
    brain: Sequence[Mapping[str, Any]],
    *,
    user_id: int,
    run_id: str,
    generator: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    prompt = (
        "You are VELIA Software Factory's Technical Planner. Return ONLY one valid JSON object. "
        "Create the smallest dependency-safe coding DAG that can be executed by specialist engineers. "
        "Every task must stay inside the explicit allowed paths. Never add merge/deploy steps. "
        "Use only these roles: backend, frontend, android, qa, security, devops, fullstack. "
        "Prefer independent tasks in parallel only when they do not need each other's outputs.\n\n"
        "JSON schema:\n"
        '{"tasks":[{"id":"...","title":"...","goal":"...","role":"backend|frontend|android|qa|security|devops|fullstack","depends_on":["..."],"allowed_paths":["..."],"acceptance_criteria":["..."]}],"parallelism":1,"notes":["..."]}\n\n'
        f"ProjectSpec: {_compact(spec.to_dict())}\n"
        f"Architecture: {_compact(architecture)}\nDesign brief: {_compact(design)}\nProject Brain: {_compact(_brain_context(brain))}"
    )
    generate = generator or _default_generator("software_factory_planner", user_id, run_id, 2600)
    try:
        return _normalize_plan(_extract_json_object(generate(prompt)), spec, architecture, design)
    except Exception as exc:
        return _fallback_plan(spec, architecture, design, type(exc).__name__)


def build_team_bundle(
    spec: ProjectSpec,
    brain: Sequence[Mapping[str, Any]],
    *,
    repository: str,
    branch: str,
    user_id: int,
    run_id: str,
    architect_generator: Optional[Callable[[str], str]] = None,
    designer_generator: Optional[Callable[[str], str]] = None,
    planner_generator: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    architecture = build_architecture_plan(
        spec,
        brain,
        repository=repository,
        branch=branch,
        user_id=user_id,
        run_id=run_id,
        generator=architect_generator,
    )
    design = build_design_brief(
        spec,
        architecture,
        brain,
        user_id=user_id,
        run_id=run_id,
        generator=designer_generator,
    )
    plan = build_team_plan(
        spec,
        architecture,
        design,
        brain,
        user_id=user_id,
        run_id=run_id,
        generator=planner_generator,
    )
    used_roles = sorted(
        {
            str((item.get("result") or {}).get("role") or "fullstack")
            for item in plan.get("tasks") or []
            if isinstance(item, Mapping)
        }
    )
    manifest = {
        "meta_agents": ["architect", "planner"] + (["designer"] if bool(design.get("required")) else []),
        "execution_roles": used_roles,
        "review_owner": "coding_autopilot",
        "write_owner": "coding_autopilot",
        "parallelism": int(plan.get("parallelism") or 1),
    }
    brain_entries: List[Dict[str, Any]] = []
    if _text(architecture.get("summary")):
        brain_entries.append({"kind": "architecture", "text": _text(architecture.get("summary"), 3000), "source": "architect", "confidence": 0.9})
    for decision in _str_list(architecture.get("decisions"), limit=30, item_limit=1200):
        brain_entries.append({"kind": "architecture_decision", "text": decision, "source": "architect", "confidence": 0.9})
    for assumption in _str_list(architecture.get("assumptions"), limit=30, item_limit=1200):
        brain_entries.append({"kind": "assumption", "text": assumption, "source": "architect", "confidence": 0.65})
    if bool(design.get("required")) and _text(design.get("visual_direction")):
        brain_entries.append({"kind": "design_direction", "text": _text(design.get("visual_direction"), 1800), "source": "designer", "confidence": 0.85})
    return {
        "architecture": architecture,
        "design": design,
        "plan": plan,
        "manifest": manifest,
        "brain_entries": brain_entries,
    }
