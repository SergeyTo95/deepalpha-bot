#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import re
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


def request_json(url: str, token: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def installation_token_for_repo(app_jwt: str, repo: str) -> str:
    installation = request_json(
        f"https://api.github.com/repos/{repo}/installation",
        app_jwt,
    )
    installation_id = installation["id"]
    payload = request_json(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        app_jwt,
        method="POST",
    )
    return payload["token"]


def installation_token(app_jwt: str) -> str:
    return installation_token_for_repo(app_jwt, ANDROID_REPO)


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



def prefetch_old_apk(work: Path) -> Path | None:
    url = os.environ.get("ANDROID_DIFF_OLD_URL", "").strip()
    if not url:
        return None
    artifact_zip = work / "old-artifact.zip"
    old_apk = work / "old.apk"
    subprocess.run(
        ["curl", "--fail", "--location", "--silent", "--show-error", "--retry", "3", url, "-o", str(artifact_zip)],
        check=True,
    )
    subprocess.run(
        ["unzip", "-p", str(artifact_zip), "app-debug.apk"],
        stdout=old_apk.open("wb"),
        check=True,
    )
    print(f"APK_DELTA_BASE_READY bytes={old_apk.stat().st_size}", flush=True)
    return old_apk


def emit_delta_from_old(apk: Path, old_apk: Path | None) -> None:
    if old_apk is None:
        return
    with tempfile.TemporaryDirectory(prefix="velia-apk-delta-") as temp:
        patch = Path(temp) / "current.bsdiff"
        subprocess.run(["bsdiff", str(old_apk), str(apk), str(patch)], check=True)
        patch_bytes = patch.read_bytes()
        patch_sha = hashlib.sha256(patch_bytes).hexdigest()
        encoded = base64.b64encode(patch_bytes).decode("ascii")
        chunk_size = 6000
        total = (len(encoded) + chunk_size - 1) // chunk_size
        print(
            f"APK_BSDIFF_META bytes={len(patch_bytes)} sha256={patch_sha} "
            f"base64_chars={len(encoded)} chunks={total}",
            flush=True,
        )
        if len(patch_bytes) > 2_000_000:
            print("APK_BSDIFF_TOO_LARGE", flush=True)
            return
        for index in range(total):
            part = encoded[index * chunk_size:(index + 1) * chunk_size]
            print(f"APK_BSDIFF_CHUNK {index + 1}/{total} {part}", flush=True)



def build_delta(token: str, apk: Path) -> None:
    artifact_id = os.environ.get("ANDROID_BASE_ARTIFACT_ID", "").strip()
    if not artifact_id:
        return
    with tempfile.TemporaryDirectory(prefix="velia-delta-") as temp:
        work = Path(temp)
        archive = work / "base.zip"
        subprocess.run(
            [
                "curl", "--fail", "--location", "--silent", "--show-error", "--retry", "3",
                "-H", f"Authorization: Bearer {token}",
                "-H", "Accept: application/vnd.github+json",
                "-H", "X-GitHub-Api-Version: 2022-11-28",
                f"https://api.github.com/repos/{ANDROID_REPO}/actions/artifacts/{artifact_id}/zip",
                "-o", str(archive),
            ],
            check=True,
        )
        import zipfile
        with zipfile.ZipFile(archive) as zf:
            candidates = [n for n in zf.namelist() if n.endswith(".apk")]
            if not candidates:
                raise RuntimeError("Base artifact contains no APK")
            base = work / "base.apk"
            with zf.open(candidates[0]) as src, base.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        patch = work / "VELIA-0.12.1.xdelta"
        subprocess.run(["xdelta3", "-e", "-s", str(base), str(apk), str(patch)], check=True)
        patch_bytes = patch.read_bytes()
        print(
            "APK_DELTA_READY "
            f"base_sha256={hashlib.sha256(base.read_bytes()).hexdigest()} "
            f"target_sha256={hashlib.sha256(apk.read_bytes()).hexdigest()} "
            f"patch_sha256={hashlib.sha256(patch_bytes).hexdigest()} "
            f"patch_size={len(patch_bytes)}",
            flush=True,
        )
        encoded = base64.b64encode(patch_bytes).decode("ascii")
        chunk_size = 6000
        chunks = [encoded[i:i + chunk_size] for i in range(0, len(encoded), chunk_size)]
        print(f"APK_DELTA_CHUNKS count={len(chunks)} chunk_size={chunk_size}", flush=True)
        for index, chunk in enumerate(chunks):
            print(f"APK_DELTA_{index:05d}={chunk}", flush=True)



def publish_release_asset(app_jwt: str, apk: Path, sha: str) -> None:
    repo = os.environ.get("ANDROID_PUBLISH_REPO", "").strip()
    if not repo:
        return
    tag = os.environ.get("ANDROID_RELEASE_TAG", f"velia-android-{sha[:12]}").strip()
    asset_name = os.environ.get("ANDROID_RELEASE_ASSET", "VELIA.apk").strip()
    token = installation_token_for_repo(app_jwt, repo)
    release = request_json(
        f"https://api.github.com/repos/{repo}/releases",
        token,
        method="POST",
        payload={
            "tag_name": tag,
            "target_commitish": "main",
            "name": f"VELIA Android {sha[:12]}",
            "body": f"Exact Android build from {sha}. Temporary delivery artifact.",
            "draft": False,
            "prerelease": True,
        },
    )
    release_id = release["id"]
    upload_url = (
        f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets"
        f"?name={asset_name}"
    )
    subprocess.run(
        [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--retry",
            "3",
            "-X",
            "POST",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            "-H",
            "Content-Type: application/vnd.android.package-archive",
            "--data-binary",
            f"@{apk}",
            upload_url,
        ],
        check=True,
    )
    url = f"https://github.com/{repo}/releases/download/{tag}/{asset_name}"
    print(f"APK_PUBLIC_RELEASE_URL {url}", flush=True)


def serve_artifact(apk: Path, sha: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUT_DIR / f"VELIA-debug-{sha[:7]}.apk"
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



def relay_only() -> None:
    if os.environ.get("APK_RELAY_ONLY", "").strip() != "1":
        return
    src = os.environ["APK_RELAY_SOURCE"].strip()
    expected = os.environ["APK_RELAY_SHA256"].strip().lower()
    target = Path("/tmp/VELIA-0.12.1.apk")
    subprocess.run(
        ["curl", "--fail", "--location", "--silent", "--show-error", "--retry", "3", src, "-o", str(target)],
        check=True,
    )
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError(f"relay sha mismatch: {actual} != {expected}")
    proc = subprocess.run(
        [
            "curl", "--fail", "--silent", "--show-error",
            "-F", "reqtype=fileupload",
            "-F", "time=1h",
            "-F", f"fileToUpload=@{target}",
            "https://litterbox.catbox.moe/resources/internals/api.php",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    url = proc.stdout.strip()
    print(f"APK_RELAY_URL {url}", flush=True)
    print(f"APK_RELAY_SHA256 {actual}", flush=True)
    port = os.environ.get("PORT", "8080")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, OUT_DIR / target.name)
    os.execvp("python3", ["python3", "-m", "http.server", port, "--directory", str(OUT_DIR)])


def main() -> None:
    relay_only()
    app_id = os.environ["VELIA_GITHUB_APP_ID"].strip()
    private_key = os.environ["VELIA_GITHUB_APP_PRIVATE_KEY"]
    sha = os.environ.get("ANDROID_SOURCE_SHA", DEFAULT_SHA).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError(f"Refusing non-exact Android source ref: {sha}")
    with tempfile.TemporaryDirectory(prefix="velia-android-build-") as temp:
        work = Path(temp)
        jwt = github_app_jwt(app_id, private_key)
        token = installation_token(jwt)
        print(f"ANDROID_SOURCE_AUTH_OK repo={ANDROID_REPO} commit={sha}", flush=True)
        download_source(token, sha, work)
        print("ANDROID_SOURCE_DOWNLOAD_OK", flush=True)
        apk = run_build(work / "src")
        build_delta(token, apk)
        publish_release_asset(jwt, apk, sha)
        serve_artifact(apk, sha)


if __name__ == "__main__":
    main()
