# VELIA Context Efficiency v1

VELIA Context Efficiency is a native Python adaptation of efficiency patterns from
[NVlabs/SoL-Pi](https://github.com/NVlabs/SoL-Pi) (MIT). VELIA does not install Pi or
vendor SoL-Pi; it applies compatible ideas at VELIA's own prompt/tool boundaries.

## v1 scope

The first rollout is deliberately lossless/exact-only:

- Research evidence removes empty metadata, canonicalizes whitespace, deduplicates
  repeated author names, and replaces only byte-identical normalized excerpts with
  an `excerpt_same_as` reference to the first supplied source.
- Agent planner tool catalogs remove only empty fields and redundant whitespace.
- Coding planning evidence replaces only exact repeated blocks. Exact source used for
  patch generation is not compacted, so exact-replacement semantics stay unchanged.
- An ObservationPack-compatible placeholder helper exists for later tool/log rollout,
  but v1 does not replace a live observation unless the caller can guarantee exact
  recall of the archived source.

No extra model call is introduced by v1. No provider, safety, approval, merge,
deployment, repository-write, or Research Center policy is relaxed.

## Rollout

`VELIA_CONTEXT_EFFICIENCY_ENABLED=false` by default. Set it to `true` after
acceptance. `VELIA_CONTEXT_EFFICIENCY_USER_IDS` can optionally restrict the rollout
to a comma-separated user allowlist.

Every active compaction emits `VELIA_CONTEXT_EFFICIENCY` telemetry with character
counts, estimated tokens removed, and reduction ratio. Token estimates use the same
simple four-characters-per-token accounting as SoL-Pi and are telemetry only; actual
provider usage remains authoritative.

## Deferred stages

Evidence-Preserving Reducer for CI/research logs, exact observation archival/recall,
and semantic Online Context Compact remain separate gated stages. They must preserve
source evidence and pass quality/cost acceptance before production enablement.
