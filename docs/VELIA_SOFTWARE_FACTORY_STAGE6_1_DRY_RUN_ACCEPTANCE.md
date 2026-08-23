# VELIA Software Factory Stage 6.1 — Dry-Run Acceptance

Stage 6.1 is an acceptance harness, not a new execution layer. It proves that the production Software Factory runtime can turn a natural-language product request into Architect/Planner/team-DAG output while repository execution stays blocked.

## Safety boundary

The acceptance startup gate is intended only for a Railway PR-preview environment and is disabled unless `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED=true` is explicitly set there.

Required preview settings:

- `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_REPOSITORY=<exact owner/repository>`
- optional `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_PROMPT`; default: `Хочу интернет-магазин цветов`
- `VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE=<admin_id|live_owner|jarvis_founder|chat_beta|mobile_debug>`
- `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ACTOR_SOURCE=<pilot_allowlist|repository_owner|preview_fixture>`
- `VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE=dry_run`
- `VELIA_DEVELOPER_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_TEAM_ENABLED=true`
- `VELIA_SOFTWARE_FACTORY_AUTONOMY_ENABLED=true`

`VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE` never contains a numeric user ID. It selects one already configured server-controlled identity source. Unknown source names resolve to an empty actor set and fail closed.

The normal acceptance actor sources are `pilot_allowlist` and `repository_owner`. `preview_fixture` exists only for Railway PR-preview environments whose isolated database does not contain a real user-owned Developer project binding. It requires the independent flag `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_FIXTURE_ENABLED=true`; the fixture service also requires `RAILWAY_ENVIRONMENT_NAME` to contain `-pr-` and a non-empty `RAILWAY_ENVIRONMENT_ID`. Production cannot satisfy this preview guard.

The following execution/write gates must remain false during Stage 6.1 acceptance:

- `VELIA_SOFTWARE_FACTORY_SUPERVISOR_ENABLED=false`
- `VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_ENABLED=false`
- `VELIA_SOFTWARE_FACTORY_INTEGRATION_VALIDATOR_ENABLED=false`
- `VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_ENABLED=false`
- `VELIA_SOFTWARE_FACTORY_GREENFIELD_BOOTSTRAP_ENABLED=false`
- `VELIA_DEVELOPER_AUTOPILOT_ENABLED=false`
- `VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED=false`
- `VELIA_DEVELOPER_WRITE_ENABLED=false`
- `VELIA_DEVELOPER_AUTOPILOT_MERGE_POLICY_ENABLED=false`
- every Stage 5 release/delivery flag remains false

The startup gate re-checks all of these dangerous flags before resolving an acceptance actor. Any open execution/write/release gate aborts startup. No production variable is changed for Stage 6.1.

## Railway preview startup gate

Stage 6.1 is enforced inside the existing controlled rollout runtime after the Stage 2 team runtime and dry-run wrapper have been installed.

Runtime order:

1. Stage 2 owns Factory `create/get/clarify/advance`.
2. Stage 3 hardening installs the controlled rollout wrapper.
3. The rollout runtime replaces `factory.advance_run` with the `dry_run` planner path.
4. The startup gate verifies `dry_run` mode and that every dangerous execution/write/release flag is closed.
5. It resolves the acceptance actor through the configured acceptance actor source.
6. Only then does it call the acceptance service.
7. A non-passing result or exception is re-raised and prevents the preview web process from starting.

The startup gate logs a safe `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_RESULT` marker. It may expose source names and aggregate counts, but never actor IDs, `ADMIN_ID`, allowlist contents, or prompt text.

The standalone helper remains available for local/diagnostic use:

```text
PYTHONPATH=. python scripts/run_velia_software_factory_dry_run_acceptance.py
```

It is not the acceptance authority for Railway PR previews because Railway Docker deployments do not reliably expose pre-deploy command execution/output through the deployment evidence available to this project.

## Actor resolution

### `pilot_allowlist`

The gate checks the selected server-controlled Stage 6 pilot actor set and requires exactly one actor with an active Developer project matching the exact repository. Zero matches or multiple matches fail closed.

### `repository_owner`

The gate reads the preview database and requires exactly one user owning an active `velia_developer_projects` row for the exact repository with an active Developer installation. The user ID is kept in-process and is never logged. This source does not broaden normal Factory rollout eligibility; the resolved actor is bridged only for the synchronous startup acceptance probe and the original rollout functions are restored in `finally`.

### `preview_fixture`

This is the final fallback for an isolated PR-preview database with no real Developer binding. It is not a fake live GitHub installation and is never available in production.

The fixture:

1. requires the dedicated fixture flag and Railway `-pr-` environment guard;
2. creates deterministic synthetic Developer installation/project rows in reserved high BIGINT ranges, scoped by Railway environment ID and exact repository;
3. refuses to overwrite any conflicting existing fixture IDs;
4. exists only to satisfy the real Factory run foreign key inside the preview database;
5. supplies a deterministic read-only repository tree (`services/`, `tests/`, `docs/`) only to the existing `recommend_write_scope(..., tree_loader=...)` safety filter;
6. introduces no GitHub write, Coding Autopilot, merge, deployment, repository-creation, credential, or external-network primitive;
7. temporarily bridges only the synthetic actor's dry-run eligibility during the synchronous probe and restores the normal rollout/scope functions in `finally`.

The fixture does not claim to validate GitHub App installation wiring. Stage 6.1 validates Factory product intake, clarification, architecture, planning, team-DAG generation, evidence persistence, and the repository-execution safety boundary.

## Acceptance flow

The probe resolves one exact Developer project (real or preview fixture). It then:

1. Reads a safe recommended write scope through the production safety filter.
2. Builds a `ProjectSpec` from the natural-language request.
3. Creates a real Factory run in the preview database.
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

The database uniqueness key also includes the resolved acceptance actor, so the same code/repo/prompt tested by another actor cannot reuse another actor's evidence.

Restarting the same preview commit for the same actor reuses the existing result. A new commit or actor produces new acceptance evidence and must pass again.

## Completion criterion

Stage 6.1 is accepted only when all of the following are true on the same PR head SHA:

- dedicated and repository-wide GitHub CI are green;
- Railway PR preview starts with the Stage 6.1 preview-only variables above;
- startup logs contain `VELIA_SOFTWARE_FACTORY_DRY_RUN_PLANNED` for the run;
- startup logs contain `VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_RESULT` with `status=passed` and `passed=true`;
- the marker exposes no raw actor ID;
- `dry_run=true`;
- `execution_blocked=true`;
- `autopilot_missions_unchanged=true`;
- `repository_write_performed=false`;
- `autopilot_task_dispatched=false`;
- `merge_performed=false`;
- `deployment_triggered=false`;
- the preview webapp remains running after the gate;
- the preview service subsequently reaches `SUCCESS`.

After acceptance, preview-only acceptance/fixture flags should be returned to their fail-closed values. No production variable or production service configuration is changed or copied from the preview acceptance environment.