#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
Q="${REPO_ROOT}/quantum"

: "${QUANTUM_BASE_PATH:?Set QUANTUM_BASE_PATH to the pinned original Qwen snapshot}"
: "${QUANTUM_DATASET:?Set QUANTUM_DATASET to the validated multilingual JSONL}"
: "${QUANTUM_VISION_PROFILE:?Set QUANTUM_VISION_PROFILE to the vision-routing artifact}"
: "${RCO_ROOT:?Set RCO_ROOT to pinned IST-DASLab/RCO checkout}"
: "${QUANTUM_OUTPUT_DIR:?Set QUANTUM_OUTPUT_DIR}"

STAGE="${QUANTUM_STAGE:-smoke}"
mkdir -p "${QUANTUM_OUTPUT_DIR}"

case "${STAGE}" in
  smoke)
    SAMPLES=256
    SEQ=512
    STEPS=20
    GUMBEL=2
    HW_PROFILE=smoke_search
    ;;
  search)
    SAMPLES=4096
    SEQ=2048
    STEPS=300
    GUMBEL=4
    HW_PROFILE=faithful_bf16_search_preferred
    ;;
  *)
    echo "Unsupported QUANTUM_STAGE=${STAGE}" >&2
    exit 2
    ;;
esac

python "${Q}/gpu_hardware_preflight.py" \
  --profile "${HW_PROFILE}" \
  --work-dir "${QUANTUM_OUTPUT_DIR}" \
  --check-torch \
  --report "${QUANTUM_OUTPUT_DIR}/hardware-${STAGE}.json"

python "${Q}/preflight.py" --base-path "${QUANTUM_BASE_PATH}"
python "${Q}/validate_calibration.py" "${QUANTUM_DATASET}" --stage pruning_search
python "${Q}/plan_calibration.py" --output "${QUANTUM_OUTPUT_DIR}/calibration-plan.json"

python "${Q}/run_rco_prune.py" \
  --rco-root "${RCO_ROOT}" \
  --base-path "${QUANTUM_BASE_PATH}" \
  --dataset "${QUANTUM_DATASET}" \
  --vision-profile "${QUANTUM_VISION_PROFILE}" \
  --output-dir "${QUANTUM_OUTPUT_DIR}/rco-${STAGE}" \
  --samples "${SAMPLES}" \
  --seq-length "${SEQ}" \
  --steps "${STEPS}" \
  --batch-size 1 \
  --gumbel-samples "${GUMBEL}"

if [[ "${STAGE}" == "search" ]]; then
  python "${Q}/materialize_pruned.py" \
    --rco-root "${RCO_ROOT}" \
    --base-path "${QUANTUM_BASE_PATH}" \
    --prune-mask "${QUANTUM_OUTPUT_DIR}/rco-search/quantum-prune-mask.pt" \
    --output-dir "${QUANTUM_OUTPUT_DIR}/velia-quantum-pruned"
fi
