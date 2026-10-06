# VELIA Quantum

VELIA Quantum is the middle model in the VELIA line:

- VELIA Flash — fastest CPU model.
- **VELIA Quantum — balanced intelligence/speed model.**
- VELIA Pro — future maximum-quality model.

Quantum is built from the **original** `Qwen/Qwen3.8-Flash-Next` checkpoint.
Strata is not a model dependency and its pruned Coder weights are not used.
External projects may be studied only for implementation ideas and measurements.

## Reproducibility pins

- Base model: `Qwen/Qwen3.8-Flash-Next`
- Base revision: `de4b8e4d43b917e7706784d8bb445c9af86a3540`
- Expert pruning optimizer: `IST-DASLab/RCO`
- RCO revision: `9a1e09c07d468109cbe60a1b87d5036034a79d10`
- Product model id: `velia-quantum`
- Transformers compatibility pin: `cbde22f4c7b5cd1cef4e63c22c1200890696d522`

The target architecture is 48 MoE layers, 512 routed experts per layer in the
base, 10 routed experts active per token, and exactly 256 routed experts retained
per layer after VELIA-specific pruning.

## Why VELIA has its own expert selection

Expert importance depends on the calibration distribution. Quantum therefore
must not be calibrated only on Russian/English or only on code. The repository
owns `calibration_plan.json` and rejects a dataset that is dominated by one
language, lacks core-language coverage, leaks prompts across holdout/calibration
splits, or under-represents key VELIA workloads.

The calibration schema requires source and license metadata for every sample.
`source_manifest.json` separates calibration sources from holdout-only
benchmarks and explicitly excludes non-commercial sources. `build_public_corpus.py`
creates the balanced public multilingual component; `merge_calibration.py`
refuses holdout leakage, unknown provenance, duplicate prompts and source-share
violations before RCO can consume the corpus.

## Build stages

1. **Calibration** — assemble a multilingual, multi-capability JSONL and run
   `validate_calibration.py`. Public Aya/OASST data is only one component;
   VELIA-owned code/tool/browser/safety/vision coverage is required before pruning.
2. **Preflight** — Qwen3.8 uses the newer `qwen4_exp` multimodal architecture.
   Upstream RCO's historical Transformers pin predates it, so Quantum pins a
   Qwen4Exp-capable Transformers revision and checks the exact 48x512/top-10/PLE
   topology before a large model is loaded.
3. **Expert selection** — run the pinned original Qwen checkpoint through
   `run_rco_prune.py`. VELIA replaces only RCO's data loader so answer-token
   KL is optimized on our multilingual distribution. The RCO optimizer itself
   remains pinned upstream.
4. **Materialize** — `materialize_pruned.py` physically removes the selected
   50% of experts and fails unless every layer ends with 256 experts and top-k
   remains 10.
5. **Ternary compression** — target Bonsai-class ternary storage and CPU
   inference. The reproducible open path is GSQ ternary / mixed precision plus
   distillation or QAT as needed. We do **not** claim to possess PrismML's
   unpublished Bonsai-2 training recipe. A quantized candidate is not a VELIA
   release until the quality gates pass.
6. **Railway CPU acceptance** — benchmark warm decode, 2K TTFT, RSS and quality.
   `acceptance.py` fails closed if an aggregate metric or any core-language
   retention floor misses the contract in `spec.json`.
7. **Product routing** — only after acceptance do backend and Android expose
   `velia-quantum` to users. Flash remains untouched until that point.

## Validated pre-GPU milestone

Railway preview validation has completed the full reproducible pre-GPU build:

- 4,096 calibration rows.
- 512 development rows and 512 holdout rows.
- 20 core languages with 204-205 calibration rows each.
- Exact capability quotas from `calibration_plan.json`.
- 256 balanced synthetic vision-routing samples.
- zero calibration validation errors.
- calibration SHA-256:
  `6f48c38f5cf495c6ce75f7e9e5e86152320e993bba2ab454519405ddc51f3872`.
- validation deployment:
  `074fba62-3eec-4d61-ac83-35c95b3e86a5` — SUCCESS.

Rebuild the same pre-GPU artifact set with:

```bash
pip install -r quantum/requirements-corpus.txt
python quantum/build_training_bundle.py \
  --output-dir /data/velia-quantum-bundle \
  --scan-limit 250000 \
  --eval-per-split 512 \
  --vision-samples 256
```

The bundle manifest records file hashes. A rebuilt dataset must pass validation
before it may be supplied to RCO.

## Calibration record

Each JSONL line has this shape:

