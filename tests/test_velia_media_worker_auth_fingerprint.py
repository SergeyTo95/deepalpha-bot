from __future__ import annotations

import hashlib

import services.velia_media_worker_runtime_patch as runtime_patch


def test_auth_token_diagnostics_matches_client_normalization(monkeypatch) -> None:
    token = "a" * 64
    monkeypatch.setenv("VELIA_MEDIA_WORKER_AUTH_TOKEN", f"  {token}\n")

    length, fingerprint = runtime_patch._auth_token_diagnostics()

    assert length == len(token)
    assert fingerprint == hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    assert token not in fingerprint


def test_auth_token_diagnostics_reports_missing(monkeypatch) -> None:
    monkeypatch.delenv("VELIA_MEDIA_WORKER_AUTH_TOKEN", raising=False)

    assert runtime_patch._auth_token_diagnostics() == (0, "missing")
