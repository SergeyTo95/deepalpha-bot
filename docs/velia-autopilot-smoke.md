# VELIA Coding Autopilot — Smoke Overview

> Smoke-test reference for the VELIA Coding Autopilot running in the
> `turbo-short-term-btc` branch. Behavior described here mirrors
> `services/velia_agent_coding_autopilot_routes.py` (API/status fields) and
> `services/velia_agent_coding_autopilot_service.py` (mission/task lifecycle).

## Purpose

VELIA Coding Autopilot is an agent execution layer that takes a high-level
coding goal, decomposes it into ordered tasks, executes them against a
repository work branch, and opens a **draft pull request** with the results.
It is designed for supervised automation: humans review every change before
it can land anywhere.

## Mission / Task Model

- **Mission** — a top-level unit of work created from a goal description plus
  repository/branch context. A mission is decomposed into an ordered list of
  tasks (e.g. `1/2`, `2/2`).
- **Task** — a single executable step with its own objective and an explicit
  allowlist of files it may touch. Tasks run sequentially within a mission.
- **Statuses** — missions and tasks progress through the lifecycle states
  defined in `services/velia_agent_coding_autopilot_service.py`
  (pending → running → completed/failed, with tasks additionally skippable
  when their objective is already satisfied). Status responses expose the
  current state, progress counters (current task index / total), and the
  active work branch.

## Mode: `draft_pr_only`

The autopilot operates in **`draft_pr_only`** mode:

- All changes are delivered exclusively as a **draft pull request**.
- **No auto-merge** — the agent never merges pull requests.
- **No deployment** — the agent never triggers deploys or touches production
  configuration.
- Every run ends in a reviewable artifact; merging is a human decision.

## Concurrency: One Active Run per Repository

At most **one active autopilot run per repository** is allowed. A request to
start a new mission while another run is active for the same repository is
rejected, guaranteeing a single work branch and a single draft PR in flight
at any time.

## Protected Path Policy

Each task declares an explicit list of **allowed files**. The autopilot
refuses to modify anything outside that allowlist. In addition, the
following are always protected, regardless of allowlists:

- secrets, credentials, and `.env` files;
- CI/CD and GitHub workflow definitions;
- generated dependency manifests/lockfiles;
- production configuration.

Violating operations are rejected before execution.

## Key API Endpoints

Defined in `services/velia_agent_coding_autopilot_routes.py`:

| Endpoint | Description |
| --- | --- |
| `GET .../status` | Current autopilot status: mode (`draft_pr_only`), active run (if any), repository, work branch, mission/task progress, and lifecycle state. |
| `GET .../missions` | List of missions with their statuses, task breakdowns, and results (draft PR references where available). |

## Smoke Check

1. `GET` the status endpoint and confirm `mode` is `draft_pr_only` and no
   unexpected active run exists.
2. `GET` the missions endpoint and confirm previously recorded missions are
   listed with valid statuses.
3. Attempt to start a second run for the same repository while one is
   active — it must be rejected.
4. Confirm no merge or deployment side effects occur as a result of any run.
