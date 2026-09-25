#!/bin/sh
set -eu
mkdir -p /work
src="https://raw.githubusercontent.com/SergeyTo95/deepalpha-bot/apkchatdelivery/tmp/VELIA-0.12.1.bsdiff.b64"
curl -fL --retry 3 --connect-timeout 20 --max-time 300 "$src" -o /work/patch-0121.b64.txt
echo "PATCH1_BYTES=$(wc -c < /work/patch-0121.b64.txt)"
echo "PATCH1_SHA256=$(sha256sum /work/patch-0121.b64.txt | cut -d' ' -f1)"
resp=$(curl -fsS --max-time 300 -F "files=@/work/patch-0121.b64.txt;filename=VELIA-0.12.1.bsdiff.b64.txt;type=text/plain" -F "expiryHours=1" https://tempfile.org/api/upload/local)
echo "PATCH1_RESP=$resp"
sleep 600
