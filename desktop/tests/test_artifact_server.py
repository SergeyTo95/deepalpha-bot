from hashlib import sha256
import importlib.util
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

spec = importlib.util.spec_from_file_location("artifact_server", Path(__file__).parents[1] / "scripts/serve-artifacts.py")
artifact_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact_server)


class ArtifactServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.name = "VELIA-Desktop-0.2.0-win-x64.exe"
        self.content = b"MZ_verified_test_installer"
        (self.root / self.name).write_bytes(self.content)
        (self.root / "private.txt").write_text("must not be served")
        (self.root / "manifest.json").write_text(json.dumps({"files": [{"name": self.name,
            "bytes": len(self.content), "sha256": sha256(self.content).hexdigest()}]}))

    def tearDown(self):
        self.temp.cleanup()

    def test_download_range_health_and_private_file_rejection(self):
        manifest, files = artifact_server.load_artifacts(self.root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), artifact_server.artifact_handler(manifest, files))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/health", timeout=5) as response:
                self.assertEqual(json.load(response)["status"], "ready")
            with urlopen(base + "/" + self.name, timeout=5) as response:
                self.assertEqual(response.read(), self.content)
            request = Request(base + "/" + self.name, headers={"Range": "bytes=3-7"})
            with urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), self.content[3:8])
            for path in ("/private.txt", "/%2e%2e/private.txt"):
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + path, timeout=5)
                self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_changed_installer_is_rejected_before_serving(self):
        changed = bytearray(self.content)
        changed[-1] ^= 1
        (self.root / self.name).write_bytes(changed)
        with self.assertRaises(ValueError):
            artifact_server.load_artifacts(self.root)


if __name__ == "__main__":
    unittest.main()
