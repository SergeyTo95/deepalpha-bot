import io
import zipfile

import pytest

from services import velia_code_archive_service as service


def test_archive_intent_requires_archive_and_action_terms():
    assert service.is_code_archive_request("Отправь файлы архивом") is True
    assert service.is_code_archive_request("Собери код в zip") is True
    assert service.is_code_archive_request("send the files as a ZIP") is True
    assert service.is_code_archive_request("расскажи что такое zip") is False
    assert service.is_code_archive_request("отправь файлы") is False


def test_zip_preserves_paths_and_is_deterministic():
    files = [
        {"path": "src/main.py", "content": "print('ok')\n"},
        {"path": "tests/test_main.py", "content": "def test_ok():\n    assert True\n"},
    ]
    first = service._zip_bytes(files)
    second = service._zip_bytes(list(reversed(files)))
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first), "r") as archive:
        assert archive.namelist() == ["src/main.py", "tests/test_main.py"]
        assert archive.read("src/main.py") == b"print('ok')\n"
        assert archive.read("tests/test_main.py").startswith(b"def test_ok")


def test_archive_path_guard_rejects_secret_material():
    assert service._protected_archive_path(".env") is True
    assert service._protected_archive_path("config/.env.production") is True
    assert service._protected_archive_path("keys/id_rsa") is True
    assert service._protected_archive_path("certs/client.pem") is True
    assert service._protected_archive_path("config/credentials.json") is True
    assert service._protected_archive_path("services/token_service.py") is False
    assert service._protected_archive_path("app/src/Main.kt") is False


def test_result_paths_are_deduplicated_and_bounded(monkeypatch):
    job = {
        "step_results": [
            {"files": ["src/a.py", "src/b.py"]},
            {"files": ["src/a.py", "tests/test_a.py"]},
        ]
    }
    assert service._result_paths(job) == ["src/a.py", "src/b.py", "tests/test_a.py"]
    monkeypatch.setenv("VELIA_CODE_ARCHIVE_MAX_FILES", "2")
    with pytest.raises(service.VeliaCodeArchiveError, match="code_archive_too_many_files"):
        service._result_paths(job)


def test_read_final_files_skips_later_deletion_and_rejects_protected(monkeypatch):
    project = {"id": "p1"}

    def fake_read(_project, _branch, path):
        if path == "src/deleted.py":
            raise service.write_service.DeveloperWriteError("github_not_found", status=404)
        return {"path": path, "content": f"content:{path}"}

    monkeypatch.setattr(service.write_service, "read_utf8_file", fake_read)
    files = service._read_final_files(
        project,
        "velia/example",
        ["src/kept.py", "src/deleted.py"],
    )
    assert files == [{"path": "src/kept.py", "content": "content:src/kept.py"}]

    with pytest.raises(service.VeliaCodeArchiveError, match="code_archive_protected_path"):
        service._read_final_files(project, "velia/example", [".env"])
