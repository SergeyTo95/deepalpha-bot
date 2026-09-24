#!/bin/sh
set -eu
: "${APK_URL:?APK_URL required}"
printf '[Download VELIA 0.13.1 Flash APK](%s)\n' "$APK_URL" > /tmp/link.md
echo PASTE_BEGIN
curl -fsS --max-time 60 --data-binary @/tmp/link.md https://paste.rs
echo
echo PASTE_END
sleep 600
