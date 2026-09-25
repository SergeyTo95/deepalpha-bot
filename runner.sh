#!/usr/bin/env bash
set -euo pipefail

: "${VELIA_GITHUB_APP_ID:?missing VELIA_GITHUB_APP_ID}"
: "${VELIA_GITHUB_APP_PRIVATE_KEY:?missing VELIA_GITHUB_APP_PRIVATE_KEY}"

repo="SergeyTo95/deepalpha-bot"
api="https://api.github.com"

b64url() {
  openssl base64 -A | tr '+/' '-_' | tr -d '='
}

now=$(date +%s)
header=$(printf '%s' '{"alg":"RS256","typ":"JWT"}' | b64url)
payload=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' "$((now-60))" "$((now+540))" "$VELIA_GITHUB_APP_ID" | b64url)
signing_input="$header.$payload"
key=/tmp/app-key.pem
printf '%s\n' "$VELIA_GITHUB_APP_PRIVATE_KEY" > "$key"
sig=$(printf '%s' "$signing_input" | openssl dgst -sha256 -sign "$key" | b64url)
jwt="$signing_input.$sig"

installation_id=$(curl -fsSL \
  -H "Authorization: Bearer $jwt" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$api/repos/$repo/installation" | jq -r '.id')

installation_token=$(curl -fsSL -X POST \
  -H "Authorization: Bearer $jwt" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$api/app/installations/$installation_id/access_tokens" | jq -r '.token')

reg_json=$(curl -sS -w '\nHTTP_STATUS=%{http_code}' -X POST \
  -H "Authorization: Bearer $installation_token" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$api/repos/$repo/actions/runners/registration-token")
status=$(printf '%s\n' "$reg_json" | sed -n 's/^HTTP_STATUS=//p')
body=$(printf '%s\n' "$reg_json" | sed '/^HTTP_STATUS=/d')
echo "RUNNER_REGISTRATION_HTTP=$status"
if [ "$status" != "201" ]; then
  echo "$body"
  exit 42
fi
reg_token=$(printf '%s' "$body" | jq -r '.token')

release=$(curl -fsSL "$api/repos/actions/runner/releases/latest")
runner_url=$(printf '%s' "$release" | jq -r '.assets[] | select(.name|test("^actions-runner-linux-x64-[0-9.]+\\.tar\\.gz$")) | .browser_download_url' | head -1)
runner_name=$(printf '%s' "$release" | jq -r '.tag_name')
echo "RUNNER_RELEASE=$runner_name"
curl -fL --retry 3 "$runner_url" -o /tmp/runner.tgz
tar -xzf /tmp/runner.tgz -C /runner
rm -f /tmp/runner.tgz

name="railway-velia-apk-${HOSTNAME:-temp}"
./config.sh \
  --url "https://github.com/$repo" \
  --token "$reg_token" \
  --name "$name" \
  --labels "velia-apk" \
  --work "_work" \
  --unattended \
  --ephemeral \
  --replace

echo "SELF_HOSTED_RUNNER_READY name=$name"
exec ./run.sh
