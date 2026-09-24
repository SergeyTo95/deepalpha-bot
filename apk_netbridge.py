import base64
import hashlib
import time
import urllib.request

URL = "https://api.pastes.dev/x6hS84sBEM"
EXPECTED_PATCH_SHA = "cba6a4e3ac93dae4e97939c08308fe4beb7ea3cf6115e04b0ca152bc703e5bf2"
EXPECTED_B64_CHARS = 1825328
CHUNK = 38000

req = urllib.request.Request(URL, headers={"User-Agent": "VELIA-APK-Handoff"})
with urllib.request.urlopen(req, timeout=120) as resp:
    encoded = resp.read().decode("ascii").strip()

if len(encoded) != EXPECTED_B64_CHARS:
    raise RuntimeError(f"unexpected encoded length: {len(encoded)}")
raw = base64.b64decode(encoded)
digest = hashlib.sha256(raw).hexdigest()
if digest != EXPECTED_PATCH_SHA:
    raise RuntimeError(f"patch sha mismatch: {digest}")

chunks = [encoded[i:i+CHUNK] for i in range(0, len(encoded), CHUNK)]
print(f"PATCH_META chars={len(encoded)} raw_bytes={len(raw)} chunks={len(chunks)} sha256={digest}", flush=True)
for i, chunk in enumerate(chunks):
    print(f"PATCH_CHUNK_{i:03d}={chunk}", flush=True)
print("PATCH_DONE", flush=True)
time.sleep(600)
