#!/bin/sh
# Required local release checks. Quantum's live acceptance is operator-invoked.
set -eu
python ci/railway-verified-ci.py verify
python ci/probe-velia-flash.py
python -c 'from services.velia_flash_service import public_capability; print(public_capability())'
python ci/probe-selected-model-inputs.py
python ci/probe-velia-quantum.py --config-only
