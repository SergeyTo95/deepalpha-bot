from pathlib import Path


coding_path = Path("services/velia_developer_coding_service.py")
source = coding_path.read_text(encoding="utf-8")

helpers = '''def _bounded_design_text(
    value: str,
    profile: Dict[str, Any],
    *,
    env_name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> str:
    text = str(value or "")
    if not isinstance(profile, dict) or not profile.get("active"):
        return text
    limit = _env_int(env_name, default, minimum, maximum)
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    boundary = clipped.rfind("\\n")
    if boundary >= max(1, limit // 2):
        clipped = clipped[:boundary]
    return clipped


def _design_plan_evidence(evidence: str, profile: Dict[str, Any]) -> str:
    return _bounded_design_text(
        evidence,
        profile,
        env_name="VELIA_DEVELOPER_TASTE_PLAN_EVIDENCE_CHARS",
        default=10000,
        minimum=4000,
        maximum=16000,
    )


def _design_step_context(context: str, profile: Dict[str, Any]) -> str:
    return _bounded_design_text(
        context,
        profile,
        env_name="VELIA_DEVELOPER_TASTE_STEP_CONTEXT_CHARS",
        default=17000,
        minimum=8000,
        maximum=24000,
    )'''

if "def _design_plan_evidence(" not in source:
    marker = "\n\ndef plan_job("
    if marker not in source:
        raise SystemExit("plan_job insertion marker missing")
    source = source.replace(marker, "\n\n" + helpers + marker, 1)

old_plan = '''    design_profile = taste_skill.classify(normalized_goal, paths)
    prompt = _plan_prompt(
'''
new_plan = '''    design_profile = taste_skill.classify(normalized_goal, paths)
    evidence = _design_plan_evidence(evidence, design_profile)
    prompt = _plan_prompt(
'''
if old_plan not in source:
    raise SystemExit("design plan evidence marker missing")
source = source.replace(old_plan, new_plan, 1)

old_step = '''    context, states = _step_context(project, str(job["work_branch"]), step, str(job["goal"]))
    prompt = _step_prompt(project, job, step, context)
'''
new_step = '''    context, states = _step_context(project, str(job["work_branch"]), step, str(job["goal"]))
    plan = job.get("plan") if isinstance(job.get("plan"), dict) else {}
    design = plan.get("design") if isinstance(plan.get("design"), dict) else {}
    context = _design_step_context(context, design)
    prompt = _step_prompt(project, job, step, context)
'''
if old_step not in source:
    raise SystemExit("design step context marker missing")
source = source.replace(old_step, new_step, 1)

coding_path.write_text(source, encoding="utf-8")

taste_path = Path("services/velia_developer_taste_skill_service.py")
taste_source = taste_path.read_text(encoding="utf-8")
taste_source = taste_source.replace(
    '        "mode": str(value.get("mode") or fallback.get("mode") or "web-new-ui")[:80],\n'
    '        "platform": str(value.get("platform") or fallback.get("platform") or "web")[:80],\n',
    '        "mode": str(fallback.get("mode") or "web-new-ui")[:80],\n'
    '        "platform": str(fallback.get("platform") or "web")[:80],\n',
    1,
)
taste_source = taste_source.replace(
    '            "### Design direction",\n',
    '            "### Направление дизайна",\n',
    1,
)
taste_path.write_text(taste_source, encoding="utf-8")
print("VELIA Design Taste cost hardening applied")
