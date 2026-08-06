# Standalone `velia-repowise` service

## Status

This directory contains a deployable, read-only Repowise context service for VELIA Coding Autopilot. The code is not the same process as `deepalpha-bot` and does not add Repowise to the production backend dependency set.

The service is not enabled merely by merging this code. Production activation additionally requires:

1. a separate Railway service built from `Dockerfile.velia-repowise`;
2. a persistent volume mounted for mirrors and indexes;
3. a strong service-to-service bearer token;
4. an allowlisted local mirror for every repository;
5. an external read-only mirror synchronization mechanism;
6. the matching disabled-by-default variables in `deepalpha-bot`;
7. a real exact-SHA integration test through the private Railway network.

## Architecture

```text
VELIA Coding Autopilot Planning
        |
        | POST /v1/context/planning
        | requested branch head SHA + bounded candidate paths
        v
velia-repowise API
        |
        | allowlist + bearer auth + exact SHA
        v
local read-only Git mirror
        |
        | detached persistent worktree at requested SHA
        v
repowise init --no-prose
        |
        | MCP get_context / get_overview
        v
bounded exact-SHA read-only context
```

The service never receives the GitHub App private key or an installation token. It does not fetch from GitHub, write commits, create branches, open or merge pull requests, or deploy anything.

## API

### `GET /health`

Unauthenticated liveness and capability response. It does not expose paths, repository names, tokens or index data.

### `GET /v1/license`

Returns the bundled Repowise version, AGPL license identifier and upstream source URL.

### `POST /v1/context/planning`

Requires:

```text
Authorization: Bearer <VELIA_REPOWISE_SERVICE_TOKEN>
```

Request:

```json
{
  "repository_full_name": "SergeyTo95/deepalpha-bot",
  "repository_id": 1197469576,
  "branch": "feature/turbo-short-term-btc",
  "requested_sha": "40-character exact branch head SHA",
  "goal": "bounded planning goal",
  "candidate_paths": ["services/example.py"],
  "max_context_chars": 12000,
  "mode": "read_only"
}
```

Response:

```json
{
  "ok": true,
  "repository_full_name": "SergeyTo95/deepalpha-bot",
  "requested_sha": "same exact SHA",
  "indexed_sha": "same exact SHA",
  "mode": "read_only",
  "read_only": true,
  "context": "bounded Repowise MCP result",
  "telemetry": false,
  "llm_generation": false
}
```

The workspace is verified before and after the MCP read. The client in `deepalpha-bot` separately rejects the response unless `indexed_sha` equals the current selected-branch head.

## Persistent storage

Recommended volume mount:

```text
/data/velia-repowise/
├── mirrors/
│   ├── deepalpha-bot.git/
│   └── deepalpha-android.git/
└── workspaces/
    ├── SergeyTo95--deepalpha-bot-<hash>/
    │   └── <exact-sha>/
    └── SergeyTo95--deepalpha-android-<hash>/
        └── <exact-sha>/
```

`VELIA_REPOWISE_REPOSITORIES_JSON` must map repository identities to paths under the configured mirror root. Any path outside that root is rejected. Requested SHAs absent from the local mirror are rejected; the API does not attempt a network fetch.

Example:

```json
{
  "SergeyTo95/deepalpha-bot": "/data/velia-repowise/mirrors/deepalpha-bot.git",
  "SergeyTo95/deepalpha-android": "/data/velia-repowise/mirrors/deepalpha-android.git"
}
```

## Required environment

```text
PORT=7337
VELIA_REPOWISE_SERVICE_TOKEN=<at least 24 random characters>
VELIA_REPOWISE_MIRROR_ROOT=/data/velia-repowise/mirrors
VELIA_REPOWISE_WORKSPACE_ROOT=/data/velia-repowise/workspaces
VELIA_REPOWISE_REPOSITORIES_JSON={...}
REPOWISE_TELEMETRY_DISABLED=1
REPOWISE_SKIP_EDITOR_SETUP=1
DO_NOT_TRACK=1
```

