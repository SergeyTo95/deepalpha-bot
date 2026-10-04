# VELIA request understanding

VELIA restores recognizable spelling and dictation errors from wording, sound
and task context, then answers the actual question. Health questions follow the
same rule: a recognizable misspelling does not require the user to confirm its
spelling before receiving useful help. Recognizing a word does not establish an
additional diagnosis or circumstance.

The shared rules in `velia_request_understanding.py` apply to browser, Desktop,
native Flash, PRO and voice. User corrections take priority over earlier assistant
guesses. Search pages cannot establish facts about the user. Numbers, units,
negations, dates, quoted literals, identifiers and the original author's gender
and perspective are retained.
Restrictions also govern the actual proposed actions, rather than merely the
recognized wording. A restart that must retain router settings uses power cycling;
holding Reset is a factory reset and violates that restriction.

## Interpretation and generation

Before retrieval, the browser asks its existing free Flash worker for a bounded
structured decision: answer, search or clarify. Original wording and recent
history are passed without retrieved pages. A local Russian/English vocabulary
(`wordfreq==3.1.1`) offers nearby spellings; quoted text, paths, identifiers,
acronyms, camelCase and values are protected. No external spelling service or
additional spelling-model call is used.

Both direct and search decisions can carry a resolved spelling as an exact
original span and a nearby candidate. A dependency-free guard rejects unrelated
meanings, extra facts, changed literals, short units, negations and partial-word edits. The context model
chooses the reading; edit distance alone never promotes an ambiguous turn into
an answer. Malformed decisions fail before guessed retrieval.

Resolved spellings reach the normal answer generator rather than the prepared
clarification path. A canonical handoff carries exact end-relative positions and a bounded reading.
The inference renderer applies that repair to a copy of the question before
the model sees it. The raw question remains the stored prefix of the handoff.
Unique independent sound-alike words retain a separating comma instead of
becoming an invented compound. The model-selected words are never overridden. Search uses the recognized
wording and retains bounded source provenance; its wrapper repeats the recognized question after source snippets and asks for
a useful answer rather than waiting for spelling confirmation.

Clarification remains for a substantial unknown term or multiple plausible
meanings requiring different answers. VELIA can answer the useful part of an
otherwise incomplete question. It never guesses a number, dose or precise device
model. Genuine clarification emits one complete question without retrieval or a
second generator. Earlier position-only and candidate clarification handoffs
remain readable for stored conversations.

Account request IDs cache the interpretation and source context. Account history
restores the exact original question using its authenticated context metadata.
The resolved-spelling marker is hidden from the browser. Native search uses the
existing `LIVE_WEB_CONTEXT_UNTRUSTED` envelope so Flash can shorten only added
sources to its measured input budget, and it avoids a duplicate native search.
For medical recommendations the interpretation selects `official_health` and
an English query about the main task. Retrieved results are restricted to official
health services and public-health authorities (NHS, CDC, NHLBI, NIDDK, NICE, WHO).
The server verifies the hostname after retrieval, including rejecting lookalike
domains. It makes one search and does not fall back to clinic advice if no official
result is available. The original conditions remain in the inference question;
search need not combine every uncommon condition into an ineffective query.
The answer gives general useful steps without establishing an intolerance from
an isolated substance name or prescribing unsupported food exclusions.

Quotas, credentials, permissions, tool-call correlations and paid fallback rules
retain their existing limits.

## Verification

Local regression checks: **305 passed**, one PostgreSQL integration check skipped
because no local test database was supplied. The deployed gate separately checks
real PostgreSQL quota enforcement and source caching.

HTTP checks exercise resolved spelling through normal generation for direct and
search turns, clean account history and replay, Flash/PRO compatibility, original
input/history, protected identifiers and values, rejected invented diagnoses and
unknown-term clarifications. Source tests exercise official-domain restriction,
lookalike-domain rejection and no unverified fallback. Native checks verify that restored text reaches the
free generator instead of returning a prepared spelling question and that
browser-provided native sources do not trigger another search.

The pre-deploy understanding gate requires eight complete real browser SSE
answers on an isolated loopback guest server with a temporary quota database:

- The exact screenshot query receives substantive weight-loss guidance, with the
  nearby readings `гистамин` and `апноэ`, rather than a spelling-confirmation
  question. The pre-search reading must include both restored words; the answer must address
  the useful task rather than give a spelling lecture. No invented gestational
  diabetes, pregnancy, confusion of apnea with fainting,
  or unrelated source cards or unsupported broad food exclusions. Sources must
  be official; the complete answer must finish with `stop`.
- An unknown device name remains a complete clarification.
- Arithmetic returns 391.
- A corrected Ubuntu context returns Ctrl+Alt+T.
- Finance spelling yields a revenue/profit explanation with expenses.
- Router spelling yields restart instructions without requesting confirmation.
- Python syntax retains `app.py` and returns the corrected line.
- A recognized phone brand with an unknown model receives general screenshot
  instructions without inventing the model or asking about recognizable spelling.