```json
{
  "id": "source:unique-id",
  "language": "tr",
  "category": "reasoning_math",
  "split": "calibration",
  "source": "dataset-name",
  "license": "Apache-2.0",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

The final assistant turn is the RCO loss target. Prompt/system/tool tokens are
masked out by `calibration.py`.

## Example commands

Validate data:

```bash
python quantum/validate_calibration.py data/quantum.jsonl --stage pruning_search
```

Install the Quantum GPU environment and run the cheap compatibility check before
the large model is allocated:

```bash
pip install -r quantum/requirements-gpu.txt
python quantum/preflight.py --base-path /data/qwen38-flash-next
```

Download the exact base snapshot on the GPU training host (the downloader checks
`config.json` before fetching the full checkpoint):


```bash
python quantum/download_base.py --local-dir /data/qwen38-flash-next
```

Clone the pinned RCO revision:

```bash
git clone https://github.com/IST-DASLab/RCO.git /data/RCO
git -C /data/RCO checkout 9a1e09c07d468109cbe60a1b87d5036034a79d10
```

Run the first real 50% expert search:

```bash
python quantum/profile_vision_routing.py \
  --base-path /data/qwen38-flash-next \
  --manifest /data/velia-quantum-bundle/vision-routing-set/vision_manifest.jsonl \
  --output /data/velia-quantum-artifacts/vision-routing.pt

python quantum/run_rco_prune.py \
  --rco-root /data/RCO \
  --base-path /data/qwen38-flash-next \
  --dataset /data/velia-quantum-bundle/velia-quantum-calibration.jsonl \
  --vision-profile /data/velia-quantum-artifacts/vision-routing.pt \
  --output-dir /data/velia-quantum-rco \
  --samples 4096 \
  --steps 300 \
  --gumbel-samples 4
```

Materialize the **text backbone** checkpoint. Vision and MTP stay explicit sidecar artifacts and must pass their own acceptance gates:

```bash
python quantum/materialize_pruned.py \
  --rco-root /data/RCO \
  --base-path /data/qwen38-flash-next \
  --prune-mask /data/velia-quantum-rco/quantum-prune-mask.pt \
  --output-dir /data/velia-quantum-pruned
```

## First private CPU preview

The shortest path to a live Quantum is intentionally separate from the final
GSQ experiment:

1. RCO creates the physical 256-of-512 expert checkpoint.
2. `build_cpu_preview.py` converts that checkpoint with the pinned llama.cpp
   Qwen4Exp converter.
3. Heavy matrix weights use `TQ2_0`.
4. The enormous PLE row table uses `Q8_0`, not upstream's default F16 for
   TQ conversion. Qwen4Exp reads PLE through `GET_ROWS`, which the pinned
   CPU backend supports for Q8_0.
5. MTP and vision are excluded from the first text-only preview. They remain
   separate acceptance items for the later complete release.
6. The GGUF is mounted from persistent storage at
   `/model/velia-quantum.gguf`; weights are never baked into the worker image.
7. Backend access remains private through
   `VELIA_QUANTUM_PREVIEW_USER_IDS`. Public exposure additionally requires
   `VELIA_QUANTUM_PUBLIC_ENABLED=true`, which stays false until acceptance.

Build the preview after RCO materialization:

```bash
git clone https://github.com/ggml-org/llama.cpp.git /data/llama.cpp
git -C /data/llama.cpp checkout abeada335e2e78bd3fe63febafab7e900ce75810

python quantum/build_cpu_preview.py \
  --checkpoint /data/velia-quantum-pruned \
  --llama-root /data/llama.cpp \
  --output /data/releases/velia-quantum.gguf \
  --report /data/releases/velia-quantum-preview.json
```

The resulting size is **not known yet**. Current 70-75 GB figures are only an
engineering estimate and must not be treated as an artifact measurement.

The first CPU worker is then benchmarked objectively with
`benchmark_cpu_endpoint.py`: 2K-prompt TTFT and warm output tokens/second are
fed into the same fail-closed release gates as the Railway RSS measurement.

GSQ ternary remains the preferred later v1 optimization, but it is no longer on
the critical path to the first private Quantum preview. Stock GSQ's Qwen3.5 MoE
wrapper must not be used directly for Qwen4Exp because its layerwise activation
pipeline does not model Qwen4Exp's four hyper-connection streams.

## Release contract

The initial CPU target is a single Railway replica with at most 24 GB RAM.
The candidate must meet the machine-readable contract in `spec.json`,
including:

- RSS <= 20 GB.
- warm decode >= 10 tokens/s; >= 15 tokens/s is the preferred target.
- 2K-prompt TTFT <= 6 seconds.
- overall quality retention >= 95% of the pinned base.
- per-core-language retention >= 92%.
- separate coding, reasoning, tool-call and vision retention floors.

No route, UI switch, or production model name is enabled merely because a
checkpoint exists.
