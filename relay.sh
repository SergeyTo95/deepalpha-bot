#!/bin/sh
set -eu
: "${APK_URL:?APK_URL required}"
mkdir -p /work
curl -fL --retry 3 --connect-timeout 20 --max-time 1200 "$APK_URL" -o /work/VELIA.apk
echo "APK_BYTES=$(wc -c < /work/VELIA.apk)"
echo "APK_SHA256=$(sha256sum /work/VELIA.apk | cut -d' ' -f1)"
split -b 20000000 -d -a 2 /work/VELIA.apk /work/part
i=0
for f in /work/part[0-9][0-9]; do
  b="/work/part$(printf '%02d' "$i").b64.txt"
  base64 "$f" | tr -d '\n' > "$b"
  echo "UPLOAD_PART_$i bytes=$(wc -c < "$b")"
  u=$(curl -fsS --max-time 600     -F "reqtype=fileupload"     -F "time=1h"     -F "fileToUpload=@$b;filename=VELIA-part$(printf '%02d' "$i").txt"     https://litterbox.catbox.moe/resources/internals/api.php)
  echo "PART_URL_$i=$u"
  i=$((i+1))
done
echo "RELAY_DONE parts=$i"
sleep 600
