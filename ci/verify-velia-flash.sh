#!/bin/bash
set -euo pipefail
# Disposable PostgreSQL; never connects to the production database.
initdb -D /tmp/flash-postgres -A trust --no-locale > /tmp/flash-postgres-init.log
pg_ctl -D /tmp/flash-postgres -l /tmp/flash-postgres.log \
  -o "-h 127.0.0.1 -p 55432 -k /tmp" -w start
trap 'pg_ctl -D /tmp/flash-postgres -m immediate -w stop' EXIT
export VELIA_FLASH_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/postgres
/opt/tests/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_velia_flash.py \
  tests/test_velia_chat_service.py \
  tests/test_velia_mobile_hardening_service.py \
  tests/test_velia_chat_streaming_runtime_patch.py \
  tests/test_velia_attachment_transport_compatibility.py \
  tests/test_velia_attachment_review_regressions.py \
  tests/test_velia_attachment_final_privacy_routing.py \
  tests/test_velia_attachment_final_review_regressions.py
if [[ "${VELIA_FLASH_RUN_REAL_PROBE:-0}" == "1" ]]; then
  PYTHONPATH=/app /opt/tests/bin/python /app/ci/probe-velia-flash.py
fi
