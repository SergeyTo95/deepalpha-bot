# VELIA Autoloop adapter

This implements the event / round / ask / later design studied in
https://github.com/KKKKhazix/agent-autoloop without installing another agent,
copying its implementation, upgrading Harness, or creating infrastructure.

The account backend owns schedules and signed events in PostgreSQL and admits
them atomically into `velia_background_chat`. Existing admission, account
budgets, selected-model transport, chat publication and worker remain in use.
The gateway only authenticates/proxies controls and renders state. Closing its
tab or letting the gateway sleep does not stop the backend scheduler.

## Browser workflow

The **Повторять…** button inserts a draft instruction, for example:
`Повторяй каждые 60 минут: Проверь актуальный статус проекта`.
Submitting saves a rule on the selected Flash, Quantum or PRO tier. The first
scheduled run occurs after the interval. Controls pause future events or submit
a manual event. Default: one initial job per UTC day per rule, at most three
per account across rules; a manual event also obeys this limit.

An explicit coding instruction resolves a connected repository and uses
`kind=coding`. The existing superagent prepares a plan and token quote. The job
waits for an explicit answer before the existing executor can write code. No
automatic merge or deployment permission is added by this adapter. An uncertain
coding turn after a process crash fails for inspection rather than replaying a
possible GitHub write.

Question and answer messages are persisted in the original conversation. Answers
are owner-scoped and idempotent. The same job, model and history resume. Waiting
and delayed jobs occupy no inference worker; another foreground/background job
can use the account. There are at most eight deliberate continuations per job.
Retries of a crashed round retain the existing four-attempt ceiling.

## API

Authenticated `/mobile-api/v1/autoloop/rules`: GET lists owner rules; POST creates
one with `conversation_id`, `model` (`flash|quantum|pro`), `instruction`, and
`idempotency_key`. Optional: `interval_minutes` (0 or 15..10080), `event_types`,
`kind` (`answer|coding`), `project_id`, `web_search`, `enabled`,
`max_runs_per_day` (1..3). The public browser proxy uses `/web-api/v1/autoloop/rules`
and model IDs `velia-flash|velia-quantum|velia-pro`.

POST `rules/{id}/enable`, `disable` accept `{}`. POST `rules/{id}/events` takes
`{id,type,subject,detail}`. Repeated IDs with changed payloads are rejected.
Pending events for one subject coalesce; dispatched inputs are immutable.

GitHub event rules require an owned `project_id`. Obtain their HMAC secret from
the authenticated account API `rules/{id}/github-secret` (never exposed through
the browser/chat). Configure the repository's webhook to POST to
`/mobile-api/v1/autoloop/github/{rule_id}` with this secret. Signature verification,
bound repository and configured event type are all required. Supported events:
`issues.opened`, `issues.reopened`, `issue_comment.created`, `check_run.completed`.
No repository webhook is installed automatically by this change.

POST `/mobile-api/v1/background-tasks/{job_id}/answer` accepts
`{answer,idempotency_key}`. The browser proxies this through the same path under
`/web-api/v1` with its existing session and CSRF checks.

## Operations and verification

The adapter is available alongside the existing free-model/background-chat
feature flags. `VELIA_AUTOLOOP_ENABLED=false` disables new intake and execution;
no rules are created automatically. It uses no new server or subscription.
Model work and hosting still consume existing account resources.

Tests cover restart recovery, event deduplication/coalescing, atomic admission,
concurrent dispatch, daily quotas, signed repository events, owner isolation,
question/resume, delayed rounds, circuit breaking and the HTTP boundary. The
PostgreSQL fixture additionally verifies real chat publication and linkage.
Live coding quality and real repository-webhook delivery are separate acceptance
steps; fixture tests do not establish them.
