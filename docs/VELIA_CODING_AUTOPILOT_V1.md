# VELIA Coding Autopilot v1

## Scope

The first Autopilot release turns the existing guarded VELIA Coding Agent into a persistent background queue.

A mission owns one connected Developer project and an immutable base-branch snapshot. Tasks are processed sequentially and may create only isolated `velia/` branches and draft pull requests.

This release stops at `ready_for_review`. It does not wait for final CI, repair CI, merge, deploy, resolve review threads, or modify production configuration.

## Lifecycle

```text
mission paused
→ task queued
→ mission activated
→ task claimed with lease
→ Coding Agent planning
→ mission path policy validation
→ Coding Agent execution
→ draft pull request
→ ready_for_review
```

Failure after GitHub writes have started is never automatically retried. A stale run is marked `blocked` so a human can inspect any branch or draft PR before another task is attempted.

## Safety boundaries

- feature and worker flags default to disabled;
- every mission starts paused;
- an explicit non-empty path allowlist is required;
- `.github`, `.env`, secrets, credentials, auth, billing, migrations and infrastructure paths are protected;
- one active run is allowed per repository;
- queue claims use PostgreSQL advisory locking and `FOR UPDATE SKIP LOCKED`;
- runs use leases and persistent events;
- existing Coding Agent cost, file-size and GitHub write limits remain authoritative;
- pull requests remain draft;
- no merge or deployment operation exists in the Autopilot service or routes;
- no public manual worker tick endpoint exists.

## Feature flags

```env
VELIA_DEVELOPER_ENABLED=true
VELIA_DEVELOPER_CODING_ENABLED=true
VELIA_DEVELOPER_WRITE_ENABLED=true
VELIA_DEVELOPER_AUTOPILOT_ENABLED=false
VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED=false
```

Optional worker controls:

```env
VELIA_DEVELOPER_AUTOPILOT_INTERVAL_SECONDS=60
VELIA_DEVELOPER_AUTOPILOT_LEASE_SECONDS=3600
VELIA_DEVELOPER_AUTOPILOT_MAX_RUNS_PER_TICK=1
VELIA_DEVELOPER_AUTOPILOT_MAX_QUEUED_TASKS=50
```

## Mobile API

Prefix:

```text
/mobile-api/v1/developer/autopilot
```

Available operations:

- status;
- list/create missions;
- activate/pause a mission;
- list/enqueue tasks;
- cancel queued, failed or blocked tasks;
- list runs and inspect one run.

There is intentionally no endpoint for merge, deploy, CI repair, arbitrary shell execution or forced worker ticks.

## Required acceptance before enabling the worker

1. Create a mission for a disposable test repository or allowlisted documentation/test paths.
2. Confirm that the mission is paused after creation.
3. Queue one small task and confirm no branch or PR appears while paused.
4. Activate the mission.
5. Confirm one `velia/` branch, bounded commits and one draft PR.
6. Confirm the run stops at `ready_for_review`.
7. Confirm no merge or deployment occurs.
8. Restart the worker during a separate test and verify stale-run behavior before enabling production workloads.

CI waiting and bounded repair loops belong to the next Autopilot PR.
