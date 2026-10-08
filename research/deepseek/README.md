# VELIA: DeepSeek research audit, 2026-10-08

This is an isolated operator toolset, not a new Harness, model release or user
provider route. Existing Model Lab owns admin authentication, PostgreSQL jobs,
fencing, manual review, approved train/holdout data and exports. These tools
produce research receipts. They are not yet wired into the admin UI or its queue.

## Audited code

| Component | Inspected branch/revision | Finding |
| --- | --- | --- |
| Work / Agent Core | `feature/velia-work-multiagent`, `02b1295ed72865cefc203d4bc22698b984068a50` | Five roles, reviewer corrections, owner revisions, expiring leases, owner isolation; text drafts only. Harness exists. Upwork catalog qualification exists; submissions and wallet signing remain unavailable. |
| Model Lab | `feature/velia-model-lab-20261005`, `81f6148a96371a910e52a8959d28252c903d7403` | Existing owner-only research and quality jobs, 10-case diagnostic, independent references, reviewed train exports. Teacher currently Gemini/Kimi research, not a blind truth oracle. |
| Quantum | `feature/velia-quantum`, `cf378f4f6cad087b06ac7d904e5f9975012bc51c` | Original Qwen3.8-Flash-Next, qwen4_exp, 256-of-512 expert pruning plan, TQ2_0/Q8 PLE preview. Calibration validation recorded; pruned/quantized checkpoint and CPU acceptance still pending. |
| Android | `develop`, `eed201ff24d8214c68092e42c43d5600e9a304ef` | Existing file analyst and model selection; main contains only a placeholder README. No client integration change in this PR. |
| Media Worker | `feature/velia-image-2`, `7842fa305c64c4e578d4ae3ec1f1e8efe9e69373` | Existing service-auth async GPU jobs and bounded one-GPU engines. No OCR operation in inspected contract. Its documented queue/artifact store has POC persistence limits. |
| Flash CPU worker | `desktop/Dockerfile.flash-worker` | Ternary-Bonsai-2 27B PQ2_0 GGUF, checksum `3907dc1658db1f78a9826bf8d5bcb8dc65db0d466388937af57f2294fae62ec1`, PrismML llama.cpp `01ae597e3f7d4742909e1e831abb12fe3d24b2cf`, CPU only. Prompt-state cache already implemented. |

These branches differ; `main` is not the current VELIA code in these repositories.
A branch document's claimed deployment is historical evidence, not a fresh
attestation of a live service. No production merge/deployment is part of this work.

## Technology plan

| Technology | Expected benefit / next experiment | CPU / GPU | Complexity / expense | Main risk |
| --- | --- | --- | --- | --- |
| DeepSpec | Target-specific draft training and acceptance measurement | Official training/evaluation GPU stack; CPU port unqualified | High, GPU training + draft RAM | Released Qwen3/Gemma drafts do not match Flash or qwen4_exp Quantum. |
| Native n-gram speculation | Reuse repeated token patterns without another model | CPU candidate in pinned Flash runtime | Low research compute; no new checkpoint | Verification/batching cost may outweigh accepted proposals on CPU. |
| Small draft / Quantum MTP | Later compare compatible tokenizer + target, or preserved MTP sidecar | CPU serving only after validation; training likely GPU | Extra RAM and compute, unmeasured | Token vocabulary/state/cache mismatch; Quantum MTP is not built yet. |
| Engram | Ablate bounded learned lookup against existing PLE at matched RAM/FLOPs | CPU addressing; training GPU for real-scale study | High model/export changes; toy module ~4.5 MiB estimated weights | Duplicates PLE; learned tables consume RAM and need training. |
| OCR 2 | Layout-aware scan/table extraction on missing native-text pages | Official code CUDA; on-demand GPU worker proposal | Medium/high; cost per page unmeasured | Cyrillic/complex-table accuracy and GPU contention unqualified. |
| External teacher | Independently solve fixed tasks and diagnose errors against references | API, opt-in | Small bounded diagnostic cost; not free | Teacher errors, privacy, exam leakage, model alias/pricing drift. |
| Verified distillation | Multilingual code/tool/reasoning examples with independent tests | CPU data prep, GPU fine-tuning | Medium/high; re-quantization required | Lost languages/knowledge after compression, licensing and holdout leakage. |
| GRPO / RL | Reward only executable tests or grounded outcomes after strong SFT | Primarily GPU training | High rollout expense, postponed | Reward hacking, language drift, false tool-success rewards. |
| MoE/pruning | Continue existing answer-masked Quantum RCO calibration | Existing GPU search -> CPU inference | Existing plan, avoid duplicate | Few active experts do not mean low resident weights/I/O. |

