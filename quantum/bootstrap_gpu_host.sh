#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="${QUANTUM_WORKDIR:-/data/velia-quantum}"
VENV="${QUANTUM_VENV:-${WORKDIR}/venv}"
RCO_ROOT="${RCO_ROOT:-${WORKDIR}/RCO}"
BASE_PATH="${QUANTUM_BASE_PATH:-${WORKDIR}/qwen38-flash-next}"
BUNDLE="${QUANTUM_BUNDLE_DIR:-${WORKDIR}/bundle}"
ARTIFACTS="${QUANTUM_ARTIFACTS_DIR:-${WORKDIR}/artifacts}"
STAGE="${QUANTUM_STAGE:-smoke}"

mkdir -p "${WORKDIR}" "${ARTIFACTS}"

echo "VELIA_QUANTUM_GPU_BOOTSTRAP stage=${STAGE} workdir=${WORKDIR}"

# Cheap host check before installing anything or downloading hundreds of GB.
python3 "${REPO_ROOT}/quantum/gpu_hardware_preflight.py" \
  --profile smoke_search \
  --work-dir "${WORKDIR}" \
  --report "${ARTIFACTS}/hardware-preflight-before-install.json"

if [[ ! -x "${VENV}/bin/python" ]]; then
  python3 -m venv "${VENV}"
fi
# shellcheck disable=SC1091
source "${VENV}/bin/activate"

python -m pip install --upgrade pip setuptools wheel

if ! python - <<'PY'
try:
    import torch
    good = torch.cuda.is_available() and torch.__version__.split("+", 1)[0] == "2.11.0"
except Exception:
    good = False
raise SystemExit(0 if good else 1)
PY
then
  python -m pip install \
    --index-url https://download.pytorch.org/whl/cu126 \
    "torch==2.11.0"
fi

python -m pip install -r "${REPO_ROOT}/quantum/requirements-gpu.txt"
python -m pip install -r "${REPO_ROOT}/quantum/requirements-corpus.txt"

python "${REPO_ROOT}/quantum/gpu_hardware_preflight.py" \
  --profile smoke_search \
  --work-dir "${WORKDIR}" \
  --check-torch \
  --report "${ARTIFACTS}/hardware-preflight.json"

if [[ ! -d "${RCO_ROOT}/.git" ]]; then
  git clone https://github.com/IST-DASLab/RCO.git "${RCO_ROOT}"
fi
git -C "${RCO_ROOT}" fetch --all --tags --prune
git -C "${RCO_ROOT}" checkout --detach 9a1e09c07d468109cbe60a1b87d5036034a79d10

if [[ ! -f "${BUNDLE}/bundle-manifest.json" || "${QUANTUM_REBUILD_BUNDLE:-0}" == "1" ]]; then
  rm -rf "${BUNDLE}"
  python "${REPO_ROOT}/quantum/build_training_bundle.py" \
    --output-dir "${BUNDLE}" \
    --scan-limit 250000 \
    --eval-per-split 512 \
    --vision-samples 256
fi

if [[ ! -f "${BASE_PATH}/VELIA_QUANTUM_BASE.json" ]]; then
  download_args=(
    python "${REPO_ROOT}/quantum/download_base.py"
    --local-dir "${BASE_PATH}"
  )
  if [[ -n "${HF_TOKEN:-}" ]]; then
    download_args+=(--token "${HF_TOKEN}")
  fi
  "${download_args[@]}"
fi

python "${REPO_ROOT}/quantum/preflight.py" --base-path "${BASE_PATH}"

VISION_PROFILE="${ARTIFACTS}/vision-routing.pt"
if [[ ! -f "${VISION_PROFILE}" || "${QUANTUM_REBUILD_VISION_PROFILE:-0}" == "1" ]]; then
  python "${REPO_ROOT}/quantum/profile_vision_routing.py" \
    --base-path "${BASE_PATH}" \
    --manifest "${BUNDLE}/vision-routing-set/vision_manifest.jsonl" \
    --output "${VISION_PROFILE}" \
    --summary "${ARTIFACTS}/vision-routing.json"
fi

export QUANTUM_BASE_PATH="${BASE_PATH}"
export QUANTUM_DATASET="${BUNDLE}/velia-quantum-calibration.jsonl"
export QUANTUM_VISION_PROFILE="${VISION_PROFILE}"
export RCO_ROOT
export QUANTUM_OUTPUT_DIR="${ARTIFACTS}"
export QUANTUM_STAGE="${STAGE}"

bash "${REPO_ROOT}/quantum/run_gpu_job.sh"

echo "VELIA_QUANTUM_GPU_BOOTSTRAP_COMPLETE stage=${STAGE} artifacts=${ARTIFACTS}"
