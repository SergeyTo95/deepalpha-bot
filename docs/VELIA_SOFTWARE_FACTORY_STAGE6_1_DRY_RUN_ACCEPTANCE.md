# VELIA Software Factory Stage 6.1 — Dry-Run Acceptance

Stage 6.1 is an acceptance harness, not a new execution layer. It proves that the production Software Factory runtime can turn a natural-language product request into Architect/Planner/team-DAG output while repository execution stays blocked.

## Safety boundary

The acceptance runner is intended only for a Railway PR-preview environment and is disabled unless `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED=true` is explicitly set there.

Required preview settings:

- `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_REPOSITORY=<exact owner/repository>`
- optional `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_PROMPT`; default: `Хочу интернет-магазин цветов`
- `VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE=dry_run`
- `VELIA_DEVELOPER_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_TEAM_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_AUTONOMY_ENABLED=true`

The following execution/write gates must remain false during Stage 6.1 acceptance:

- `VELIA_SOFTWARE_FACTORY_SUPERVISOR_ENABLED=false`
- `VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_ENABLED=false`
- `VELIA_DEVELOPER_AUTOPILOT_ENABLED=false`
- `VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED=false`
- `VELIA_DEVELOPER_WRITE_ENABLED=false`
- `VELIA_DEVELOPER_AUTOPILOT_MERGE_POLICY_ENABLED=false`
- every Stage 5 release/delivery flag remains false

No production variable is changed for Stage 6.1.

## Railway preview gate

Set the PR-preview `deepalpha-bot` pre-deploy command to:

```text
PYTHONPATH=. python scripts/run_velia_software_factory_dry_run_acceptance.py
```

The runner reproduces production runtime order before probing:

1. Stage 2 runtime is installed on the Factory Lead.
2. Stage 3 hardening is installed after Stage 2.
3. Stage 3 installs the controlled rollout wrapper.
4. The acceptance service runs through the wrapped `factory.create_run`, `factory.answer_clarifications`, and `factory.advance_run` methods.

A `passed` result exits with code `0`. `blocked`, `failed`, or an exception exits non-zero and fails the Railway preview deployment.

## Acceptance flow

The probe resolves one exact Developer project owned by the configured VELIA administrator. It then:

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

Restarting the same preview commit reuses the existing result. A new commit produces a new probe fingerprint and must pass again.

## Completion criterion

Stage 6.1 is accepted only when all of the following are true on the same PR head SHA:

- dedicated and repository-wide GitHub CI are green;
- Railway PR preview executes the pre-deploy acceptance runner;
- pre-deploy output contains `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_RESULT` with `status=passed` and `passed=true`;
- `dry_run=true`;
- `execution_blocked=true`;
- `autopilot_missions_unchanged=true`;
- `repository_write_performed=false`;
- `autopilot_task_dispatched=false`;
- the preview service subsequently reaches `SUCCESS`.

After acceptance, preview-only variables/configuration must not be copied to production.
