# VELIA request understanding

The browser answered an unclear query using a disease found in related web pages.
The user had not named that disease. The answer then treated that guess as a fact.
Prompt-only fixes later produced a bare quoted fragment, while automatic search
still showed sources about the invented diagnosis. That screenshot exposed a
failure that the earlier sampled answers had not caught.

`velia_request_understanding.py` supplies one shared instruction for browser,
Desktop, native Flash, PRO and voice chat. Clear spelling or transcription errors
are interpreted from the user's words and confirmed context. An unresolved term
that changes the answer requires only one short clarification with the exact
unclear phrase. The answer waits for the user's response; it does not list
speculative meanings or continue giving advice under a guessed interpretation.
Earlier assistant guesses and retrieved pages cannot establish facts about the
user. Numbers, units, negations, identifiers and explicit constraints are retained.
Text editing also retains the original speaker, gender and perspective unless the
user requests a change.

The instructions are added once. The submitted text and stored conversation are
not rewritten. Tool-call ids and arguments retain their existing transport rules.
Voice keeps its short answer budget while following the same ambiguity rules.

With Internet available, the browser first asks the same free Flash worker for
a bounded structured decision: direct answer, search, or clarification. This
step receives the original question and recent history, without retrieved pages.
Only an exact fragment of the last user message can become a clarification.
Invalid or contradictory tool arguments stop the turn instead of falling through
to guessed search. Current external facts use at most one search after this step;
arithmetic and ordinary text editing bypass retrieval.

For clarification, the server emits a complete Russian question and ends the
stream. It does not call search or a second generator, and has no source cards.
The native Flash/PRO persistence path returns the same question. Only span
positions are cached; raw questions remain in their existing conversation store.
Positions measured from the end survive native leading-whitespace normalization.
Account history removes the handoff marker, duplicate request IDs reuse the
decision, and user facts are not inferred from earlier assistant guesses.

Clear/direct and search turns have one additional bounded free interpretation
call before normal generation. Quotas, permissions, provider credentials, tool
correlations and paid fallback policy retain their existing bounds. Search turns
still repeat the original question after bounded source snippets.

## Verification

Relevant Python checks passed locally: 177 passed, one isolated PostgreSQL
integration test skipped because no test database was supplied. The browser's
10 Node tests also passed. HTTP checks cover no-search clarification, a complete
SSE question, direct turns with constraints/history, invalid interpretation,
signed-in replay and persistence, and rejected out-of-input spans. Native checks
ensure that the persisted question equals the streamed answer without a second
Flash call or a paid PRO call.

The real pre-deploy gate exercises all 24
installed Harness tools, reads a synthetic local file in two model rounds, checks
PRO's correlated tool/SSE calls and checks persistent guest quotas and search
provenance. It additionally checks six synthetic live interpretation decisions,
including exact screenshot spacing and an unfamiliar device name. This operator
gate does not establish real-owner Desktop pairing.

Earlier sampled browser answers are recorded in
`docs/verification/request-understanding-2026-10-04.json`. Each row records the
source commit actually used for that response. They were reviewed
against the criteria in `desktop/understanding-cases.json`, separately from the
transport tests. Earlier raw runs retain the observed failures and their outputs.

The earlier checks covered unclear medical wording, router and finance spelling errors,
mass conversion, quoted Python identifiers, arithmetic, a user's correction of
an earlier Windows assumption, contextual rewriting, an unspecified key and a
0.5% fee added to a 200 TON price. Passing these probes does not establish perfect
understanding of every possible question.

The updated public route was checked with six complete streamed replies, recorded
in `docs/verification/request-intent-public-2026-10-04.json`. The exact screenshot
text and unknown device both produced complete questions without sources.
The Python literal stayed `app.py`, arithmetic returned 391, the user's Ubuntu
correction produced Ctrl+Alt+T, and contextual rewriting retained the author.
All six met the recorded criteria. In this CPU-worker sample, clarification took
15–18 seconds; direct answers took 36–63 seconds. The extra interpretation call
adds work to direct turns; this change does not claim a latency improvement.

## Deployment

The change targets the existing PR-577 preview services. Browser gateway commit
`9ee3e1f81972fb0c2bdce9a37a354e7e792e5e80`, deployment
`3f96d6ed-1573-4fc1-9162-ad3ec53ed24a`, status **SUCCESS**. Its image passed
150 Python tests and its live gate passed all six interpretation cases, the
existing Harness checks, PRO SSE/tool correlation, persistent quotas and source
provenance.

Native commit `dc312b0e9da7f619d6b9024763344918298eb04f`, deployment
`387eb866-cf38-4ae3-8eea-314f65125316`, status **SUCCESS**. Source verification
and live Flash coding/Russian probes passed. Slim receipts are recorded in
`docs/verification/request-intent-deployments-2026-10-04.json`. Earlier
deployment receipts are retained in
`docs/verification/request-understanding-deployments-2026-10-04.json`.
