# VELIA Repowise integration spike

## Goal

Validate Repowise against the real `deepalpha-bot` repository before it is connected to VELIA Coding Autopilot or installed in a production container.

This phase is intentionally read-only. It does not change Autopilot planning, CI Repair, review handling, merge policy, GitHub credentials, Railway configuration, or Android.

## Why this is isolated

VELIA Coding Autopilot currently works through the GitHub API. Repowise requires a local Git checkout and maintains a local `.repowise/` index. Adding the full package directly to the `deepalpha-bot` runtime would significantly expand the production dependency graph and container size.

The spike therefore runs only in GitHub Actions on an exact checked-out PR head.

## Safety controls

- Repowise is pinned in `requirements-repowise.txt`, not production `requirements.txt`.
- The index is built with `repowise init --yes --no-prose`.
- No LLM provider key is supplied.
- `REPOWISE_TELEMETRY_DISABLED=1` and `DO_NOT_TRACK=1` are mandatory.
- The workflow has `contents: read` only.
- Full Git history is checked out because dependency and change-risk analysis use Git history.
- The runner verifies that the index is attributable to the exact checkout head.
- The generated index and reports are ignored by Git and uploaded only as short-lived workflow artifacts.
- No commit, pull-request, merge, deployment, secret, billing, or migration endpoint is available to the spike.

## Evidence collected

The workflow records:

- installed Repowise version;
- exact repository head and base SHA;
- initial indexing duration;
- total spike duration;
- index size and file count;
- Repowise status output;
- code-health JSON;
- dead-code JSON;
- change-risk JSON for `base..head` when possible;
- telemetry spool state;
- a Markdown and JSON acceptance report.

## Acceptance criteria

The spike passes only when all of the following are true:

1. The pinned Repowise package installs on Python 3.11.
2. Unit tests and compilation of the runner pass.
3. Keyless indexing completes within the workflow timeout.
4. `.repowise/state.json` exists and the index can be attributed to the exact checked-out head.
5. Health, dead-code, and risk commands return machine-readable JSON.
6. The local index is non-empty.
7. No telemetry remains queued.
8. Repowise does not modify tracked repository files.

## Next phase after a successful spike

A separate PR may add a disabled-by-default read-only `RepowiseContextProvider` and a dedicated workspace service. That service must:

- maintain one bounded checkout and index per allowlisted repository;
- checkout the exact requested SHA;
- return the indexed SHA with every response;
- reject stale responses;
- expose only read-only context, risk, callers, dependencies, and impacted-test data;
- fall back to the existing GitHub evidence path when unavailable;
- have no commit, merge, or deployment capability.

Production enablement requires a separate Railway service and persistent volume. It is not part of this spike.