## DeepSpec and acceleration decision

Official DeepSpec has DSpark, DFlash and Eagle3, target-specific Qwen3 4/8/14B
and Gemma checkpoints, CUDA/Triton dependencies, GPU training defaults and a very
large target-cache example. MIT code includes third-party notices. None is a
qualified draft for Flash's ternary checkpoint or Quantum's qwen4_exp architecture.
Do not install that dependency stack in Railway's serving image.

The first trial is the **existing runtime's** `ngram-simple`, not a claimed CPU
port of DeepSpec. `speculation.ngram_trial_args` requires the exact audited
runtime and the binary's advertised flags. It only constructs operator trial
arguments; it does not restart or configure any service. Use an isolated worker
with identical weights, tokenizer, context, batches and sampling. Compare
interleaved baseline/trial runs, at least five repetitions each, cold and warm,
short chat, code/repetition, multilingual and tokenized 2K prompts. Reuse
Quantum's `benchmark_cpu_endpoint.py` for its existing 2K/warm-decode measurements.

Record content TTFT, complete-response time, server-reported decode throughput,
accepted/proposed draft tokens, sampled worker RSS, worker CPU time, request
failures and exact artifact identities. A remote endpoint does not reveal RAM or
CPU: missing metrics remain null. `/proc` sampling requires the **correct worker
PID in the same namespace**; sampled RSS is not a certified peak. CPU time must
come from that worker, not the benchmark client. No metadata label attests weights.

For greedy trials compare generated token IDs, including EOS and long contexts.
For sampling, correct target verification/resampling is essential; fixed seed
text equality alone does not prove preservation of the probability distribution.
Do not alter sampling to manufacture a quality/speed gain. The initial gate
requires no closed-reference regression, >=10% decode gain, <=5% TTFT/CPU
regression and <=256 MiB added sampled RSS, then still requires independent
holdout and manual review. A small diagnostic never authorizes automatic release.
If speculation fails, retain existing bounded cache, tune batches/thread counts
under the same test rather than loading another model. Previous latency records
already show that more threads can make this CPU workload slower.

## Engram versus Quantum PLE

Engram uses deterministic n-gram addressing, learned embeddings, projections,
context-dependent gates and convolution. Addressing alone does not need training;
useful embeddings/fusion do. The official implementation is a demonstration
with mocked backbone components, Apache-2.0. Our original minimal module is a
CPU-only architectural ablation scaffold, not the official full model.

Quantum's spec already includes roughly 51B n-gram parameters / 51.2 GB FP8 PLE
payload, disk row lookup and a bounded cache. Adding another lookup table by
default would repeat the mechanism and worsen memory pressure. Compare:
(1) base/pruned Qwen with its own PLE; (2) existing quantized/mmap PLE;
(3) optional small gated lookup **as an alternative ablation**, with identical
quality data and RAM budget. Check integration with four hyper-connection streams,
pruning calibration and PLE-preserving export before loading the real backbone.

`engram.py` measures addressing and estimates table/projection bytes only.
`engram_candidate.py` is optional PyTorch, zero-initialized residual output:
insertion starts as identity, then requires supervised training. It omits the
paper's full normalization/convolution/backbone design. No main weights change.
This environment has no PyTorch; the forward/gradient test is explicitly skipped.
No trained candidate, Qwen integration or quality improvement is claimed.

For a real experiment: freeze the pinned base, train only the experimental
module initially on reviewed multilingual train data; use disjoint development
and frozen holdout sets. Compare base, pruning, quantization, distillation and
lookup independently and in combinations. Then quantize tables/projections
separately, export only with runtime support, measure cold I/O and p95 latency.
Lookup memory is model capacity, not a user's long-term memory database.

