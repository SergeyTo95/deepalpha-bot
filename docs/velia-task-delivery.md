# Background task delivery

Complex text requests and explicit missions are accepted into the existing encrypted
mission store before planning. The selected Flash, Quantum or PRO model plans and
executes on the server. There is one background execution slot and an independent
foreground admission lane within the gateway's existing two-generation limit.
Ordinary discussion during a task uses a local encrypted continuation with its
current context, so a native account conversation lock cannot block it. The model
selection is preserved. Simple questions keep their ordinary chat path. Existing
attachment, Browser Agent and explicitly selected native background flows retain
their supported execution paths.

The queue is bounded to 64 in-memory workers and 10 persisted missions per account.
An owner lock plus a database check serializes that owner's missions; execution
rotates the queue lease so another replica cannot corrupt a running task. Expired
queue/planning/execution leases become interrupted. A restart preserves results
and lets the user resume; it does not automatically replay an uncertain external
write. Credentials remain in the authenticated session rather than being saved in
mission data. Clarifying answers and exact-action approval are separate operations.

Each task stores its originating chat, limited context, selected model and durable
status notices. The browser restores the task request, progress card and verified
result in that chat, including after local cache loss or native-history refresh.
Stable notice IDs prevent repeated messages. Deleting a chat detaches recovery;
it does not cancel or delete the separately saved mission. Every mission response
carries the authenticated account fingerprint, and mutations must match the
browser's fingerprint before any model or service action runs.

Completion, clarification, approval, failure, restart interruption and ambiguous
external execution have distinct notifications. The persistent task inbox is
merged into Pulse. Existing Pulse's authenticated one-off API transports notices
through its established scheduler and push delivery, preserving preview consent,
quiet hours and delivery receipts. The adapter stores and claims an occurrence
before sending. Transport acceptance is not a push-delivery receipt. A failed or
ambiguous transport never changes the task's verified result and is not blindly
resent. Read system notices complete their transport reminders to release Pulse
quota; that operation never approves or completes the mission itself. Push still
requires the user's existing browser permission/subscription and healthy backend
workers. No new service, push provider or GPU is provisioned.
