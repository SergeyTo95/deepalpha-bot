#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

ANDROID_REPO = "SergeyTo95/deepalpha-android"
DEFAULT_SHA = "3cc927d23a71a52b798e540a4db7a92932291c26"
GRADLE_BIN = "/opt/gradle-9.5.0/bin/gradle"
OUT_DIR = Path("/srv")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def github_app_jwt(app_id: str, private_key: str) -> str:
    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    with tempfile.NamedTemporaryFile("w", delete=False) as key_file:
        key_file.write(private_key)
        key_path = key_file.name
    try:
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_path],
            input=signing_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    finally:
        os.unlink(key_path)
    return f"{header}.{payload}.{b64url(proc.stdout)}"


def request_json(url: str, token: str, method: str = "GET") -> dict:
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def installation_token(app_jwt: str) -> str:
    installation = request_json(
        f"https://api.github.com/repos/{ANDROID_REPO}/installation",
        app_jwt,
    )
    installation_id = installation["id"]
    payload = request_json(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        app_jwt,
        method="POST",
    )
    return payload["token"]


def download_source(token: str, sha: str, destination: Path) -> None:
    archive = destination / "android.tar.gz"
    subprocess.run(
        [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--retry",
            "3",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            f"https://api.github.com/repos/{ANDROID_REPO}/tarball/{sha}",
            "-o",
            str(archive),
        ],
        check=True,
    )
    source_dir = destination / "src"
    source_dir.mkdir()
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        prefix = members[0].name.split("/", 1)[0] + "/"
        for member in members:
            if not member.name.startswith(prefix):
                continue
            member.name = member.name[len(prefix):]
            if not member.name:
                continue
            tar.extract(member, source_dir, filter="data")


def tune_build_memory(source_dir: Path) -> None:
    properties = source_dir / "gradle.properties"
    lines = properties.read_text(encoding="utf-8").splitlines()
    filtered = [line for line in lines if not line.startswith("org.gradle.jvmargs=")]
    filtered.extend(
        [
            "org.gradle.jvmargs=-Xmx5g -XX:MaxMetaspaceSize=1024m -Dfile.encoding=UTF-8",
            "org.gradle.workers.max=1",
            "org.gradle.parallel=false",
        ]
    )
    properties.write_text("\n".join(filtered) + "\n", encoding="utf-8")
    print("ANDROID_BUILD_MEMORY_TUNED heap=5g workers=1", flush=True)


def run_build(source_dir: Path) -> Path:
    tune_build_memory(source_dir)
    env = os.environ.copy()
    env.setdefault("GRADLE_USER_HOME", "/tmp/gradle-home")
    subprocess.run(
        [
            GRADLE_BIN,
            "--no-daemon",
            "--max-workers=1",
            "lintDebug",
            "testDebugUnitTest",
            "assembleDebug",
        ],
        cwd=source_dir,
        env=env,
        check=True,
    )
    apk = source_dir / "app/build/outputs/apk/debug/app-debug.apk"
    if not apk.is_file():
        raise RuntimeError("APK was not produced")
    return apk


def serve_artifact(apk: Path, sha: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUT_DIR / "VELIA-debug-3cc927d.apk"
    shutil.copy2(apk, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    metadata = {
        "android_commit": sha,
        "file": output.name,
        "sha256": digest,
        "size_bytes": output.stat().st_size,
    }
    (OUT_DIR / "build.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"APK_BUILD_SUCCESS commit={sha} file={output.name} sha256={digest} size={output.stat().st_size}", flush=True)
    port = os.environ.get("PORT", "8080")
    os.execvp("python3", ["python3", "-m", "http.server", port, "--directory", str(OUT_DIR)])


def main() -> None:
    app_id = os.environ["VELIA_GITHUB_APP_ID"].strip()
    private_key = os.environ["VELIA_GITHUB_APP_PRIVATE_KEY"]
    sha = os.environ.get("ANDROID_SOURCE_SHA", DEFAULT_SHA).strip()
    if sha != DEFAULT_SHA:
        raise RuntimeError(f"Refusing unexpected Android SHA: {sha}")
    with tempfile.TemporaryDirectory(prefix="velia-android-build-") as temp:
        work = Path(temp)
        jwt = github_app_jwt(app_id, private_key)
        token = installation_token(jwt)
        print(f"ANDROID_SOURCE_AUTH_OK repo={ANDROID_REPO} commit={sha}", flush=True)
        download_source(token, sha, work)
        print("ANDROID_SOURCE_DOWNLOAD_OK", flush=True)
        apk = run_build(work / "src")
        serve_artifact(apk, sha)


if __name__ == "__main__":
    main()
