# VELIA request understanding

The browser answered an unclear query using a disease found in related web pages.
The user had not named that disease. The answer then treated that guess as a fact.

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

Browser search repeats the exact original question after source snippets. The
search query and source count retain their existing bounds. The suffix explains
that a page cannot identify what the user intended and preserves the speaker of
an edited text. Account history still restores the original question using the
existing source marker. No extra model request or search request was added.

## Verification

Transport and native prompt checks passed locally: 153 passed, one isolated
PostgreSQL integration test skipped because no test database was supplied.
After the final policy wording changed, the affected prompt and search suites
passed again: 82 passed and the same isolated database test skipped.
The gateway image ran 128 tests. Its real pre-deploy gate exercised all 24
installed Harness tools, read a synthetic local file in two model rounds, checked
PRO's correlated tool/SSE calls and checked persistent guest quotas and search
provenance. This operator gate does not establish real-owner Desktop pairing.

Real browser answers are recorded in
`docs/verification/request-understanding-2026-10-04.json`. Each row records the
source commit actually used for that response. They were reviewed
against the criteria in `desktop/understanding-cases.json`, separately from the
transport tests. Earlier raw runs retain the observed failures and their outputs.

The checks cover unclear medical wording, router and finance spelling errors,
mass conversion, quoted Python identifiers, arithmetic, a user's correction of
an earlier Windows assumption, contextual rewriting, an unspecified key and a
0.5% fee added to a 200 TON price. Passing these probes does not establish perfect
understanding of every possible question.

## Deployment

The change targets the existing PR-577 preview services.

Browser gateway: commit `4e69273746c896d539a96f634c52478010119eb3`,
deployment `b0a95fe0-9667-4823-bf53-e98ffd96bdef`, status **SUCCESS**.
Native chat: commit `9ca3ce84f98fac5b9949a95162a1e75c05ea4298`,
deployment `f432ec7b-013a-4b88-9c81-1faa5ed1f66b`, status **SUCCESS**. Its
source verification gate passed, followed by actual Flash coding and Russian
answer probes. The native probe input was 594/598 tokens, under the unchanged
768-token input limit. These are preview deployments, not a production merge.
Slim deployment receipts are in
`docs/verification/request-understanding-deployments-2026-10-04.json`.
