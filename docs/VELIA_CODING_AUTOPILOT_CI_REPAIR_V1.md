# VELIA Coding Autopilot — CI Watch and Repair v1

This stage extends the draft-PR-only Autopilot foundation. A run no longer becomes ready for review immediately after opening its pull request.

## Lifecycle

```text
queued
→ planning
→ executing
→ waiting_ci
→ repairing (only after a repairable failure)
→ waiting_ci
→ ready_for_review
```

Terminal safety states are `blocked`, `failed`, and `cancelled`.

## Exact-head CI watch

For the current work-branch head, VELIA reads:

- GitHub check runs;
- check-run output and bounded annotations for failed checks;
- commit statuses, including Railway preview statuses.

A run reaches `ready_for_review` only when every observed exact-head check is completed with a successful, neutral, or skipped conclusion.

Missing checks remain pending during a short grace period. A run is blocked when checks never appear or remain pending beyond the configured maximum wait.

## Bounded repair

Repair is separately feature-gated. When enabled, VELIA may create at most two repair commits on the existing `velia/` work branch.

Each repair:

- uses only files from the original approved Coding Agent plan;
- revalidates the mission allowlist and protected paths;
- uses bounded check output and annotations as evidence;
- checks that the branch head did not change before committing;
- creates no new branch or pull request;
- never merges or deploys.

Infrastructure, cancellation, timeout, permission, runner, network, and external-status-only failures are blocked rather than "fixed" by changing product code.

## Feature flags

```env
VELIA_DEVELOPER_AUTOPILOT_CI_ENABLED=false
VELIA_DEVELOPER_AUTOPILOT_CI_REPAIR_ENABLED=false
VELIA_DEVELOPER_AUTOPILOT_CI_MAX_REPAIRS=2
VELIA_DEVELOPER_AUTOPILOT_CI_GRACE_SECONDS=90
VELIA_DEVELOPER_AUTOPILOT_CI_MAX_WAIT_MINUTES=45
VELIA_DEVELOPER_AUTOPILOT_CI_REPAIR_MAX_COST_USD=0.06
```

## Staged rollout

1. Merge and deploy with both new flags disabled.
2. Enable `VELIA_DEVELOPER_AUTOPILOT_CI_ENABLED=true` only.
3. Run one documentation-only mission and confirm `waiting_ci → ready_for_review` without a repair commit.
4. Create a controlled failing test task in an isolated smoke repository or allowlisted smoke path.
5. Enable `VELIA_DEVELOPER_AUTOPILOT_CI_REPAIR_ENABLED=true`.
6. Confirm one bounded repair commit, a new exact-head CI attempt, and no merge/deployment.
7. Keep the mission paused after the smoke until the run history and cost are reviewed.