Five additional live interpretation cases cover the unknown phone model,
explicit allergy with a negation, alternate medical wording, English spelling,
and current-information search. The existing gateway gate also
checks PRO tool/SSE correlation, persistent quotas and search provenance, plus
two live Harness rounds with all 24 installed tool declarations available. This
operator gate does not establish real-owner Desktop pairing.

Successful runtime receipts are stored under `docs/verification`. Earlier
`request-restoration-*` receipts describe the older confirmation-only behavior;
they are historical evidence, not verification of direct answers. The public
workspace network quota was already exhausted by those earlier runs, so the new
quality gate uses the same production guest route on an isolated server. It does
not reset public quota or spend a user's account messages.

The free CPU worker remains slow. This change does not claim perfect understanding
of every question or a latency improvement.

The medical fixture qualifies restored wording and a complete task response;
it does not clinically validate every recommendation. Historical samples below
still inferred histamine sensitivity and a weight-loss rate. The context-grounding
revision rejects those observed assumptions. Some generated Russian wording
still remains imperfect; a passing fixture does not certify arbitrary answers.

The screenshot follow-up strengthens answer qualification: known incorrect
Russian action verbs, personal safety promises and the observed pounds/kg rate
error are rejected. Finance checks also reject the observed incorrect Russian
wording. Health retrieval uses a separate bounded primary-task query; accompanying
conditions remain in the unchanged inference question. One provider request asks
for six candidates, keeps up to three distinct pages, and deduplicates anchors
and repeated titles from the same host. Health excerpts retain up to 900 characters.
Citation rules require support for the adjacent claim and preserve numerical units
and periods. The instruction requires omitting unsupported numerical recommendations;
this is not a guarantee that the model follows it in every future answer.

Plain-text Flash generation uses a factual non-thinking profile
(`temperature=0.3`, `top_p=0.8`, `top_k=20`, `min_p=0.0`,
`presence_penalty=0.0`). The existing qualified tool profile is preserved.
Ordinary answers use one answer-generation call. Guest medical advice and turns
with unspecified personal properties additionally pass through a focused editor
on the same free worker before their text is emitted.

The direct-answer preview was deployed on 2026-10-04 with gateway commit
`93a0505665d82328b3427cb909c906b23ab68df3` (deployment
`c5da94b1-c265-4afd-87fe-2bb8576ce041`) and native commit
`4d413d3b6dbc4d5f77f6ec4fa32c83db9cea1c7f` (deployment
`c4315f24-1176-4d6f-ba0f-d16746ddade8`). Both deployments succeeded.
The gateway image passed 201 checks; all ten live understanding cases and the
24-tool, two-round Harness gate passed. Public `/health` and `/` returned HTTP 200.
The full receipt and actual generated replies are retained in
[the deployment evidence](verification/request-direct-understanding-deployments-2026-10-04.json).

The screenshot follow-up succeeded with gateway commit
`c0df9f555cf0b36231a6b60639f2ba85d4e7b00d` (deployment
`c3b18ac7-a717-45e4-8ccc-0a9475b1c7da`) and native commit
`73aa4270166396731711935c5a6dcf43316d87e6` (deployment
`c27ef7b0-9c2c-441c-8301-335f9857b584`). The gateway image passed 208 checks,
all ten live cases and the 24-tool, two-round Harness gate passed, and public
`/health` and `/` returned HTTP 200. The medical example now uses the primary
task query `healthy weight loss advice`, three distinct weight/nutrition sources,
and the correct action word `взвешивайтесь`. Its final sentence still assumes an
unconfirmed allergy. Medical accuracy is not fully solved or clinically certified.
[The follow-up evidence](verification/request-answer-quality-2026-10-04.json)
retains the actual replies and this limitation.

## Explicit user context

The context-grounding revision separates spelling restoration from personal facts. The same
pre-search decision now supplies bounded `context` entries: an exact original
quote, semantic kind, and `stated` or `unspecified`. The kind separates a
condition/reaction, a bare substance, a device/brand, software, a measurement,
or another kind of context. The server keeps a bare substance unspecified,
even when the planner labels its mere presence as stated. A mentioned substance or brand does not
establish a diagnosis, cause, or exact device model. A missing property can stay
unspecified while the useful task receives an immediate answer.

The server validates those quotes and carries only positions and statuses in
cached metadata. New diagnoses, partial-word evidence, duplicate quotes and
unknown status values are rejected. Immediately preceding negations remain part of
the evidence span. Repeating an exact original quote as its own correction is a
harmless no-op. One internal retry can repair an invalid planner schema without
changing the original question or performing a search for the invalid plan. The inference wrapper renders the exact
user evidence separately from retrieved pages. Explicitly stated allergy and
negative facts remain usable; the policy does not ban medical vocabulary.
Authenticated replay preserves this context while account history restores the
original question without displaying the annotation. No paid fallback or new
model is added.

