#!/bin/sh
# Required local release checks. Quantum's live acceptance is operator-invoked.
set -eu
python ci/railway-verified-ci.py verify
python ci/probe-velia-flash.py
python -c 'from services.velia_flash_service import public_capability; print(public_capability())'
python ci/probe-selected-model-inputs.py
python ci/probe-velia-quantum.py --config-only
python -m services.velia_superagent_chat_service
# Optional, read-only test-key verification. Never runs an upgrade or grants
# credits, and reports a sanitized result without blocking production release.
python -m scripts.velia_creem_plan_change_probe --predeploy-discover
