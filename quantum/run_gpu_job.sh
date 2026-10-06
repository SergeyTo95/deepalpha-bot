#!/usr/bin/env bash
set -euo pipefail

: "${QUANTUM_BASE_PATH:?Set QUANTUM_BASE_PATH to the pinned original Qwen snapshot}"
: "${QUANTUM_DATASET:?Set QUANTUM_DATASET to the validated multilingual JSONL}"
: "${QUANTUM_VISION_PROFILE:?Set QUANTUM_VISION_PROFILE to the vision-routing artifact}"
: "${RCO_ROOT:?Set RCO_ROOT to pinned IST-DASLab/RCO checkout}"
: "${QUANTUM_OUTPUT_DIR:?Set QUANTUM_OUTPUT_DIR}"

STAGE="${QUANTUM_STAGE:-smoke}"
mkdir -p "${QUANTUM_OUTPUT_DIR}"

python quantum/preflight.py --base-path "${QUANTUM_BASE_PATH}"
python quantum/validate_calibration.py "${QUANTUM_DATASET}" --stage pruning_search
python quantum/plan_calibration.py --output "${QUANTUM_OUTPUT_DIR}/calibration-plan.json"

case "${STAGE}" in
  smoke)
    SAMPLES=256
    SEQ=512
    STEPS=20
    GUMBEL=2
    ;;
  search)
    SAMPLES=4096
    SEQ=2048
    STEPS=300
    GUMBEL=4
    ;;
  *)
    echo "Unsupported QUANTUM_STAGE=${STAGE}" >&2
    exit 2
    ;;
esac

python quantum/run_rco_prune.py \
  --rco-root "${RCO_ROOT}" \
  --base-path "${QUANTUM_BASE_PATH}" \
  --dataset "${QUANTUM_DATASET}" \
  --vision-profile "${QUANTUM_VISION_PROFILE}" \
  --output-dir "${QUANTUM_OUTPUT_DIR}/rco-${STAGE}" \
  --samples "${SAMPLES}" \
  --seq-length "${SEQ}" \
  --steps "${STEPS}" \
  --batch-size 1

if [[ "${STAGE}" == "search" ]]; then
  python quantum/materialize_pruned.py \
    --rco-root "${RCO_ROOT}" \
    --base-path "${QUANTUM_BASE_PATH}" \
    --prune-mask "${QUANTUM_OUTPUT_DIR}/rco-search/quantum-prune-mask.pt" \
    --output-dir "${QUANTUM_OUTPUT_DIR}/velia-quantum-pruned"
fi
