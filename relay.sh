#!/bin/sh
set -eu
: "${APK_URL:?APK_URL required}"
mkdir -p /work
apk=/work/VELIA-0.13.1-Flash.apk
curl -fL --retry 3 --connect-timeout 20 --max-time 1200 "$APK_URL" -o "$apk"
echo "APK_BYTES=$(wc -c < "$apk")"
echo "APK_SHA256=$(sha256sum "$apk" | cut -d' ' -f1)"
python3 -m http.server 8080 --bind 127.0.0.1 --directory /work >/tmp/http.log 2>&1 &
curl -fL --retry 3 --connect-timeout 20 --max-time 300 \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared
echo CLOUDFLARE_TUNNEL_BEGIN
exec /usr/local/bin/cloudflared tunnel --url http://127.0.0.1:8080 --no-autoupdate
