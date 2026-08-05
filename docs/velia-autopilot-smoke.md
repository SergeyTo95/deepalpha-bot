# VELIA Coding Autopilot — Smoke Overview

Short smoke-level description of VELIA Coding Autopilot as implemented in the `turbo-short-term-btc` branch. For the complete specification see [VELIA Coding Autopilot v1](VELIA_CODING_AUTOPILOT_V1.md).

## What it is

Autopilot turns the guarded VELIA Coding Agent into a persistent background queue. A mission owns one connected Developer project and an immutable base-branch snapshot. Tasks are processed sequentially and may create only isolated `velia/` branches and draft pull requests. The release stops at `ready_for_review` — it never merges, deploys, or repairs CI.

## Modes and limits

- Planning and execution run inside the existing guarded Coding Agent pipeline; there are no additional execution modes beyond the standard plan → execute flow.
- Planning limits: `max_steps` is constrained to 1–5, `max_files` to 1–12.
- One active run is allowed per repository; queue claims use PostgreSQL advisory locking and `FOR UPDATE SKIP LOCKED`.
- Existing Coding Agent cost, file-size and GitHub write limits remain authoritative.

## Safety boundaries (summary)

- Feature and worker flags default to disabled; every mission starts paused.
- An explicit non-empty path allowlist is required; `.github`, `.env`, secrets, credentials, auth, billing, migrations and infrastructure paths are protected.
- Pull requests remain draft; no merge or deployment operation exists in the Autopilot service or routes.
- Failure after GitHub writes have started is never automatically retried; a stale run is marked `blocked` for human inspection.

## Staged smoke check

1. Keep `VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED=false` and enable only the Autopilot API together with the existing Developer/Coding/write prerequisites.
2. Create a mission with a path allowlist covering `docs/` only. Confirm that the mission is returned with status `paused`.
3. Queue one small documentation task. Wait longer than one worker interval and verify that no `velia/` branch or pull request appears while the mission is paused and the worker flag is false.
4. Enable `VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED=true`, wait for the production deployment, and then explicitly activate the mission.
5. Verify that exactly one `velia/` branch and one draft pull request appear and that the run reaches `ready_for_review`.
6. Confirm that Autopilot did not merge or deploy anything. Disable the worker again after the disposable smoke if no further queued tasks should run.

See [VELIA_CODING_AUTOPILOT_V1.md](VELIA_CODING_AUTOPILOT_V1.md) for lifecycle details, feature flags, worker controls and the Mobile API.
