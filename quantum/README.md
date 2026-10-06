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

## Build stages

1. **Calibration** — assemble a multilingual, multi-capability JSONL and run
   `validate_calibration.py`.
2. **Expert selection** — run the pinned original Qwen checkpoint through
   `run_rco_prune.py`. VELIA replaces only RCO's data loader so answer-token
   KL is optimized on our multilingual distribution. The RCO optimizer itself
   remains pinned upstream.
3. **Materialize** — `materialize_pruned.py` physically removes the selected
   50% of experts and fails unless every layer ends with 256 experts and top-k
   remains 10.
4. **Ternary compression** — target Bonsai-class ternary storage and CPU
   inference. The reproducible open path is GSQ ternary / mixed precision plus
   distillation or QAT as needed. We do **not** claim to possess PrismML's
   unpublished Bonsai-2 training recipe. A quantized candidate is not a VELIA
   release until the quality gates pass.
5. **Railway CPU acceptance** — benchmark warm decode, 2K TTFT, RSS and quality.
   `acceptance.py` fails closed if an aggregate metric or any core-language
   retention floor misses the contract in `spec.json`.
6. **Product routing** — only after acceptance do backend and Android expose
   `velia-quantum` to users. Flash remains untouched until that point.

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

Download the exact base snapshot on the GPU training host:

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
python quantum/run_rco_prune.py \
  --rco-root /data/RCO \
  --base-path /data/qwen38-flash-next \
  --dataset data/quantum.jsonl \
  --output-dir /data/velia-quantum-rco \
  --samples 4096 \
  --steps 300
```

Materialize the checkpoint:

```bash
python quantum/materialize_pruned.py \
  --rco-root /data/RCO \
  --base-path /data/qwen38-flash-next \
  --prune-mask /data/velia-quantum-rco/quantum-prune-mask.pt \
  --output-dir /data/velia-quantum-pruned
```

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
