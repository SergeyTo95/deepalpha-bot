#!/usr/bin/env python3
import base64
import hashlib
import importlib.util
import os
import subprocess
import time
import zipfile
from pathlib import Path

BUILDER = "/usr/local/bin/railway_android_apk_builder.py"
ASSET_ID = "590709729"
ANDROID_REPO = "SergeyTo95/deepalpha-android"

spec = importlib.util.spec_from_file_location("velia_builder", BUILDER)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

app_id = os.environ["VELIA_GITHUB_APP_ID"].strip()
private_key = os.environ["VELIA_GITHUB_APP_PRIVATE_KEY"]
jwt = builder.github_app_jwt(app_id, private_key)
token = builder.installation_token_for_repo(jwt, ANDROID_REPO)

base_zip = Path("/tmp/base.zip")
old_apk = Path("/tmp/old.apk")
new_apk = Path("/tmp/new.apk")
patch = Path("/tmp/current.bsdiff")
patch_b64 = Path("/tmp/current.bsdiff.b64.txt")

subprocess.run([
    "curl","--fail","--location","--silent","--show-error","--retry","3",
    os.environ["ANDROID_DIFF_OLD_URL"],"-o",str(base_zip)
], check=True)

with zipfile.ZipFile(base_zip) as zf:
    names = [name for name in zf.namelist() if name.endswith(".apk")]
    if not names:
        raise RuntimeError("base artifact contains no APK")
    with zf.open(names[0]) as src, old_apk.open("wb") as dst:
        dst.write(src.read())

subprocess.run([
    "curl","--fail","--location","--silent","--show-error","--retry","3",
    "-H",f"Authorization: Bearer {token}",
    "-H","Accept: application/octet-stream",
    "-H","X-GitHub-Api-Version: 2022-11-28",
    f"https://api.github.com/repos/{ANDROID_REPO}/releases/assets/{ASSET_ID}",
    "-o",str(new_apk),
], check=True)

subprocess.run(["bsdiff",str(old_apk),str(new_apk),str(patch)], check=True)
raw = patch.read_bytes()
patch_b64.write_text(base64.b64encode(raw).decode("ascii"), encoding="ascii")

print(
    "PATCH_META "
    f"bytes={len(raw)} "
    f"patch_sha256={hashlib.sha256(raw).hexdigest()} "
    f"target_sha256={hashlib.sha256(new_apk.read_bytes()).hexdigest()}",
    flush=True,
)

attempts = [
    ("paste", [
        "curl","--fail","--silent","--show-error","--max-time","180",
        "--data-binary",f"@{patch_b64}","https://paste.rs"
    ]),
    ("catbox", [
        "curl","--fail","--silent","--show-error","--max-time","180",
        "-F","reqtype=fileupload","-F",f"fileToUpload=@{patch_b64}",
        "https://catbox.moe/user/api.php"
    ]),
]

for name, command in attempts:
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    print(
        f"PATCH_UPLOAD name={name} code={proc.returncode} "
        f"out={proc.stdout[:500]} err={proc.stderr[:300]}",
        flush=True,
    )
    if proc.returncode == 0 and proc.stdout.strip().startswith("http"):
        print(f"PATCH_B64_URL {proc.stdout.strip()}", flush=True)
        break
else:
    raise RuntimeError("patch upload failed")

time.sleep(600)
