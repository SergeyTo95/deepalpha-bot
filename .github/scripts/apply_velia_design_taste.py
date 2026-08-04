from pathlib import Path


SERVICE_PATH = Path("services/velia_developer_coding_service.py")


def replace_block(source: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = source.find(start_marker)
    if start < 0:
        raise SystemExit(f"start marker missing: {start_marker}")
    end = source.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"end marker missing: {end_marker}")
    return source[:start] + replacement.rstrip() + "\n\n" + source[end + 2 :]


source = SERVICE_PATH.read_text(encoding="utf-8")

import_marker = "from services import velia_developer_github_write_service as write_service\n"
import_replacement = (
    import_marker
    + "from services import velia_developer_taste_skill_service as taste_skill\n"
)
if "velia_developer_taste_skill_service as taste_skill" not in source:
    if import_marker not in source:
        raise SystemExit("coding service import marker missing")
    source = source.replace(import_marker, import_replacement, 1)

plan_prompt = '''def _plan_prompt(
    project: Dict[str, Any],
    goal: str,
    paths: List[str],
    evidence: str,
    *,
    taste_profile: Optional[Dict[str, Any]] = None,
) -> str:
    repository = str(project.get("repository_full_name") or "")
    branch = str(project.get("selected_branch") or "")
    profile = taste_profile if isinstance(taste_profile, dict) else {}
    taste_guidance = taste_skill.planning_guidance(profile)
    guidance_block = f"\\n\\n{taste_guidance}" if taste_guidance else ""
    design_schema = ""
    design_rule = ""
    if profile.get("active"):
        design_schema = (
            '  "design": {"mode":"web-redesign|web-new-ui|mobile-android|mobile-ios|mobile-cross-platform|product-dashboard",'
            '"platform":"web|android|ios|cross-platform-mobile",'
            '"read":"one concise design read","system":"existing or verified design foundation",'
            '"variance":1,"motion":1,"density":1},\\n'
        )
        design_rule = (
            "- Follow VELIA DESIGN TASTE. Include the design object, keep its dials between 1 and 10, "
            "and make the first step an audit when audit-first guidance is present."
        )
    return f"""You are the planning stage of VELIA Coding Agent.
Create a small, ordered implementation plan for the user's request.
Repository: {repository}
Base branch: {branch}
User request:
{goal}

Candidate paths:
{json.dumps(paths[:20], ensure_ascii=False)}

Verified repository excerpts:
{evidence}{guidance_block}

Return ONLY one compact JSON object with this schema:
{{
{design_schema}  "title": "short PR title",
  "summary": "what will be changed and why",
  "steps": [
    {{
      "title": "small task title",
      "objective": "one concrete outcome",
      "files": ["repository/path.ext"],
      "checks": ["specific validation"]
    }}
  ],
  "suggestions": ["optional follow-up improvement"]
}}
Rules:
- 1 to 6 ordered steps.
- Each step must be independently committable.
- Use only repository-relative paths.
- Include tests in the same step as the behavior they verify, or in the immediately following step.
- Do not propose direct writes to the base branch, merging, secrets, credentials, .env files, or production deployment.
- Prefer the smallest safe change.
{design_rule}
- No markdown outside JSON.
"""'''
source = replace_block(source, "def _plan_prompt(", "\n\ndef _normalize_plan", plan_prompt)

