# Velia chat → superagent → existing GitHub App

This adapter uses the actual reusable `Engine` from DeepSeek's MIT-licensed
`dsh-superagent-prototype`, pinned at
`6337fc823ce20251c8de8085948165c81aaa701e`. It does not load the upstream
GitHub notifications Source or standalone runtime. A separate GitHub user
account is therefore unnecessary.

In a signed-in Velia conversation, name a connected repository and
describe a code change. The existing developer router prepares a bounded plan
and quote. Approve that plan in the same conversation (`Выполняй план`). The
existing billing guard charges the approved quote, then the custom Executor
uses repo-scoped App installation tokens to create a work branch, commits and
a draft PR. Its answer and PR URL use the existing persistent chat/SSE path.
On the current free API tiers, planning and patches use the selected Flash,
Quantum or Pro configuration with no paid fallback, no provider reserves and
one physical attempt per model call. The plan records its mode; approval from
another mode is rejected. Ordinary non-coding conversation retains its existing
path. Legacy paid Pro keeps its original quoted coding executor.

Enable with `VELIA_SUPERAGENT_CHAT_ENABLED=true` on the existing backend.
The existing developer, coding and write flags and GitHub App permissions are
also required. Gateway needs no GitHub secrets or additional service.

The authenticated approval becomes a direct-human Source trigger. Policy
binds the mandate to user, conversation, project, repository and base branch;
inferred work is disabled. The Engine creates an Assignment before a write.
Its snapshot and the planned→running claim commit atomically in PostgreSQL.
Only the process that claimed a planned job can launch it. Progress and final
reports are persisted after each step. A failed/interrupted write is not
automatically retried; inspect the stored branch/commits/PR first. Execution
currently uses the existing bounded synchronous chat worker, not a new daemon.

GitHub secrets remain in the Python App executor. The short-lived pure-domain
Node process receives only task metadata, is launched with an empty credential
environment, and cannot execute model-generated shell commands. Existing path,
branch, operation, file-size and cost guards stay in force, as do existing local
syntax checks. It queries GitHub checks; it does not run a project test suite or
claim CI passed. Each selected-model task turn has at most 17 model calls,
additionally bounded by the existing maximum steps and patch attempts.

`vendor/src` is the unmodified upstream core; `vendor/LICENSE` preserves MIT.
`vendor/provenance.json` records source and compiled artifact SHA-256 hashes.
The committed ES2022 ESM bundle has no package dependencies and uses Node's
built-in crypto only. Production includes pinned Node 24.19.0.

To reproduce the bundle with Node 24 and tsdown 0.22.14 / rolldown 1.2.7:

```sh
pnpm --package tsdown@0.22.14 dlx tsdown agent_core/superagent/chat/vendor/src/index.ts --no-config --no-dts --target es2022 --out-dir agent_core/superagent/chat/vendor/dist --no-sourcemap
node --test agent_core/superagent/chat/bridge.test.mjs
python -m pytest -q tests/test_velia_superagent_chat.py
```
