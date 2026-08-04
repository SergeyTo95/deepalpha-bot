import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from services import velia_developer_github_service as github


def _decode_part(value):
    raw = value.encode("ascii")
    return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


def test_app_jwt_is_rs256_signed(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    monkeypatch.setenv("VELIA_GITHUB_APP_ID", "12345")
    monkeypatch.setenv("VELIA_GITHUB_APP_PRIVATE_KEY", private_pem)

    token = github._app_jwt(now=1_800_000_000)
    encoded_header, encoded_payload, encoded_signature = token.split(".")
    header = json.loads(_decode_part(encoded_header).decode("utf-8"))
    payload = json.loads(_decode_part(encoded_payload).decode("utf-8"))

    assert header == {"alg": "RS256", "typ": "JWT"}
    assert payload["iss"] == "12345"
    assert payload["iat"] == 1_799_999_940
    assert payload["exp"] == 1_800_000_540
    key.public_key().verify(
        _decode_part(encoded_signature),
        f"{encoded_header}.{encoded_payload}".encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_path_and_branch_validation_blocks_traversal():
    assert github.validate_path("services/api.py") == "services/api.py"
    assert github.validate_branch("feature/developer-v1") == "feature/developer-v1"

    for value in ("../secret", "services/../../secret", "/", "a//b"):
        try:
            github.validate_path(value)
            assert False, value
        except github.DeveloperGithubError as exc:
            assert exc.code == "invalid_path"

    for value in ("../main", "/main", "main/", "feature//bad"):
        try:
            github.validate_branch(value)
            assert False, value
        except github.DeveloperGithubError as exc:
            assert exc.code == "invalid_branch"


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _HTTP:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _Response(self.payload)


def test_read_file_returns_numbered_utf8_range(monkeypatch):
    raw = "one\nдва\nthree\nfour\n".encode("utf-8")
    fake = _HTTP(
        {
            "type": "file",
            "sha": "abc",
            "content": base64.b64encode(raw).decode("ascii"),
        }
    )
    monkeypatch.setattr(github, "HTTP", fake)
    monkeypatch.setattr(github, "_installation_token", lambda *args, **kwargs: "token")

    result = github.read_file(
        10,
        20,
        "owner/repo",
        "main",
        "src/example.txt",
        start_line=2,
        end_line=3,
    )

    assert result["path"] == "src/example.txt"
    assert result["start_line"] == 2
    assert result["end_line"] == 3
    assert result["content"] == "2: два\n3: three"
    assert fake.calls[0][2]["params"] == {"ref": "main"}

def test_non_default_branch_search_reads_exact_branch_tree_and_blobs(monkeypatch):
    calls = []

    def fake_request(method, path, *, token, params=None, body=None, expected=(200,), text_matches=False):
        calls.append((method, path, params))
        if "/git/trees/feature%2Fnew-auth" in path:
            return {
                "tree": [
                    {"path": "src/auth.py", "type": "blob", "size": 80, "sha": "blob-auth"},
                    {"path": "build/generated.py", "type": "blob", "size": 80, "sha": "blob-generated"},
                ],
                "truncated": False,
            }
        if path.endswith("/git/blobs/blob-auth"):
            content = base64.b64encode(b"def refresh_session():\n    return True\n").decode("ascii")
            return {"encoding": "base64", "content": content}
        raise AssertionError(path)

    monkeypatch.setattr(github, "_request", fake_request)
    monkeypatch.setattr(github, "_installation_token", lambda *args, **kwargs: "token")

    results = github.search_code(
        10,
        20,
        "owner/repo",
        "refresh_session",
        branch="feature/new-auth",
        default_branch="main",
    )

    assert [item["path"] for item in results] == ["src/auth.py"]
    assert "refresh_session" in results[0]["fragments"][0]
    assert not any(path == "/search/code" for _, path, _ in calls)
    assert any("feature%2Fnew-auth" in path for _, path, _ in calls)
