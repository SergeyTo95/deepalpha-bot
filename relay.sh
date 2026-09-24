#!/bin/sh
set -eu
: "${APK_URL:?APK_URL required}"
enc=$(python3 - <<'PY'
import os, urllib.parse
print(urllib.parse.quote(os.environ["APK_URL"], safe=""))
PY
)
echo SHORT_BEGIN
curl -fsS --max-time 60 "https://is.gd/create.php?format=simple&url=$enc"
echo
echo SHORT_END
sleep 600