normalize_plan = '''def _normalize_plan(
    value: Dict[str, Any],
    *,
    design_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    raw_steps = value.get("steps") if isinstance(value, dict) else []
    if not isinstance(raw_steps, list):
        raw_steps = []
    maximum = _env_int("VELIA_DEVELOPER_CODING_MAX_STEPS", 5, 1, 8)
    steps: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_steps[:maximum], start=1):
        if not isinstance(raw, dict):
            continue
        files: List[str] = []
        seen = set()
        for path in raw.get("files") if isinstance(raw.get("files"), list) else []:
            try:
                normalized = github_service.validate_path(str(path or ""))
            except github_service.DeveloperGithubError:
                continue
            if normalized not in seen:
                seen.add(normalized)
                files.append(normalized)
        checks = [
            str(item).strip()[:300]
            for item in (raw.get("checks") if isinstance(raw.get("checks"), list) else [])
            if str(item or "").strip()
        ][:8]
        title = str(raw.get("title") or f"Task {index}").strip()[:160]
        objective = str(raw.get("objective") or title).strip()[:1000]
        if not files:
            continue
        steps.append(
            {
                "index": len(steps) + 1,
                "title": title,
                "objective": objective,
                "files": files[:8],
                "checks": checks,
            }
        )
    if not steps:
        raise DeveloperCodingError("developer_coding_plan_empty", status=502)
    suggestions = [
        str(item).strip()[:400]
        for item in (value.get("suggestions") if isinstance(value.get("suggestions"), list) else [])
        if str(item or "").strip()
    ][:6]
    result = {
        "title": str(value.get("title") or "VELIA Coding Agent changes").strip()[:200],
        "summary": str(value.get("summary") or "Implement the requested repository change.").strip()[:2000],
        "steps": steps,
        "suggestions": suggestions,
    }
    design = taste_skill.normalize_design(
        value.get("design") if isinstance(value, dict) else {},
        design_profile if isinstance(design_profile, dict) else {},
    )
    if design:
        result["design"] = design
    return result'''
source = replace_block(source, "def _normalize_plan(", "\n\ndef plan_job", normalize_plan)

old_plan_job = '''    paths = [str(item.get("path") or "") for item in candidates]
    evidence = _planning_evidence(project, queries, candidates)
    prompt = _plan_prompt(project, normalized_goal, paths, evidence)
'''
new_plan_job = '''    paths = [str(item.get("path") or "") for item in candidates]
    evidence = _planning_evidence(project, queries, candidates)
    design_profile = taste_skill.classify(normalized_goal, paths)
    prompt = _plan_prompt(
        project,
        normalized_goal,
        paths,
        evidence,
        taste_profile=design_profile,
    )
'''
if old_plan_job not in source:
    raise SystemExit("plan_job prompt marker missing")
source = source.replace(old_plan_job, new_plan_job, 1)

old_normalize_call = '    plan = _normalize_plan(_extract_json(str(result.get("text") or "")))\n'
new_normalize_call = '''    plan = _normalize_plan(
        _extract_json(str(result.get("text") or "")),
        design_profile=design_profile,
    )
'''
if old_normalize_call not in source:
    raise SystemExit("plan normalization call marker missing")
source = source.replace(old_normalize_call, new_normalize_call, 1)

step_prompt = '''def _step_prompt(
    project: Dict[str, Any],
    job: Dict[str, Any],
    step: Dict[str, Any],
    context: str,
) -> str:
    allowed = [str(path) for path in step.get("files") or []]
    plan = job.get("plan") if isinstance(job.get("plan"), dict) else {}
    design = plan.get("design") if isinstance(plan.get("design"), dict) else {}
    required_checks = [str(item) for item in (step.get("checks") or []) if str(item).strip()]
    for item in taste_skill.preflight_checks(design):
        if item not in required_checks:
            required_checks.append(item)
    required_checks = required_checks[:12]
    taste_guidance = taste_skill.execution_guidance(design, step)
    guidance_block = f"\\n\\n{taste_guidance}" if taste_guidance else ""
    return f"""You are the execution stage of VELIA Coding Agent.
Repository: {project.get('repository_full_name')}
Base branch: {job.get('base_branch')}
Work branch: {job.get('work_branch')}
Overall goal: {job.get('goal')}
Current task {step.get('index')}/{job.get('total_steps')}: {step.get('title')}
Objective: {step.get('objective')}
Allowed files: {json.dumps(allowed, ensure_ascii=False)}
Required checks: {json.dumps(required_checks, ensure_ascii=False)}

Relevant current source excerpts (line numbers are reference only):
{context}{guidance_block}

Return ONLY one compact JSON object:
{{
  "summary": "what this task changes",
  "operations": [
    {{"op":"replace","path":"allowed/path.py","old":"exact existing snippet","new":"replacement snippet"}},
    {{"op":"create","path":"allowed/new_file.py","content":"complete file content"}},
    {{"op":"delete","path":"allowed/obsolete_file.py"}}
  ],
  "checks": ["validation to run in CI"],
  "suggestions": ["optional later improvement"]
}}
Rules:
- Use only allowed files.
- Prefer small exact replacements over rewriting complete existing files.
- Every `old` value must be an exact unique substring from the current file.
- `create` is only for a file that does not exist.
- Do not modify secrets, credentials, .env files, GitHub workflows, generated dependencies, or production configuration.
- Do not merge, deploy, or claim tests passed.
- Preserve existing style and public contracts unless the goal explicitly requires a change.
- When DESIGN EXECUTION GUARD is present, follow it without adding unrelated redesign work.
- No markdown outside JSON.
"""'''
source = replace_block(source, "def _step_prompt(", "\n\ndef _apply_patch_payload", step_prompt)

