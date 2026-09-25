#!/bin/sh
set -eu
: "${APK_URL:?APK_URL required}"
mkdir -p /work
curl -fL --retry 3 --connect-timeout 20 --max-time 1200 "$APK_URL" -o /work/VELIA-0.13.1-Flash.apk
echo "APK_BYTES=$(wc -c < /work/VELIA-0.13.1-Flash.apk)"
echo "APK_SHA256=$(sha256sum /work/VELIA-0.13.1-Flash.apk | cut -d' ' -f1)"
echo TMPFILES_BEGIN
curl -fsS --max-time 1200 -F "file=@/work/VELIA-0.13.1-Flash.apk" https://tmpfiles.org/api/v1/upload
echo
echo TMPFILES_END
sleep 600
