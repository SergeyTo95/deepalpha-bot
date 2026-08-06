# VELIA Repowise integration spike

## Goal

Validate Repowise against the real `deepalpha-bot` repository before it is connected to VELIA Coding Autopilot or installed in a production container.

This phase is intentionally read-only. It does not change Autopilot planning, CI Repair, review handling, merge policy, GitHub credentials, Railway configuration, or Android.

## Why this is isolated

VELIA Coding Autopilot currently works through the GitHub API. Repowise requires a local Git checkout and maintains a local `.repowise/` index. Adding the full package directly to the `deepalpha-bot` runtime would significantly expand the production dependency graph and container size.

Repowise 0.39.0 also writes project-local MCP and VS Code files during `init` even when global editor registration, Claude instructions, AGENTS and Codex setup are disabled. The observed files are:

- `.mcp.json`;
- `.vscode/extensions.json`;
- `.vscode/mcp.json`.

For that reason, the spike never runs in the primary checkout. GitHub Actions creates a disposable detached worktree on the exact PR head, builds the index there, copies only bounded evidence to the artifact directory, verifies the primary checkout is clean, and removes the worktree.

## Safety controls

- Repowise is pinned in `requirements-repowise.txt`, not production `requirements.txt`.
- The index is built with `repowise init --yes --no-prose`.
- Editor and agent setup flags are explicitly disabled.
- `REPOWISE_SKIP_EDITOR_SETUP=1` is mandatory.
- No LLM provider key is supplied.
- `REPOWISE_TELEMETRY_DISABLED=1` and `DO_NOT_TRACK=1` are mandatory.
- The workflow has `contents: read` only.
- Full Git history is checked out because dependency and change-risk analysis use Git history.
- The isolated worktree is detached at the exact requested SHA.
- The runner verifies that the index is attributable to that exact checkout head.
- The primary checkout must remain clean, including untracked files.
- Side effects inside the disposable worktree are allowlisted to the three observed project configuration files; anything else fails the workflow.
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
- dead-code JSON and compact counts;
- change-risk JSON for `base..head` when possible;
- telemetry spool state;
- a Markdown and JSON acceptance report.

## Acceptance criteria

The spike passes only when all of the following are true:

1. The pinned Repowise package installs on Python 3.11.
2. Unit tests and compilation of the runner pass.
3. A disposable exact-head Git worktree is created.
4. Keyless indexing completes within the workflow timeout.
5. `.repowise/state.json` exists and the index can be attributed to the exact checked-out head.
6. Health, dead-code, and risk commands return machine-readable JSON.
7. The local index is non-empty.
8. No telemetry remains queued.
9. The primary checkout remains fully clean.
10. The isolated worktree contains no non-allowlisted project-file side effects.

## Next phase after a successful spike

A separate PR may add a disabled-by-default read-only `RepowiseContextProvider` and a dedicated workspace service. That service must:

- maintain one bounded disposable checkout and index per allowlisted repository;
- checkout the exact requested SHA;
- isolate Repowise-generated project files from the source checkout;
- return the indexed SHA with every response;
- reject stale responses;
- expose only read-only context, risk, callers, dependencies, and impacted-test data;
- fall back to the existing GitHub evidence path when unavailable;
- have no commit, merge, or deployment capability.

Production enablement requires a separate Railway service and persistent volume. It is not part of this spike.
