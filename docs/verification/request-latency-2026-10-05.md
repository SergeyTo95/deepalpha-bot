# VELIA Flash request latency — 2026-10-05

The PR577 browser preview is qualified at gateway commit `f72610e4ce14564b558ea85a408a682b0a04d38d`, deployment `4afe9374-98e1-4e92-9765-40696406839e`. Worker commit `1f1524be9ae0649bfd3cd3086bfd0e1f9777d6f8`, deployment `8a4497c4-ea0e-44f7-b3fb-4e13777bdd5b`, remains active.

All 350 tests in the actual gateway Docker build passed in 12.40 seconds. The final source was tested on Railway because the local execution environment disconnected; the preceding source had passed 341 local tests. All 14 live understanding/browser scenarios passed, including 9 complete browser SSE exchanges, actual source retrieval, single-charge guest quotas and a newly generated warm medical answer. The shipped Harness declared all 24 tools and completed two real-model rounds with the actual read tool and persona check. Existing authentication, persistent quota, PRO tool/SSE and source gates passed.

The retained worker profile is 8 threads, batch 256, microbatch 128, bounded prompt-state cache 8192 MiB, context 8192 tokens, output cap 512 tokens and one model slot. Allocation remains 24 CPU / 24 GiB; automatic sleep, model revision/checksum and runtime revision remain unchanged. Completed answers are never cached or replayed. The exact frozen source snapshot is used only by the private warm fixture; production retrieval keeps its existing freshness policy.

## Results

Before values refer to the previously qualified 8-thread, batch256/microbatch128, 4096-MiB cache run documented at `dee3fa1563fda3e755b016cdfc9a23ba9720cde1`.

| Case | Before, seconds | After, seconds | Reduction |
| --- | ---: | ---: | ---: |
| medical_spacing | 294.01 | 259.37 | 11.8% |
| device_ambiguity | 20.6 | 15.72 | 23.7% |
| arithmetic_typo | 25.53 | 20.04 | 21.5% |
| confirmed_context | 27.81 | 23.61 | 15.1% |
| finance_typo | 46.37 | 40.19 | 13.3% |
| router_typo | 32.34 | 26.19 | 19% |
| literal_constraints | 22.6 | 20.26 | 10.4% |
| unspecified_device_model | 103.87 | 96.23 | 7.4% |
| medical_spacing_warm | 239.1 | 159.86 | 33.1% |

The original medical question now resolves the spelling as “гистамин, апноэ” and keeps histamine as an unspecified substance, with asthma and apnea as the stated conditions. It gives a useful answer without requesting confirmation of these typos, inventing pregnancy/diabetes or a histamine intolerance, or prescribing a new personal numeric schedule.

Stable planner instructions and source-message checkpoints reuse computation. Unique context-label alignment maps only an already validated spelling restoration back to exact original evidence, including negations. It rejects merged concepts, ambiguity and altered protected literals. Correlated tool-error feedback gives the planner/editor the rejected call and its concrete error, retaining at most two attempts. Review checks preserve reported context and citations and reject unsupported advice and affirmative risk-free assurances. Correct negated assurances and terminal scope explanations no longer trigger needless repair. Technical labels keep their proper names and functions.

The phone fixture accepts Samsung's documented Power/Side aliases and requires volume down; corrupted translations, volume up and invented exact models remain failures. Reference: https://www.samsung.com/uk/support/mobile-devices/how-do-i-take-a-screenshot-on-my-samsung-galaxy-device/

## Limits

These are single sequential fixture measurements on an already running worker with state retained from preceding attempts. They are not a production latency distribution or a clinical certification. The first medical exchange took 4m19s, and the controlled warm exchange took 2m40s; complex requests still take minutes. Cold starts and other traffic can add delay. The warm fixture freezes source evidence only; every answer is generated and reviewed anew. Railway memory metrics exclude some mapped model pages and do not establish a whole-model memory footprint.

Public HTTP rechecking through the web tool was unavailable, and the local executor disconnected. Deployment and the unchanged health gate report SUCCESS; the actual private browser SSE route passed. Real-owner pairing and physical Windows UI acceptance are outside these checks.

Failed candidate runs and a slower batch512/microbatch512 trial were rejected. The 8-thread, batch256/microbatch128 profile was retained. Documentation and worker-only file changes skip an unrelated native Torch rebuild; all native source gates remain.
