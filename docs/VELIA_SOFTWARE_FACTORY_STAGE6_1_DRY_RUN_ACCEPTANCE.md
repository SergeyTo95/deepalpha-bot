# VELIA Software Factory Stage 6.1 — Dry-Run Acceptance

Stage 6.1 is an acceptance harness, not a new execution layer. It proves that the production Software Factory runtime can turn a natural-language product request into Architect/Planner/team-DAG output while repository execution stays blocked.

## Safety boundary

The acceptance startup gate is intended only for a Railway PR-preview environment and is disabled unless `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED=true` is explicitly set there.

Required preview settings:

- `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_REPOSITORY=<exact owner/repository>`
- optional `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_PROMPT`; default: `Хочу интернет-магазин цветов`
- `VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE=<admin_id|live_owner|jarvis_founder|chat_beta>`
- `VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE=dry_run`
- `VELIA_DEVELOPER_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_TEAM_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_AUTONOMY_ENABLED=true`

`VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE` never contains a numeric user ID. It selects one already configured server-controlled identity source. `admin_id` preserves the Stage 6 default; the other supported values reuse existing owner/founder/beta allowlists. Unknown source names resolve to an empty actor set and fail closed.

The following execution/write gates must remain false during Stage 6.1 acceptance:

- `VELIA_SOFTWARE_FACTORY_SUPERVISOR_ENABLED=false`
- `VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_ENABLED=false`
- `VELIA_DEVELOPER_AUTOPILOT_ENABLED=false`
- `VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED=false`
- `VELIA_DEVELOPER_WRITE_ENABLED=false`
- `VELIA_DEVELOPER_AUTOPILOT_MERGE_POLICY_ENABLED=false`
- every Stage 5 release/delivery flag remains false

No production variable is changed for Stage 6.1.

## Railway preview startup gate

Stage 6.1 is enforced inside the existing controlled rollout runtime after the Stage 2 team runtime and dry-run wrapper have been installed.

Runtime order:

1. Stage 2 owns Factory `create/get/clarify/advance`.
2. Stage 3 hardening installs the controlled rollout wrapper.
3. The rollout runtime replaces `factory.advance_run` with the `dry_run` planner path.
4. The startup gate reads the exact acceptance repository and the selected server-controlled pilot actor set.
5. It accepts exactly one actor whose active Developer project matches the exact repository. Zero matches or multiple matches fail closed.
6. Only then, when `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED=true`, the gate calls the acceptance service.
7. A non-passing result or exception is re-raised and prevents the preview web process from starting.

The startup gate logs a safe `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_RESULT` marker. It may expose the source name and actor count, but never the actor IDs, `ADMIN_ID`, allowlist contents, or the prompt text.

The standalone helper remains available for local/diagnostic use:

```text
PYTHONPATH=. python scripts/run_velia_software_factory_dry_run_acceptance.py
```

It is not the acceptance authority for Railway PR previews because Railway Docker deployments do not reliably expose pre-deploy command execution/output through the deployment evidence available to this project.

## Acceptance flow

The probe resolves one exact Developer project owned by the uniquely matched pilot actor. It then:

1. Reads a safe recommended write scope.
2. Builds a ProjectSpec from the natural-language request.
3. Creates a Factory run.
4. If and only if the sole blocking gap is `write_scope_required`, it supplies the safe recommended paths as the acceptance-only scope answer.
5. Any other blocking clarification stops the probe; the harness never guesses product requirements.
6. Advances the run through the real controlled `dry_run` wrapper.
7. Requires stored architecture, non-empty team plan, non-empty team roles, final Factory state `planning`, `dry_run=true`, and `execution_blocked=true`.
8. Compares the complete Autopilot mission-ID set before and after. Any change fails acceptance.

The result records `repository_write_performed=false`, `autopilot_task_dispatched=false`, `merge_performed=false`, and `deployment_triggered=false`.

## Idempotence and evidence

Acceptance evidence is append-only in `velia_software_factory_dry_run_acceptance_runs`.

The probe fingerprint includes:

- Stage version `stage6.1`
- exact repository
- prompt fingerprint
- exact `RAILWAY_GIT_COMMIT_SHA`

The database uniqueness key also includes the resolved pilot actor, so the same code/repo/prompt tested by another actor cannot reuse another actor's evidence.

Restarting the same preview commit for the same actor reuses the existing result. A new commit or actor produces new acceptance evidence and must pass again.

## Completion criterion

Stage 6.1 is accepted only when all of the following are true on the same PR head SHA:

- dedicated and repository-wide GitHub CI are green;
- Railway PR preview starts with the Stage 6.1 preview-only variables above;
- startup logs contain `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_RESULT` with `status=passed` and `passed=true`;
- the marker identifies only the bounded `pilot_source` and actor count, never raw IDs;
- `dry_run=true`;
- `execution_blocked=true`;
- `autopilot_missions_unchanged=true`;
- `repository_write_performed=false`;
- `autopilot_task_dispatched=false`;
- `merge_performed=false`;
- `deployment_triggered=false`;
- the preview service subsequently reaches `SUCCESS`.

After acceptance, preview-only variables/configuration must not be copied to production.