# VELIA request understanding

The browser answered an unclear query using a disease found in related web pages.
The user had not named that disease. The answer then treated that guess as a fact.
Prompt-only fixes later produced a bare quoted fragment, while automatic search
still showed sources about the invented diagnosis. That screenshot exposed a
failure that the earlier sampled answers had not caught.

`velia_request_understanding.py` supplies one shared instruction for browser,
Desktop, native Flash, PRO and voice chat. Clear spelling or transcription errors
are interpreted from the user's words and confirmed context. Before asking the
user to explain a spelling error, VELIA tries to restore the phrase using its
spelling, sound and the task. A plausible reading that changes important facts
becomes a specific confirmation question. An unrecoverable term still requires
one question about the original phrase. The answer waits for confirmation of a
disputed meaning instead of giving advice under that assumption.
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
The structured decision can also propose a short corrected spelling of that
fragment. An offline Russian/English vocabulary (`wordfreq==3.1.1`) offers bounded
nearby spellings and sound-alike words to the model. Quoted literals, paths,
identifiers, numbers, acronyms and camelCase tokens are excluded from these
hints. The packaged vocabulary is local data; this step sends no text to an
external spelling service and adds no extra model call.

A dependency-free lexical guard checks corresponding words, permits
nearby spelling edits and a connecting conjunction, and rejects added facts,
unrelated terms, numbers and identifiers. It contains no medical-word lookup or
special handling of the screenshot phrase. It is a lexical bound rather than a
diagnosis or a guarantee of semantic certainty.

An invalid candidate is discarded, so it cannot become an asserted user fact.
If a clarification has no valid model candidate, a unique sound-alike reading
for each unknown word may supply the confirmation candidate. This fills an
otherwise empty question; it does not turn a clarification into a direct answer
or establish a diagnosis. Ambiguous or unrecoverable word readings stay unfilled.
Malformed or contradictory tool arguments stop the turn instead of falling
through to guessed search. Current external facts use at most one search after this step;
arithmetic and ordinary text editing bypass retrieval.

For clarification, the server emits a complete Russian question and ends the
stream. It does not call search or a second generator, and has no source cards.
When several words need confirmation, the reply pairs each original word with
its proposed reading in one complete question. It does not merge adjacent terms
into an invented compound or split a genuine compound into separate conditions.
The native Flash/PRO persistence path returns the same question. Span positions
and, when valid, the bounded corrected words are cached; full raw questions remain
in their existing conversation store. Previously stored position-only handoffs
remain readable. Candidate handoffs are revalidated at the native boundary.
Positions measured from the end survive native leading-whitespace normalization.
Account history removes the handoff marker, duplicate request IDs reuse the
decision, and user facts are not inferred from earlier assistant guesses.

Clear/direct and search turns have one additional bounded free interpretation
call before normal generation. Quotas, permissions, provider credentials, tool
correlations and paid fallback policy retain their existing bounds. Search turns
still repeat the original question after bounded source snippets.

## Verification

Relevant Python checks passed locally: 199 passed, one isolated PostgreSQL
integration test skipped because no test database was supplied. The browser's
10 Node tests also passed. HTTP checks cover no-search clarification, a complete
SSE question, direct turns with constraints/history, invalid interpretation,
signed-in replay and persistence, rejected out-of-input spans, nearby readings
across languages/topics, and rejected unrelated diagnoses or literal changes. Native checks
ensure that the persisted question equals the streamed answer without a second
Flash call or a paid PRO call.

The real pre-deploy gate loads all 24
installed Harness tool declarations, reads a synthetic local file in two model rounds, checks
PRO's correlated tool/SSE calls and checks persistent guest quotas and search
provenance. Its understanding gate now exercises five complete browser SSE turns
on the live Flash worker with an isolated loopback guest server and temporary
quota database, plus three live structured interpretation decisions. The exact
screenshot wording must propose both `гистамин` and `апноэ`; the previous generic
question no longer meets this criterion. Arithmetic, code literals and a user's
Ubuntu correction are checked against complete generated answers. This operator
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

The earlier interpretation gate was checked with six complete public streamed replies, recorded
in `docs/verification/request-intent-public-2026-10-04.json`. The exact screenshot
text and unknown device both produced complete questions without sources.
The Python literal stayed `app.py`, arithmetic returned 391, the user's Ubuntu
correction produced Ctrl+Alt+T, and contextual rewriting retained the author.
All six met the earlier recorded criteria; the user's follow-up showed that a
generic clarification did not provide enough understanding. The current criteria
therefore require the likely reading, not merely a complete question. In that
earlier CPU-worker sample, clarification took
15–18 seconds; direct answers took 36–63 seconds. The extra interpretation call
adds work to direct turns; this change does not claim a latency improvement.

The final restoration version passed eight live understanding cases, including
five complete browser SSE replies. Both medical spellings now produce the
per-word confirmation, the unknown device remains a clarification, arithmetic
returns 391, the Ubuntu correction returns Ctrl+Alt+T, and the Python literal
remains `app.py`. Finance spelling is understood as a direct explanation and the
request for the current Python release selects search.

The exact screenshot text was then replayed through the deployed public guest
endpoint. It returned HTTP 200, finish reason `stop`, and a complete SSE reply:
`Правильно ли я поняла: «гестамин» — это «гистамин», а «эпное» — «апноэ»?`
There were no sources or invented diagnoses. This public run took 51.18 seconds;
the free CPU worker remains slow. The raw answer and manual quality review are
recorded in `docs/verification/request-restoration-public-2026-10-04.json`.

## Deployment

The change targets the existing PR-577 preview services. Browser gateway commit
`59a6462eb05ecfe811835fa300b067d4dc4a75bc`, deployment
`6b351d0a-3ba9-41a5-b593-83ed811b2b06`, status **SUCCESS**. Its image passed
170 Python tests and its live gate passed all eight understanding cases, the
existing Harness checks, PRO SSE/tool correlation, persistent quotas and source
provenance.

Native commit `a79e85e105f9d76d418bc48e8d1c9a28ced54d12`, deployment
`e0ac9897-4358-4e8b-a63e-89a43e27cf3b`, status **SUCCESS**. Source verification
and live Flash coding/Russian probes passed. Slim receipts are recorded in
`docs/verification/request-restoration-deployments-2026-10-04.json`. Earlier
deployment receipts are retained in `request-intent-deployments-2026-10-04.json`
and `request-understanding-deployments-2026-10-04.json` in the same directory.