Optional limits:

```text
VELIA_REPOWISE_COMMAND_TIMEOUT_SECONDS=60
VELIA_REPOWISE_INDEX_TIMEOUT_SECONDS=1200
VELIA_REPOWISE_MCP_TIMEOUT_SECONDS=30
VELIA_REPOWISE_MAX_WORKSPACES_PER_REPO=3
VELIA_REPOWISE_MAX_REQUEST_BYTES=65536
VELIA_REPOWISE_MAX_CONTEXT_CHARS=12000
VELIA_REPOWISE_MAX_CANDIDATE_PATHS=20
VELIA_REPOWISE_MAX_CONCURRENCY=2
```

## Mirror synchronization

Mirror synchronization is intentionally outside the HTTP service. A deployment must populate and update mirrors through a separate read-only operational process. That process may use a deploy key or GitHub App installation token restricted to repository contents read, but those credentials must never be present in the context API container or accepted through an API endpoint.

A mirror is ready only after this succeeds operationally:

```bash
git --git-dir=/data/velia-repowise/mirrors/deepalpha-bot.git cat-file -e <sha>^{commit}
```

Until the requested production head exists in the mirror, the context API returns `sha_not_in_mirror` and the existing backend provider falls back to GitHub evidence.

## Indexing and isolation

For each requested SHA, the service:

1. validates repository allowlisting and the 40-character SHA;
2. verifies that the commit exists in the mirror;
3. creates a detached worktree under the persistent workspace root;
4. runs Repowise in keyless `--no-prose` mode;
5. removes all common LLM provider variables from the subprocess;
6. disables telemetry and editor setup;
7. verifies `.repowise/state.json` and worktree `HEAD` against the exact SHA;
8. writes a service-owned workspace marker;
9. reuses the workspace for subsequent requests;
10. prunes older workspaces beyond the configured per-repository limit.

Repowise project-local MCP and VS Code files remain isolated inside these workspaces and never touch the source mirror or `deepalpha-bot` checkout.

## MCP surface

The child MCP server advertises only:

- `get_context`;
- `get_overview`.

Planning with candidate paths calls `get_context` in compact mode with bounded architecture, callers/callees, ownership, history, metrics, community, decisions and skeleton data. When no candidate paths exist, it calls `get_overview`.

The HTTP response is character-bounded. Truncated responses carry a clear `VELIA_REPOWISE_CONTEXT_TRUNCATED` marker.

## Security properties

- read-only HTTP contract;
- constant-time bearer comparison;
- repository allowlist;
- mirror/workspace root confinement;
- exact-SHA attribution;
- verification before and after MCP reads;
- bounded request and response sizes;
- bounded subprocess timeouts;
- bounded concurrency;
- no redirects or outbound HTTP in the service;
- no GitHub credentials;
- no Git fetch, push, commit, branch, PR, merge or deployment actions;
- no LLM generation;
- telemetry disabled;
- fail-open behavior remains in the calling backend, not in this service.

## License

The container installs the unmodified `repowise==0.39.0` PyPI package under `AGPL-3.0-or-later`. The service exposes the upstream source URL at `/v1/license`. VELIA-specific orchestration code remains separate and communicates through the public CLI/MCP surface.

## Exact-head acceptance

`.github/workflows/velia-repowise-service.yml` performs:

1. isolated dependency installation on Python 3.11;
2. compile and focused boundary tests;
3. a real local Git mirror of the exact PR checkout;
4. a real Repowise index at the exact SHA;
5. direct MCP `get_context`;
6. authenticated HTTP API request;
7. workspace reuse and SHA verification;
8. telemetry-spool check;
9. standalone Docker image build;
10. source audit for forbidden write/deployment commands.

Passing this workflow proves the service implementation, not production provisioning. Production is accepted only after the separately deployed Railway service answers through the private network on the real production head.
