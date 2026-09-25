#!/bin/sh
set -eu
: "${APK_URL:?APK_URL required}"
mkdir -p /work
curl -fL --retry 3 --connect-timeout 20 --max-time 1200 "$APK_URL" -o /work/VELIA-0.13.1-Flash.apk
echo "APK_BYTES=$(wc -c < /work/VELIA-0.13.1-Flash.apk)"
echo "APK_SHA256=$(sha256sum /work/VELIA-0.13.1-Flash.apk | cut -d' ' -f1)"
split -b 40000000 -d -a 2 /work/VELIA-0.13.1-Flash.apk /work/VELIA.part
i=0
for f in /work/VELIA.part[0-9][0-9]; do
  name="VELIA-part$(printf '%02d' "$i").bin"
  echo "TMPFILES_PART_$i bytes=$(wc -c < "$f") name=$name"
  resp=$(curl -fsS --max-time 1200 -F "file=@$f;filename=$name;type=application/octet-stream" https://tmpfiles.org/api/v1/upload)
  echo "TMPFILES_RESP_$i=$resp"
  i=$((i+1))
done
echo "TMPFILES_DONE parts=$i"
sleep 600
