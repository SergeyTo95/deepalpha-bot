#!/bin/sh
set -eu
for u in   "https://tempfile.org/B69GhYHWJVP/download"   "https://tempfile.org/LpumYEUE5Sc/download"; do
  echo "PROBE_URL=$u"
  curl -sS -D - -o /dev/null --max-time 60 "$u" || true
  echo "EFFECTIVE_BEGIN"
  curl -sS -L -o /dev/null -w '%{url_effective}\n' --max-time 120 "$u" || true
  echo "EFFECTIVE_END"
done
sleep 600