pr_body = '''def _pr_body(job: Dict[str, Any], results: List[Dict[str, Any]]) -> str:
    plan = job.get("plan") if isinstance(job.get("plan"), dict) else {}
    lines = [
        "## VELIA Coding Agent",
        "",
        str(plan.get("summary") or job.get("goal") or ""),
    ]
    design = plan.get("design") if isinstance(plan.get("design"), dict) else {}
    if design.get("active"):
        lines.extend(
            [
                "",
                "## Design direction",
                str(design.get("read") or ""),
                (
                    f"Mode: `{design.get('mode')}` · variance {design.get('variance')}/10 · "
                    f"motion {design.get('motion')}/10 · density {design.get('density')}/10"
                ),
            ]
        )
    lines.extend(["", "## Completed tasks"])
    for item in results:
        lines.extend(
            [
                f"- [x] {item.get('index')}. {item.get('title')}",
                f"  - Commit: `{item.get('commit_sha')}`",
                f"  - Files: {', '.join(item.get('files') or [])}",
                f"  - Summary: {item.get('summary')}",
            ]
        )
    lines.extend(
        [
            "",
            "## Safety",
            "- Changes were created on an isolated `velia/` branch.",
            "- This pull request is a draft.",
            "- VELIA did not merge or deploy these changes.",
            "- CI results must be reviewed before merge.",
        ]
    )
    return "\\n".join(lines)'''
source = replace_block(source, "def _pr_body(", "\n\ndef execute_job", pr_body)

format_plan = '''def format_plan(job: Dict[str, Any], message: str) -> str:
    plan = job.get("plan") if isinstance(job.get("plan"), dict) else {}
    russian = _russian(message)
    lines = [
        "## План VELIA Coding Agent" if russian else "## VELIA Coding Agent plan",
        "",
        str(plan.get("summary") or ""),
        "",
    ]
    design = plan.get("design") if isinstance(plan.get("design"), dict) else {}
    design_lines = taste_skill.format_summary(design, russian=russian)
    if design_lines:
        lines.extend(design_lines)
        lines.append("")
    for step in plan.get("steps") or []:
        lines.append(f"{step.get('index')}. **{step.get('title')}**")
        lines.append(f"   {step.get('objective')}")
        lines.append(f"   Файлы: {', '.join(step.get('files') or [])}" if russian else f"   Files: {', '.join(step.get('files') or [])}")
        if step.get("checks"):
            lines.append(f"   Проверки: {'; '.join(step.get('checks') or [])}" if russian else f"   Checks: {'; '.join(step.get('checks') or [])}")
        lines.append("")
    suggestions = plan.get("suggestions") or []
    if suggestions:
        lines.append("### Что ещё можно сделать" if russian else "### Further improvements")
        lines.extend(f"- {item}" for item in suggestions)
        lines.append("")
    lines.append(
        "Напиши **«Выполняй план»**. После одного подтверждения я создам отдельную ветку, выполню задачи по порядку и открою draft PR."
        if russian
        else "Reply **‘Execute the plan’**. After one approval I will create an isolated branch, execute tasks in order, and open a draft PR."
    )
    return "\\n".join(lines)'''
source = replace_block(source, "def format_plan(", "\n\ndef format_execution", format_plan)

SERVICE_PATH.write_text(source, encoding="utf-8")
print("VELIA Design Taste integrated into Coding Agent")