## Documents and OCR

Current attachments already parse TXT/DOCX and native PDF text and use existing
vision paths for images. Native extraction cannot recognize image-only scans or
reliably reconstruct every table. The separate PDF fix preserves page numbers
and marks missing text, rather than silently dropping mixed scanned pages.
An empty page is not necessarily a scan. All-unreadable PDFs retain their honest
failure; no fake OCR result is produced.

Official OCR 2 uses CUDA, Torch and Flash Attention; examples include images,
PDF concurrency and OmniDocBench evaluation. Apache-2.0 repository license;
pin and review the model snapshot/remote code before any worker build. Our
`ocr.py` offers native-first admission diagnostics and CER/WER/table shape/cell
metrics, **not an OCR engine**. Even an enabled/consented budget remains blocked
until worker acceptance exists. No user document is transmitted by this module.

Planned worker: reuse service auth and owner-scoped attachment authorization;
request page ranges with immutable input digest and idempotency key, persist
jobs/artifacts durably, bounded page/pixel count, one GPU slot, expiry and cleanup,
cancel fencing, no public source URL fetching or secret exposure. OCR never
loads alongside a media engine without explicit memory qualification. An
on-demand host includes cold start/model download cost and may conflict with
media latency. Do not enable paid startup without a spend ceiling and measured
per-page cost. No host is provisioned by this PR.

Build a rights-cleared document corpus: Russian/English/Turkish and other core
languages, clean scans, phone photos/rotation, mixed PDFs, 1/10/20 pages, numeric
and merged-cell tables. Human double-check reference text/cells/order; compare
native extractor, existing vision path and OCR 2. Score CER/WER, reading order,
headings, table shape/cells, page completeness, p50/p95 latency, peak VRAM,
startup time and billed compute per page. No language support claim from a
successful English demo. Control user consent for any external-provider path.

## Model Lab and DeepSeek API

Official pricing checked 2026-10-08:

| API id | Served version | Input cache miss, USD / 1M | Output, USD / 1M |
| --- | --- | --- | --- |
| `deepseek-flash` | DeepSeek-V4.1-Flash | 0.15 off-peak / 0.30 peak | 0.60 off-peak / 1.20 peak |
| `deepseek-v4-pro` | DeepSeek-V4-Pro-0813 | 0.66 off-peak / 1.32 peak | 1.98 off-peak / 3.96 peak |

Cache-hit input: Flash 0.003/0.006, Pro 0.022/0.044 off-peak/peak. Official
context 1M, maximum output 384K; our diagnostic deliberately caps output to
128 by default, <=2048. Flash has vision; this adapter is text only. The legacy
Flash names now alias V4.1-Flash. Prices/access can change; refresh before runs.
Peak windows are weekday 01:00–04:00 and 06:00–10:00 UTC except Chinese holidays.

`benchmark.py` accepts only the official endpoint for recognized external models,
requires explicit paid opt-in, a positive budget and an env-based key. It sends
only the fixed synthetic suite, never owner chat or reference answers, disables
thinking explicitly, follows no redirects, bounds output, and performs no
automatic retries. Reserve before each call at peak/cache-miss prices; ambiguous
failures consume reservation. Usage missing means cost unknown, not zero. This
is a conservative research budget, not a provider invoice or hard billing cap.
Default-key configuration and all user routes remain unchanged.

The current diagnostic has 36 exact/JSON questions covering 20 languages, typo,
logic, grounding, code semantics, context, tabular arithmetic and tool-plan
format. Equal arithmetic questions across languages test basic instruction
following only; they are not comprehensive multilingual retention evidence.
For serious intelligence acceptance expand with held-out coding tests in the
**existing sandbox**, grounded reasoning tasks, tool trajectory audits, open
multilingual tasks reviewed blind against a rubric, and memory retrieval tests
against owner-isolated records. Never execute model code in this benchmark.

Self-hosted example (operator supplies a real isolated worker endpoint/key):

