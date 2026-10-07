# VELIA Work: multi-agent foundation

This adds the authenticated Russian Web section **Работа и заработок** to the existing gateway. It uses the account's existing Flash conversation endpoint. There is no Kimi or paid-model fallback.

## Shipped behavior

- Persistent PostgreSQL workspace per authenticated owner; SQLite is used only in tests.
- Isolated role conversations: manager, executor, reviewer, treasurer, plus a proposal writer for autonomously discovered tasks. Each receives a bounded role prompt and explicit task context. The existing identity authority is checked before each role request, and the existing model reservation and account generation accounting are retained.
- Queue, background execution, per-role persisted results, cancellation, restart recovery and retry from the last saved role. Jobs carry expiring leases; a cancelled or stale worker cannot finish a job. Each pending generation uses a stable role-specific idempotency key. Invalid structured output may be regenerated with a new attempt key.
- Manager can decline a task; reviewer can return needs_revision. Ready always means a prepared draft, never customer acceptance, external delivery or payment. The first executor produces text artifacts only; it cannot run code or files, register accounts, accept contracts or submit marketplace work.
- Existing configured WebSearch supplies candidate links. Search results are not verified orders. No marketplace account is connected by this release.
- Optional owner-enabled autonomous preparation: a saved search direction, 15–1440 minute interval and 1–3 new tasks per UTC day. The scheduler searches with the existing provider, imports at most one new candidate per scan, deduplicates exact source URLs and invokes the role pipeline. Unknown budgets stay zero/unknown. The manager must decline candidates without enough requirements; search snippets are not accepted contracts. The proposal writer prepares a draft without inventing the owner's credentials. No external proposal or work submission is made.
- Autonomous sessions stay in memory, not in the database. Following a server restart or account expiry, the owner must open the section with a valid login. This pilot is not an always-on credential service. Disabling/changing policy invalidates an in-flight search; already-running jobs can be cancelled separately.
- Ready text results can be downloaded as an owner-authenticated ZIP containing result.txt, review.json, task.json and, for autonomous jobs, proposal-draft.txt. No model-generated code executes while creating the archive.
- Owner financial mandate: working reserve, expense limit, intended owner address/network, percentage for the agent's working budget. Six-decimal amounts remain strings and are validated with Decimal.
- Treasurer makes a structured hold/pay_owner decision. A pay_owner decision records a durable payout request exclusively to the owner destination from the stored mandate, not a model-supplied address. Owner-requested payouts use the same destination. Both are explicitly blocked until a wallet integration exists. Balance, available funds and confirmed earnings remain unknown; expected task prices never credit a balance.

## Activation

Set on the isolated gateway preview only:

```
VELIA_WORK_ENABLED=true
VELIA_WORK_DATABASE_URL=<persistent PostgreSQL DSN>
```

If the dedicated DSN is omitted, the existing `VELIA_WEB_GUEST_DATABASE_URL` is reused in a separate `velia_work_workspace` table. The feature remains unavailable without a DSN; there is no ephemeral production fallback. Database initialization happens at startup. The sidebar section still displays an honest unavailable state when configuration is missing.

The existing encrypted Web session, owner rollout and Flash configuration must work. No credentials, cookies or signing keys are stored in the workspace. Background execution retains the session in memory; an expired account token causes a saved failure and requires normal owner reauthentication before retry. It does not rotate an invisible browser cookie.

## API

Authenticated `/web-api/v1/work/` routes:
- GET status, workspace, jobs/{id}
- PUT mandate
- POST jobs, jobs/{id}/run, jobs/{id}/cancel, discover, payouts
- PUT autonomy; POST scan; GET jobs/{id}/artifacts

Mutations require the existing same-origin JSON boundary. Job IDs alone grant no access. Job creation and payout requests reject a reused request ID with different data. One running job per owner is enforced under a database row lock. Four background jobs per gateway process are allowed; deploy a single worker replica for this pilot. Cancellation closes the ongoing generation through the existing streaming client.

Public workspace listing omits full briefs/results; detailed artifacts are loaded for an owned job. Outputs are plain text in the browser, not executable markup. Limits: 100 jobs and 100 payout requests per owner, 6,000-character task briefs, 16,000-character role outputs, five opportunity searches per minute per owner. No actual money moves; there is no chain ledger or deposit address yet.

## Next integrations

1. Official Upwork MCP account authorization and its draft/confirmation protocol. Do not replace it with an unauthorized browser bot. Exact scopes and allowed submissions require live account acceptance.
2. Sandboxed code/data/file execution with actual artifact tests and resource accounting. Model review alone is not proof of correctness.
3. USDT wallet service controlled through agent tools: confirmed deposit receipts, exact-chain token identity, separate signing keys, mandate checks at signing time, gas budgets and owner withdrawals. Model text never directly changes a balance or destination.
4. Ledger-backed expense/reserve/profit allocation and autonomous owner distributions. The current mandate is configuration; its economics are not applied to fabricated funds.

## Validation

Work tests cover tenant isolation, decimal validation, idempotency, serialized claims, stale-worker rejection, restart recovery, role chain/retry/cancellation, unknown finances, agent-initiated blocked payouts, fixed owner destination, account identity/Flash/idempotency routing and concurrent HTTP capacity. The DOM smoke covers monetary strings, escaped artifacts, unknown balances, disabled configuration, form preservation and abort cleanup.

Tests use synthetic provider outputs/audio/browser fixtures; no live owner account, marketplace contract, Flash role-quality acceptance or USDT transfer is claimed. SpeechKit stays unconfigured. Production activation and merge are not included. Upwork's official https://www.upwork.com/ai/mcp documents OAuth 2.1 dynamic client registration and draft-confirm writes. Its public OAuth discovery addresses returned HTTP 403 from this development environment on 2026-10-07; an authenticated Upwork connector has not been shipped or qualified.
