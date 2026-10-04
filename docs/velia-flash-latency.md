# Flash request latency: PR577 preview

The preview keeps the same Ternary-Bonsai-2 27B weights and pinned CPU runtime. The worker now retains exact computed prompt states when the single slot changes between request interpretation, answering and review. It regenerates every answer. Production sources are still searched afresh, and the answer editor and its safety/grounding checks remain active.

The configured prompt-state cache is 4096 MiB, within the existing 24 CPU / 24 GiB service limits. The default remains bounded at 1024 MiB, with an explicit zero to disable it. The final CPU configuration is **8 threads**. A 16-thread trial timed out and was reverted; it is not a retained acceleration.

## Measured complete-answer times

One sequential private operator run per configuration, in seconds. These are fixture measurements, not a production percentile or latency guarantee. The medical source results can differ between runs.

| Fixture | Before: cache disabled | Qualified 4 GiB cache, 8 threads |
| --- | ---: | ---: |
| medical_spacing | 229.09 | 294.01 |
| device_ambiguity | 71.90 | 20.60 |
| arithmetic_typo | 40.09 | 25.53 |
| confirmed_context | 98.76 | 27.81 |
| finance_typo | 109.66 | 46.37 |
| router_typo | 102.05 | 32.34 |
| literal_constraints | 98.13 | 22.60 |
| unspecified_device_model | 157.12 | 103.87 |
| Same medical question after other fixtures, same source snapshot | Not measured | 239.10 |

Most ordinary fixtures improved by 1.5–4.3 times. The medical fixture did **not** improve against the previous baseline; it still takes about four to five minutes. The warm fixture reuses the identical source snapshot only inside the private benchmark. No production source or final-answer cache was introduced.

## Preserved checks

The qualified cache/8-thread profile passed all 14 understanding checks, including 9 complete browser SSE responses, and the real 24-tool Harness in two rounds. Existing interpretation, personal-context grounding, literal constraints, search source scope, citations, editor review, guest quota and paid-fallback checks remain in place. Fixed probe replies are retained in the JSON receipt; production user replies are not logged by these diagnostics. This is not a clinical certification.

The 16-thread trial processed a 644-token prompt at about 3.6 tokens per second and timed out. After restoring 8 threads, the coding and Russian native adapter probes both passed; prompt processing recovered to 29.51 tokens per second. The qualified gateway deployment was retained throughout.

Model revision: `6ed5e12bf84b7a63069882c91dd9e9218647d17b`.
GGUF SHA256: `3907dc1658db1f78a9826bf8d5bcb8dc65db0d466388937af57f2294fae62ec1`.
Runtime commit: `01ae597e3f7d4742909e1e831abb12fe3d24b2cf`.
Context 8192, parallel 1, output limit 512 and the verified sampling profile remain unchanged.

See [the verification receipt](verification/request-latency-2026-10-04.json) for source/deployment identifiers, all fixture replies, timings, rejected-trial diagnostics and verification limits.
