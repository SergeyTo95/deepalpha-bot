#!/bin/sh
set -u
: "${APK_URL:?APK_URL required}"
enc=$(python3 - <<'PY'
import os, urllib.parse
print(urllib.parse.quote(os.environ["APK_URL"], safe=""))
PY
)
echo TINYURL_BEGIN
curl -fsSL --max-time 60 "https://tinyurl.com/api-create.php?url=$enc" || true
echo
echo TINYURL_END
echo DAGD_BEGIN
curl -fsSL --max-time 60 "https://da.gd/s?url=$enc" || true
echo
echo DAGD_END
sleep 600
