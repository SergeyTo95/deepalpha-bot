# Velia Pulse

Pulse is a persistent reminder inbox with typed chat actions. It runs in the
existing backend web process; no new service, provider key or GPU is required.
Deploy the backend before the existing browser gateway, then refresh open tabs.
The existing notebook/materials releases are included in the parent commits.

## User flow

The main web chat recognizes explicit reminder requests before ordinary chat.
Simple Russian/English dates, clock times, daily/weekly repeats, weekday dates and
relative minute/hour/day delays need no model call. Missing dates/times remain a
chat draft and can be answered in the same conversation or edited in a form.
Complex reminder requests use the currently selected Flash, Quantum or PRO through
the existing authenticated planner. Models provide typed drafts, never arbitrary
IDs/URLs or delivery settings. Changes use actual owned records and revisions.
A model response saying “saved” without a successful write cannot confirm a save.
The current form and canonical chat acknowledgements use the Russian UI language.

Pulse cards support completion with a result, snooze, an exact new date/time,
cancellation and deletion. Completing a recurring occurrence preserves the series.
A dependent reminder starts only after the user confirms its parent occurrence;
simply delivering or reading a notice never starts the next step. Cancelling a
parent also cancels its still-waiting chain. A reference to a place, visit, note,
material or chat opens that account's actual saved resource; it does not execute
external actions or automatically mark the linked visit complete.

“Напомнить” controls on answers, notebook entries, places and visits open an
editable draft. Date/commitment suggestions also require a click and explicit form
submission. They never silently schedule something from a casual conversation.
The Pulse overview includes reminders needing attention and a link to today's
planned visits. The optional morning digest links back to this overview; it does
not autonomously generate reports or infer bank payments.

## Scheduling and delivery

Times are validated against the supplied IANA timezone. Nonexistent DST wall
clock times are rejected; ambiguous fall-back times use the first occurrence.
Daily/weekly repeats preserve local wall clock time, skipping nonexistent future
slots. Relative chat delays round up to the next minute rather than firing early.
Defaults: quiet hours 22:00–08:00, hidden notification contents, Telegram off,
morning digest off, no unfinished-task repeat. Timezone initializes once from
the browser/reminder and remains user-configurable. Each reminder can bypass
quiet hours only when the user selects that option. One optional follow-up is
bounded to 5–10080 minutes; it stops on completion, snooze or cancellation.

The server ticks every 15 seconds in the existing production web process. A
separate bounded delivery loop keeps slow Telegram requests from delaying the
in-app inbox. Timers, settings and occurrence identities survive restarts.
PostgreSQL row locks with SKIP LOCKED coordinate replicas; unique occurrence keys
prevent duplicate inbox records. On recovery, overdue repeating schedules emit
one catch-up record and move to the next future local slot. Previous unresolved
occurrences are labelled superseded, not completed. Done/snoozed/superseded notifications and read digests older than 90 days are
removed. Uncompleted base notices remain actionable even after being read; the
final result of a one-off reminder is retained in its reminder record.
There are at most 100 active/waiting reminders and 1000 reminder records per user.
Delete old history through Pulse to free that quota. The API returns up to 300
reminders and 100 latest notices; the chat selects at most 12 references.

In-app cards appear while the browser is open and are restored on the next visit.
Delivery while the site is closed requires opting into Telegram and starting the
existing dedicated Velia bot. The bot identity must already be verified by the
existing runtime. No fallback to the legacy bot is allowed. Notifications open
an authenticated Pulse page; URL IDs confer no access and never execute actions.
By default Telegram receives only a generic notice, without task text, amounts
or addresses. Delivery status and task-completion status are separate.

An outbound attempt is claimed before sending. Confirmed success is `sent`;
known API failure is `failed`; transport ambiguity or a process crash is `unknown`.
Uncertain attempts are not automatically resent, preventing automatic duplicates.
The persistent in-app notice remains available in every case. This is not an
exactly-once delivery guarantee for an external messaging service. There is no
Web Push, background browser geolocation or calendar synchronization in this
release. Telegram chat text itself still uses its existing conversation flow;
Pulse creation/management is in the web chat and cards.

## Storage and operation

Reminder text, linked-resource IDs, results and settings are Fernet encrypted
with per-user keys derived from the stable VELIA_NOTEBOOK_SECRET or
MASTER_ENCRYPTION_KEY. Scheduler timestamps, state, user IDs and opaque occurrence
identifiers remain queryable metadata. This is encryption at rest, not protection
from the service operator. Preserve the existing stable secret and backups.
API ownership comes only from the access token; gateway mutations additionally
check the browser account fingerprint. Responses also carry the authenticated
account fingerprint; a cookie/profile mismatch clears the client session before
any inbox can be displayed under the wrong account. No recipient ID can be supplied by a
reminder or model. Logs omit reminder content and credentials.

The tables initialize lazily through the existing PostgreSQL helper. A durable
heartbeat is shown in the UI; stale/disabled delivery is not described as healthy.
VELIA_PULSE_ENABLED=false stops automatic loops but preserves access to saved
records. Automatic loops run only in production/prod environments. The service
must stay running for timely delivery. The frontend poller is account-scoped,
cleared on logout, and does not enable any notification permission by itself.

Validation includes timezone/DST tests, occurrence idempotency, early completion,
recurrence, ownership/revisions, encryption, dependencies/cancellation, quiet
hours, digest, ambiguous Telegram delivery and worker startup/shutdown. SQLite
unit adapters do not exercise PostgreSQL's replica locking; production SQL uses
explicit user/item/notification locks. Whole-app DOM tests cover all three model
selections, clarification, form creation, snooze and completion without model calls.

Follow-up ordinary chat receives the actual reminder rows previously displayed
in that conversation, with bounded text and no unrelated reminder pool. This
lets planning questions refer to the visible cards instead of guessing their
contents. Reminder references still cannot authorize an unsolicited write.
