# Projects and DeepAlpha in Velia

General creative projects now have a passport: title, goal, audience, style and
constraints. These are separate from the existing GitHub installation projects.
Every passport update checks the expected revision and saves an immutable new
revision. Restoring a previous passport submits its fields as a new revision.

The authenticated mobile API exposes `/projects`, `/projects/{id}` and
`/project-resources`. A resource is a chat, DeepAlpha research conversation or
Studio image/video/music session. Creating the resource and its underlying
conversation/session is one transaction. Client request keys are bound to the
request payload; a changed payload returns 409. Reads, writes and composite
foreign keys enforce owner boundaries. Project creation is capped at 100 per
owner, resources at 2000 and passport versions at 1000. History is paginated.

Project chats receive the current passport. Studio incorporates relevant style,
audience and constraints into the provider prompt without changing the original
request stored in history or adding an extra model call. The existing reference
image editing, media generation history and capability gates continue to apply.
Passport versions do not represent deterministic media editing or media rollback.

DeepAlpha uses the ordinary hardened chat sender for ownership, budget checks,
idempotency, storage and disconnect recovery. Research bypasses action, code and
media planners. Evidence collection uses at most one search and one explicit
market lookup: a crypto quote or a Polymarket event/market. Nested market links
select the exact child. Event snapshots cover at most three markets and record
truncation, outcome prices and closed/active state. Market prices are not treated
as independent forecast probabilities. Search uses the configured Brave provider or the existing optional
news RSS fallback. Headlines are marked as partial coverage. Arbitrary URLs in
prompts are never fetched. Provider responses reject redirects and are bounded.
Quotes require a matching symbol, positive finite price and recent timestamp.
When no verifiable evidence is available, no model call runs.

Evidence is stored per research request with retrieval time, source links,
coverage gaps, project ID and passport revision. The final answer includes a deterministic
source footer. Opening history does not repeat generation. A tenant-scoped,
bounded 60-second evidence cache coalesces identical in-process requests; the
original retrieval time is retained. The existing daily plugin quota bounds
uncached research. `VELIA_LIVE_PLUGINS_ENABLED=false` disables new research.

Saved research can be moved into a project without regenerating it. The update
checks the previous project assignment and owner before changing the link; old
evidence retains its original project provenance. Mobile calls include an
expected-account header to prevent a request crossing an account switch.

The PostgreSQL workflow tests ownership, payload-bound idempotency, concurrent
passport updates and revision history. Unit/HTTP tests cover unavailable evidence,
unsafe links, malformed and stale quotes, bounded responses, duplicate coalescing,
anonymous requests and oversized chunked JSON. Android integration is delivered
in the companion PR in `deepalpha-android`.

This change does not enable trading, Software Factory rollout flags or additional
GPU duration/provider capabilities. GPU video acceptance remains a separate task.
