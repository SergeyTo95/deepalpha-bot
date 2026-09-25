#!/bin/sh
set -eu
host="velia-android-apk-c2205e4-production.up.railway.app"
echo DNS_BEGIN
nslookup "$host" || true
echo DNS_END
python3 - <<'PY'
import socket
host="velia-android-apk-c2205e4-production.up.railway.app"
print("PY_GAI_BEGIN")
for x in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM):
    print(x[0], x[4][0])
print("PY_GAI_END")
PY
sleep 600
