# CRM through the unified chat

This module adds contacts, user-recorded communication, agreements/follow-ups and
deals to Velia's normal browser chat. It uses the selected Flash, Quantum or PRO
model to extract typed instructions; the CRM engine performs deterministic reads,
validation and edits. No new infrastructure or paid provider is introduced.

## Supported behavior

- Create/edit/archive/restore/delete clients, partners, investors, suppliers,
  colleagues and other contacts. Store company, email, phone, Telegram handle,
  tags, projects and notes. Duplicate email, phone or Telegram identifiers fail;
  namesakes remain separate and require clarification. No silent contact merge.
- Record inbound, outbound, meeting and note entries with calendar dates and
  source time zones. Future interactions fail validation; future work belongs in
  follow-up tasks. Notes/drafts do not advance the last actual communication date.
- List unanswered inbound entries only when requires_reply is explicitly set and
  there is no linked outbound reply or explicit resolved marker. An unrelated
  outgoing message does not close all earlier inbound messages. Waiting longer
  than a threshold is distinct from simply not communicating recently. Contacts
  without recorded history are unknown, not automatically stale.
- Record next steps/commitments with optional due dates and open/done/cancelled
  status. Read overdue tasks and deterministic next-step suggestions. A CRM due
  date is not itself a scheduled notification.
- Track deals through lead/discovery/proposal/negotiation/won/lost, with contacts,
  project, optional value/currency and notes. Deal amounts are not recorded cash
  receipts; this module does not alter the financial ledger.
- Apply a reviewed batch of up to eight linked edits atomically, e.g. create an
  investor, record a meeting and create the promised follow-up. Earlier results
  can be referenced by $0/$1 in ID fields only. A failed child commits nothing.

## Existing tool connections

On an explicit request, a selected contact card can be copied to the encrypted
Notebook, a reminder can be created through the real Pulse service, or a new
meeting can be passed to the existing Google Calendar flow. Every such action
uses the current account and requires concrete review/confirmation. The calendar
handoff permits only calendar list/freebusy/create tools, preserving the existing
Google confirmation and no-invitation behavior. Calendar links are recorded only
from successful provider evidence, not generated text. If final model wording
fails after provider creation, the confirmed provider result remains authoritative.

Notebook/Pulse/Calendar operations and CRM link updates are separate commits.
Partial failures report the successful external operation and the unconfirmed link;
the workflow does not repeat an external creation to fix metadata. Stable request
IDs and attempt markers reduce duplicate reminder/calendar writes. Notes larger
than the Notebook limit are explicitly exported as an excerpt; the full CRM card
remains in CRM. Source chat text can also remain in chat history.

There is no automatic synchronization of CRM edits/deletions to exported notes,
reminders or calendar events. Changing a CRM due date does not reschedule an
existing Pulse reminder. Deleting a contact removes its CRM history/tasks and
orphaned deals but does not delete other contacts or shared deals; external
resources retain their separate controls. Existing meetings are not silently
recreated as a substitute for unsupported rescheduling.

## Source and permission boundaries

This version uses user-provided records, including pasted text or small CSV/TXT
attachments, plus explicit, reviewed Gmail history imports. It does not scrape
contact books, read Telegram, send messages, invite participants, auto-resolve
correspondence or run background relationship monitoring. Reports identify their
recorded-data scope.
Prepared responses are text only and do not become sent-history entries. The
current existing service connections still operate independently on explicit
requests. Inference extraction is fixture tested; live provider acceptance still
requires a connected test account.

### Gmail history import

Ask to import correspondence with an existing CRM contact for an inclusive date
range, e.g. `Импортируй переписку с инвестором Анной из Gmail в CRM за октябрь 2026`.
The contact must have an explicit, supported email address. Velia uses the existing
Google OAuth connection and only GET requests to Gmail profile, messages list and
message metadata. No new keys, infrastructure, sends, read-state updates or
attachments are involved. Exact parsed addresses, DRAFT/SPAM/TRASH exclusions,
SENT label and provider internalDate are checked server-side. Date filters use
epoch bounds for the selected time zone; untrusted Date headers do not set dates.
Outgoing aliases are conservatively skipped unless they match the mailbox address.

One request inspects a bounded list of up to 30 messages, imports up to eight new
subjects/snippets, and explicitly reports partial results. Already imported IDs
are skipped before metadata reads. Repeating can import remaining messages in the
first page; if more pages remain, narrow the period. No full-inbox coverage is
claimed. Duplicate provenance is scoped to Gmail mailbox/message ID across CRM,
so the same email is not silently assigned to several contacts.

The server seals a reviewed proposal for ten minutes, bound to user, session,
Google connection and CRM revision. Commit accepts only that proposal, with
confirmed=true. Editing source identifiers through ordinary mutation routes is
rejected. A stale revision, altered proposal or account change commits nothing.
Imported rows never infer requires_reply, resolved or reply_to automatically.
Source credentials never enter planner prompts; message excerpts are presented
for review without another model call. Later card queries can expose relevant
saved excerpts to the selected model like other CRM data.

## Storage, API and capacity

`CrmStore` writes one encrypted owner-bound document to `velia_crm_workspace` in
the existing VELIA_WEB_GUEST_DATABASE_URL. Encryption and owner indexing use
domain-separated keys derived from VELIA_WEB_SESSION_KEY; keep that key stable or
migrate ciphertext during rotation. Account identity is taken only from the
authenticated session. Origin checks protect all endpoints; responses use no-store.
Failure logs omit CRM payloads. Revision compare-and-swap prevents stale writes.
Stored ciphertext includes the owner binding, so copying it to another account
fails closed. No credentials are accepted in the CRM model contract.

`POST /web-api/v1/crm/plan` uses the normal selected-model authorization/quota path
with internet search disabled. Other POST actions are context, query, preview and
mutate, mail-preview and mail-commit. Edits require expected revision and stable operation_id; commit requires
confirmed=true. Query validation occurs before commit. Context and planner lookup
content are bounded; larger match sets need a narrower selection or pagination.

Capacity: 500 contacts, 3,000 interaction entries, 1,000 tasks, 500 deals, 2 MiB per
account document, 64 KiB per request, up to 50 query cards and 100 timeline entries
per card. CSV/TXT input is limited to one 12,000-byte attachment. Flash retains its
existing 1,536-token output ceiling; large batch/extraction requests may need to
be split. The service build packages and tests CRM alongside existing modules.

## Synthetic acceptance examples

1. `Добавь инвестора Анну из фонда Aurora, email anna@example.org, проект Velia.`
2. `Запиши: 1 октября Анна попросила презентацию по email, нужно ей ответить.`
3. `Кому из инвесторов мы не отвечали больше 14 дней?`
4. `Мы договорились отправить Анне отчёт 15 ноября. Запиши следующий шаг.`
5. `Сохрани карточку Анны в заметки.`
6. `Напомни позвонить инвестору Анне завтра в 10:00.`
7. `Назначь встречу с инвестором Анной 16 ноября в 16:00 на полчаса.`

Verify real results before reporting success, namesake clarification, cancellation,
expired revisions and a second account's isolation. This branch has not been
merged; it is stacked on the financial-analyst branch. Deployment status is tracked
in Railway, independently of this source document.
