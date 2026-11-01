"""Serve only the verified Desktop preview artifact and its build manifest."""

from hashlib import sha256
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


def load_artifacts(directory):
    root = Path(directory).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    files = {}
    for item in manifest["files"]:
        name = item["name"]
        if not re.fullmatch(r"VELIA-Desktop-[A-Za-z0-9_.-]+\.(?:zip|exe)", name):
            raise ValueError("Invalid artifact filename")
        path = root / name
        if path.is_symlink() or path.stat().st_size != item["bytes"]:
            raise ValueError("Artifact size differs from the manifest")
        digest = sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise ValueError("Artifact checksum differs from the manifest")
        mime = "application/zip" if path.suffix == ".zip" else "application/vnd.microsoft.portable-executable"
        files["/" + name] = (path, mime)
    if len(files) != 1:
        raise ValueError("Expected one Windows artifact")
    files["/manifest.json"] = (root / "manifest.json", "application/json")
    return manifest, files


def artifact_handler(manifest, files):
    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.serve(False)

        def do_GET(self):
            self.serve(True)

        def serve(self, body):
            path = unquote(urlsplit(self.path).path)
            if path == "/health":
                content = b'{"status":"ready"}\n'
                self.send_bytes(content, "application/json", body)
                return
            if path == "/":
                name = manifest["files"][0]["name"]
                portable = name.endswith(".zip")
                label = "Скачать переносимую версию (ZIP)" if portable else "Скачать установщик"
                instruction = ("Распакуйте архив целиком и запустите VELIA Desktop Preview.exe." if portable
                               else "Запустите установщик.")
                content = ("<!doctype html><html lang='ru'><meta charset='utf-8'>"
                           "<title>VELIA Desktop Preview</title>"
                           "<h1>VELIA Desktop Preview</h1><p>Windows x64 · тестовая сборка</p>"
                           f"<p><a href='/{escape(name)}'>{label}</a></p><p>{instruction}</p>"
                           "<p>Тестовая сборка без цифровой подписи. Запуск интерфейса и подключение "
                           "к живой модели ещё требуют проверки на Windows.</p>"
                           "<p><a href='/manifest.json'>Версия и контрольная сумма</a></p></html>").encode()
                self.send_bytes(content, "text/html; charset=utf-8", body)
                return
            if path not in files:
                self.send_error(404)
                return
            filename, mime = files[path]
            size = filename.stat().st_size
            start, end = 0, size - 1
            ranged = self.headers.get("Range")
            if ranged:
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", ranged)
                if not match:
                    self.send_error(416)
                    return
                start = int(match[1])
                end = min(int(match[2]) if match[2] else end, end)
                if start > end or start >= size:
                    self.send_error(416)
                    return
            self.send_response(206 if ranged else 200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            if ranged:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if filename.suffix in (".zip", ".exe"):
                self.send_header("Content-Disposition", f'attachment; filename="{filename.name}"')
            self.end_headers()
            if body:
                try:
                    with filename.open("rb") as source:
                        source.seek(start)
                        remaining = end - start + 1
                        while remaining:
                            chunk = source.read(min(1024 * 1024, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Downloads can be resumed with a byte-range request.

        def send_bytes(self, content, mime, body):
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if body:
                self.wfile.write(content)

    return Handler


if __name__ == "__main__":
    manifest, files = load_artifacts(os.environ.get("VELIA_ARTIFACT_DIRECTORY", "/artifacts"))
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))),
                                 artifact_handler(manifest, files))
    print("VELIA_DESKTOP_ARTIFACT_READY", json.dumps(manifest), flush=True)
    server.serve_forever()
