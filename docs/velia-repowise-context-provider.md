# VELIA Repowise read-only context provider

## Purpose

This provider lets VELIA Coding Autopilot use a separately maintained Repowise index during the Planning stage without adding Repowise or its dependency graph to the production `deepalpha-bot` container.

The provider does not change the list of candidate files and does not participate in execution, CI Repair, review repair, merge, or deployment. It may replace the bounded Planning evidence passed to the model only after an exact-SHA read-only contract is verified.

## Default state

The integration is disabled by default.

```text
VELIA_DEVELOPER_REPOWISE_CONTEXT_ENABLED=false
```

When disabled, no branch lookup and no network request to a Repowise service is made. Existing GitHub tree/read evidence remains unchanged.

## Configuration

```text
VELIA_DEVELOPER_REPOWISE_CONTEXT_ENABLED=true
VELIA_DEVELOPER_REPOWISE_CONTEXT_URL=http://velia-repowise.railway.internal:7337
VELIA_DEVELOPER_REPOWISE_CONTEXT_TOKEN=<service-to-service token>
VELIA_DEVELOPER_REPOWISE_CONTEXT_TIMEOUT_SECONDS=8
VELIA_DEVELOPER_REPOWISE_CONTEXT_MAX_CHARS=12000
```

URL policy:

- HTTPS is accepted for configured external endpoints.
- Plain HTTP is accepted only for localhost or `*.railway.internal` private networking.
- Redirects are rejected so the bearer token cannot be forwarded to another host.

The token is sent only in the Authorization header. GitHub App installation tokens and private keys are never sent to the context service.

## Request contract

`POST /v1/context/planning`

```json
{
  "repository_full_name": "SergeyTo95/deepalpha-bot",
  "repository_id": 1197469576,
  "branch": "feature/turbo-short-term-btc",
  "requested_sha": "40-character branch head SHA",
  "goal": "bounded user task",
  "candidate_paths": ["bounded/repository/path.py"],
  "max_context_chars": 12000,
  "mode": "read_only"
}
```

## Required response contract

```json
{
  "repository_full_name": "SergeyTo95/deepalpha-bot",
  "indexed_sha": "same 40-character requested SHA",
  "mode": "read_only",
  "read_only": true,
  "context": "bounded graph/git/health context"
}
```

The response is rejected unless:

1. HTTP status is 200.
2. The payload is a JSON object.
3. Repository identity matches exactly.
4. `indexed_sha` exactly equals the current selected branch head.
5. `mode=read_only` and `read_only=true` are both present.
6. Context is non-empty and within the configured character limit.

## Failure policy

Every provider failure is fail-open to the existing GitHub Planning evidence:

- feature disabled;
- endpoint not configured;
- selected branch head unavailable;
- connection timeout or network error;
- redirect or non-200 response;
- malformed JSON;
- repository mismatch;
- stale index;
- missing read-only contract;
- empty or oversized context.

The mission must not be blocked merely because the optional intelligence layer is unavailable.

## Current integration boundary

At application startup, the provider wraps only `velia_developer_coding_service._plan_prompt`. It receives the existing goal, candidate paths, and GitHub evidence. If exact-head Repowise evidence is valid, only the evidence string is replaced. Candidate paths, plan normalization, allowed files, execution patches, CI Repair, review loop, and merge policy remain under their existing controls.

The Autopilot CI status endpoint exposes:

- `repowise_context_enabled`;
- `repowise_context_configured`;
- `repowise_context_read_only=true`;
- `repowise_context_fail_open=true`;
- `repowise_context_exact_sha_required=true`.

## Next implementation

A separate `velia-repowise` service is still required before enabling the feature. It must own isolated exact-SHA worktrees and persistent indexes, expose only read-only endpoints, isolate Repowise-generated project files, enforce repository allowlisting, and have no GitHub write credentials.
