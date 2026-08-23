# VELIA Software Factory Stage 4.1 — Multi-Repo Workspaces

Stage 4.1 extends Software Factory from one Developer project/repository to one product workspace containing multiple repositories.

## Safety model

A workspace repository membership is topology only. It does not grant write access.

Each repository requires a separate approved write scope. The approved scope must remain inside the safe repository tree produced by the existing Stage 3 path hardening. A scope cannot be changed while an execution is active or blocked.

Multi-repo execution is fail-closed behind:

- `VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_ENABLED=false` by default;
- existing Software Factory live rollout;
- the existing owner/user allowlist;
- Coding Autopilot and its worker.

Coding Autopilot remains the sole repository writer and review owner. Workspace orchestration never merges pull requests.

## Execution model

A workspace execution validates a cross-repository DAG and creates one isolated Coding Autopilot mission per repository. Existing paused/active missions are treated as conflicts and are never hijacked.

Workspace tasks are dispatched only when all declared dependencies are `ready_for_review`. This is a review-ready dependency gate, not a merge gate.

The final workspace state is `review_ready` only after every task in every repository is `ready_for_review`.

Any failed, blocked, or cancelled leaf task blocks the workspace and pauses all workspace missions.

## Stop semantics

Stop pauses all workspace missions, cancels queued tasks where safe, and lets already claimed/planning/executing repository work reach a safe boundary. Stop reconciliation continues even if live rollout is subsequently disabled.

## API

Under `/mobile-api/v1/developer/factory`:

- `GET/POST /workspaces`
- `GET /workspaces/{workspace_id}`
- `GET /workspaces/{workspace_id}/brain`
- `POST /workspaces/{workspace_id}/repositories/{project_id}/scope`
- `DELETE /workspaces/{workspace_id}/repositories/{project_id}/scope`
- `POST /workspaces/{workspace_id}/plans/validate`
- `GET/POST /workspaces/{workspace_id}/executions`
- `GET /workspaces/{workspace_id}/executions/{execution_id}`
- `POST /workspaces/{workspace_id}/executions/{execution_id}/tick`
- `POST /workspaces/{workspace_id}/executions/{execution_id}/stop`
- `GET /workspaces/{workspace_id}/executions/{execution_id}/events`

Production rollout must remain off until exact-head CI, Railway preview startup, and a controlled authenticated acceptance are proven.
