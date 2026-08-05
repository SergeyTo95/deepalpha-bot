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

## Smoke check

1. Enable flags: `VELIA_DEVELOPER_ENABLED`, `VELIA_DEVELOPER_CODING_ENABLED`, `VELIA_DEVELOPER_WRITE_ENABLED`, plus `VELIA_DEVELOPER_AUTOPILOT_ENABLED` and `VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED`.
2. Create a mission with a path allowlist covering `docs/` only, then activate it.
3. Queue a single small documentation task and wait for the worker tick.
4. Verify a `velia/` branch and a draft pull request appear, and the mission reports `ready_for_review`.

See [VELIA_CODING_AUTOPILOT_V1.md](VELIA_CODING_AUTOPILOT_V1.md) for lifecycle details, feature flags, worker controls and the Mobile API.