```bash
python -m research.deepseek.benchmark \
  --endpoint http://127.0.0.1:8080/v1/chat/completions \
  --model velia-flash --revision EXACT_WEIGHT_SHA --runtime-revision EXACT_RUNTIME_SHA \
  --report /tmp/flash-baseline.json
```

External example is an **opt-in command**, not executed by this work:

```bash
python -m research.deepseek.benchmark \
  --endpoint https://api.deepseek.com/chat/completions \
  --model deepseek-flash --revision DeepSeek-V4.1-Flash --runtime-revision provider-managed \
  --key-env DEEPSEEK_API_KEY --allow-paid-external --budget-usd 0.02 \
  --report /tmp/deepseek-teacher.json
```

Use `evaluation.comparison` only with the same suite, generation and harness
snapshot. Different API models have different sampling behavior; the receipt
records this limitation. Comparison reports errors/regressions against references,
not agreement with a teacher. Logic/fullness on open tasks require human review.
`datasets.add_reviewed_candidate` forwards approved, rights-cleared, nonpersonal,
independently verified **train** examples into existing `lab.add_example`.
Known exam prompts are blocked; semantic leakage still needs human review.
Existing Model Lab owner authority/export remain responsible for persistence.

## Intelligence and multiagent next steps

Prioritize supervised distillation and verified execution traces for Quantum:
intent recovery with typos, multilingual grounding, constrained tool arguments,
code debugging with passing tests, bounded answer length and justified uncertainty.
Keep provenance/license, references and train/dev/holdout hashes. External text
can be a candidate, not proof. DeepSeek-R1 describes reasoning distillation and
allows derivatives under its license, but Qwen/Llama-derived variants retain
upstream obligations. Check current API output/data terms before collection.
SFT first; RL only after auditable rewards and an affordable rollout plan.
Bonsai PQ2 GGUF is a serving artifact, not an assumed trainable checkpoint.
Quantum's canonical checkpoint can be trained, then pruned/compressed and tested.
Neither adding a prompt nor a draft model improves target weights by itself.

Retain Work's five roles and Agent Core tools. Next implementation should add
explicit capability matching (text, file, code sandbox), per-step artifact hashes,
verified execution/test receipts in reviewer input, cancellation and fencing
across sandbox jobs, and typed tool permissions. Resume from durable checkpoints,
not a fabricated success after a timeout. Use existing owner mandate and durable
ledger/signing boundary for future expenses; no LLM secret key access. Marketplace
reads/writes must be bound to the real accepted catalog and platform permissions;
unknown methods remain disabled. This PR does not submit proposals, register
accounts, create wallets or charge Connects.

## Validation and evidence

`python -m pytest -q tests/test_velia_deepseek_research.py` exercises references,
truncation, strict JSON types, comparable snapshots, speculative admission,
external opt-in, SSE fragmentation, unknown resources, privacy of reasoning,
lookup bounds, OCR diagnostics and existing-dataset import gates.

Local receipt is **only** a Python n-gram-addressing microbenchmark. There is no
llama-server or model checkpoint in this workspace; no Flash/Quantum A/B inference
was run. PyTorch candidate test skipped. No paid teacher call, GPU allocation,
training, new checkpoint, model quality claim or Railway runtime change occurred.
Full serving benchmarks require the isolated worker and its measurable resources;
Quantum additionally requires its pending checkpoint. These are explicit pending
acceptance stages rather than invented results.

## Primary sources

- https://github.com/deepseek-ai/DeepSpec (MIT; NOTICE for third-party code)
- https://github.com/deepseek-ai/Engram (Apache-2.0; demo, not full model)
- https://github.com/deepseek-ai/DeepSeek-OCR-2 (Apache-2.0)
- https://api-docs.deepseek.com/quick_start/pricing/
- https://api-docs.deepseek.com/guides/thinking_mode/
- https://github.com/deepseek-ai/DeepSeek-R1 (MIT + upstream model obligations)
- https://github.com/PrismML-Eng/llama.cpp/blob/01ae597e3f7d4742909e1e831abb12fe3d24b2cf/common/arg.cpp