The local regression suite passes **305 checks**, with the existing local
PostgreSQL skip. The live gate now
rejects the observed unconfirmed personal-allergy claim in the original fixture,
checks explicit versus unspecified evidence for that query, and adds an
unknown-phone-model browser answer plus a stated-allergy/negation interpretation.
An unspecified substance does not justify conditional personal advice either.
Deployment qualification and actual model responses are required before reporting
this revision as active.

The live text criterion also rejects the observed malformed Russian forms
`астеме` and `пульмоном`. It accepts an explanation that Reset performs a factory
reset and violates the requested preservation of settings, while still rejecting
a positive instruction to press Reset, including one placed after a warning.

## Guest answer review

Prompt guidance alone did not reliably prevent the observed unsupported
histamine-related personal advice. Guest medical answers, plus other guest turns
with unspecified properties, now buffer the draft privately and send it to a
focused editor on the same free Flash worker. The editor receives the request,
typed user evidence with validated spellings, draft, and bounded source excerpts.
It checks unsupported personal conditions, missing stated conditions, grammar,
and nearby source support. The browser receives only the complete edited text.
Public source cards and the single guest quota reservation retain their existing
semantics; processing comments keep the SSE stream active without exposing text.

An empty or truncated draft after a completed transport may be completed by the
editor using the original request and evidence. Only its complete, nonempty
`stop` response is emitted. Review failure or malformed output cannot fall back
to the unreviewed answer. A public error replaces silent empty output. This review adds
latency and uses a 600-second route/probe deadline; ordinary answers preserve the
existing path. Account/native responses share understanding and sampling updates
but do not use this guest-only editor. The editor remains a model-based check,
not a clinical certification or a guarantee for arbitrary future questions.

The guest editor also checks lexical coverage of named conditions (allowing
ordinary case endings) and the presence and bounds of inline source indices.
A missing condition or citation triggers one private repair with the same
question, typed context, and sources. Repeated omissions produce a public error,
never the incomplete draft. This bounded check covers omissions, not the truth
or meaning of medical claims. The conditional extra call reserves no new guest
message.

For personal health advice the editor distinguishes general reference figures
from new numerical prescriptions. A bounded lexical check detects new quantities
in recommendation sentences and requests one private repair. Original quantities,
decimal notation, neutral reference facts and requested calculations are retained.
This supplements the prompt; it is not a complete semantic or clinical verifier.
The live activity criterion accepts the normal synonym `активность` and separately
rejects the observed unrequested calorie/activity prescriptions.

The focused editor uses a private `publish_reviewed_answer` result with bounded
paragraph text and source IDs. The server validates IDs and renders references
next to their paragraphs; no tool envelope or private draft is public. One
combined repair budget covers malformed structured output or missing coverage.
Compatible complete plain editor output still passes the same checks. Repeated
invalid output produces an error without an unreviewed fallback.

For direct answers, a nonessential Latin/Cyrillic recoding of a stated
device/software name is discarded while the exact original name and unknown
properties remain. This does not apply to search terms, diagnoses, new model
numbers or a changed word list. The private gate now checks the unknown-phone
property before the longer browser examples, with diagnostics for fixed fixtures
only. No production user messages are logged by that callback.

The editor rejects prescriptive paragraphs that depend on an unspecified
substance or measurement, including conditional instructions such as an exclusion
diet for a possible reaction. It asks for one private repair within the same
combined budget. The check uses the validated entity quote rather than a list of
medical diagnoses. It does not establish source entailment or clinical correctness.

## Context-grounding deployment, 2026-10-04

Gateway commit `4668a9ed7d5a2c7844715b8aecf4d631c754befd`, deployment
`b9efb92a-c191-4938-8f35-351d8c9d5af2`, passed the gate and is active in
the PR577 browser preview. Native commit
`7f6f0cf4ae67896cab54322126a3cba012b9d971`, deployment
`50017fe8-605c-430a-986a-cfbc421dd737`, also succeeded and passed real
coding and Russian adapter probes on the free worker.

Local checks passed **305 tests**, with one existing PostgreSQL skip. The gateway
image passed **272 tests**. All five live interpretation and eight full browser
SSE cases passed. The actual medical answer retained asthma and apnea, answered
directly, and did not invent an allergy, gestational diabetes, a numerical target,
or a dietary exclusion for an unspecified substance. Router settings and the
Python string literal were retained. The shipped Harness completed two model
rounds with all 24 installed tool declarations. Real PostgreSQL quota enforcement
accepted 30 and rejected 8 concurrent reservations; persistent source caching
and PRO tool-result correlation passed. Public `/health` and `/` returned HTTP 200.

The actual medical example took **229.09 seconds**; the other browser examples
took **40.09–157.12 seconds**. No latency improvement is claimed. Some Russian
wording remains imperfect, including the observed finance noun ending and phone
button naming. The editor remains a model-based check; valid source IDs do not
establish claim entailment or clinical accuracy. Account/native responses do not
use the guest-only editor. There is no paid fallback and no public guest messages
were spent by the isolated qualification route.

The actual responses, deployed commits, gate receipt and limitations are retained
in [the context-grounding evidence](verification/request-context-grounding-2026-10-04.json).
