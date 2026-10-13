# Flash preparation optimizations

Observed on the PR577 worker: a cold interpretation call processed 1,852 prompt tokens in 66.35 seconds and generated 92 tool tokens in 12.09 seconds. Answer prefill then processed 719 tokens in 25.67 seconds. The worker sleeps after inactivity, so the prompt cache cannot help the first request after wake-up.

This patch does not change weights, generation limits, inference settings or the full interpretation instruction. It does not enable paid Decision-1 or disable worker sleeping.

## Changes

1. Exact greetings and literal integer addition/multiplication in a new conversation route directly to Flash generation. Explicit translation into a named language and explicit text-shortening/punctuation operations can also route directly if the lexical checker finds no unknown terms. Existing conversation context, open questions, medical advice, ambiguous numbers/dates, retrieval requests and unknown spellings keep the full planner. Original messages are passed unchanged.
2. Tool parameter descriptions duplicate the unchanged interpretation instruction. Remove only those descriptions: the serialized tool is 979 characters instead of 2,036. All required fields, types, enums, limits and additionalProperties checks remain. Invalid compact output retries using the original full tool schema and the existing repair rules.
3. Cache only first-attempt validated plain direct decisions with an explicitly empty context, no correction, no search scope and no query. Exact model input, full retained history, hints, instruction, schema, endpoint and credential form an HMAC key. Entries stay in gateway memory for 120 seconds, capped at 128; no raw input or credentials are stored as keys or logged. Search, clarification, personal-context and spelling-repair decisions are not cached. Diagnostic callbacks bypass the cache.
4. Replace repeated full lexicon scans with an eight-bucket length/consonant index. Candidate sets and ranking are unchanged. A local six-word benchmark produced identical candidates: full scans 0.506940 seconds, warm index lookup 0.000035 seconds. This is a helper benchmark, not overall response latency.

## Verification and limits

287 Python tests passed across interpretation, fast routes, strict schema fallback, protected literals, personal context, search provenance, clinical routing, account/guest chat, feature routes, voice, gateway and worker configuration. New tests cover exact-input/history/provider isolation, cache expiration/bounds/mutation, index equivalence, and preservation of medical/typo interpretation over HTTP.

Search protocol tests now submit a retrieval request instead of a greeting; all existing fail-closed assertions remain. The Docker gateway includes the new module and tests.

A live post-deployment cold/warm latency measurement and real-model interpretation comparison are still required. General first-time explanatory questions continue to use the full planner. These changes do not establish that all requests become fast or that clinical answer quality is clinically validated.
