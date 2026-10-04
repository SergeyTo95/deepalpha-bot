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

Local regression checks: **241 passed**, one PostgreSQL integration check skipped
because no local test database was supplied. The deployed gate separately checks
real PostgreSQL quota enforcement and source caching.

HTTP checks exercise resolved spelling through normal generation for direct and
search turns, clean account history and replay, Flash/PRO compatibility, original
input/history, protected identifiers and values, rejected invented diagnoses and
unknown-term clarifications. Source tests exercise official-domain restriction,
lookalike-domain rejection and no unverified fallback. Native checks verify that restored text reaches the
free generator instead of returning a prepared spelling question and that
browser-provided native sources do not trigger another search.

The pre-deploy understanding gate requires seven complete real browser SSE
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

Three additional live interpretation cases cover the alternate medical wording,
English spelling and current-information search. The existing gateway gate also
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
it does not clinically validate every recommendation. The retained sample still
includes an inferred histamine sensitivity and a weight-loss rate that the intent
gate has not validated. Some generated Russian wording also remains imperfect.
These are limitations of the answer model, not accepted factual-quality results.

The screenshot follow-up strengthens answer qualification: known incorrect
Russian action verbs, personal safety promises and the observed pounds/kg rate
error are rejected. Finance checks also reject the observed incorrect Russian
wording. Health retrieval uses a separate bounded primary-task query; accompanying
conditions remain in the unchanged inference question. One provider request asks
for six candidates, keeps up to three distinct pages, and deduplicates anchors
and repeated titles from the same host. Health excerpts retain up to 900 characters.
Citation rules require support for the adjacent claim and preserve numerical units
and periods. Unsupported numerical recommendations are omitted.

Plain-text Flash generation uses the model publisher's documented non-thinking
sampling profile (`temperature=0.7`, `top_p=0.8`, `top_k=20`, `min_p=0.0`,
`presence_penalty=1.5`). The existing qualified tool profile is preserved.
No second answer-generation or spelling-model call is added.

The direct-answer preview was deployed on 2026-10-04 with gateway commit
`93a0505665d82328b3427cb909c906b23ab68df3` (deployment
`c5da94b1-c265-4afd-87fe-2bb8576ce041`) and native commit
`4d413d3b6dbc4d5f77f6ec4fa32c83db9cea1c7f` (deployment
`c4315f24-1176-4d6f-ba0f-d16746ddade8`). Both deployments succeeded.
The gateway image passed 201 checks; all ten live understanding cases and the
24-tool, two-round Harness gate passed. Public `/health` and `/` returned HTTP 200.
The full receipt and actual generated replies are retained in
[the deployment evidence](verification/request-direct-understanding-deployments-2026-10-04.json).
