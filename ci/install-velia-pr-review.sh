#!/bin/sh
set -eu
machine=$(uname -m)
case "$machine" in
  x86_64) arch=x64; digest='BBYkDsKX0evlqWd7mIZg5Mjf03pzlGWoqslUaLERolYBBmFF/Vus91urPDmVV9d9J7jcSqyYc331pwj+bCAf7A==' ;;
  aarch64) arch=arm64; digest='Qr0e6RYbNlLrHSYJOk9qNFFcu+M4xPdNTGB0cmICmvDDg9yEoum8rDeXoIgI3T/LFXmrjoTr6/LLyift+8DdyA==' ;;
  *) exit 1 ;;
esac
archive=$(mktemp)
trap 'rm -f "$archive"' EXIT
curl -fsSL --max-time 90 "https://registry.npmjs.org/@alibaba-group/ocr-linux-$arch/-/ocr-linux-$arch-1.14.4.tgz" -o "$archive"
actual=$(openssl dgst -sha512 -binary "$archive" | openssl base64 -A)
[ "$actual" = "$digest" ]
tar -xOzf "$archive" package/bin/opencodereview > "$1"
chmod 0755 "$1"
"$1" version
