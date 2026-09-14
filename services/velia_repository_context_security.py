"""Reduce accidental credential disclosure in repository context sent to models."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

_KEY = r"(?:[A-Za-z0-9_]*(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd|private[_-]?key))"
_LITERAL = re.compile(r"([\"']?" + _KEY + r"[\"']?\s*[:=]\s*)([\"'])([^\r\n\"']{8,})([\"'])", re.I)
_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|xox[baprs]-[A-Za-z0-9-]{15,})\b")
_PEM = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----", re.S)
_URL_AUTH = re.compile(r"([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@", re.I)


def sensitive_context_path(path: str) -> bool:
    normalized = PurePosixPath(str(path or "").replace("\\", "/").lower())
    name = normalized.name
    if set(normalized.parts) & {".ssh", ".aws", ".gnupg"}:
        return True
    if name.startswith(".env") and name not in {".env.example", ".env.sample", ".env.template"}:
        return True
    if name in {
        ".npmrc", ".pypirc", ".netrc", ".dockercfg", "credentials", "credentials.json",
        "service-account.json", "service_account.json", "secrets.json", "id_rsa", "id_ed25519",
    }:
        return True
    return normalized.suffix in {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}


def redact_source(value: str) -> str:
    text = str(value or "")
    # Keep line numbering stable, including multiline keys used in citations.
    text = _PEM.sub(lambda m: "\n".join("[REDACTED PRIVATE KEY]" for _ in m.group(0).split("\n")), text)
    text = _LITERAL.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]" + m.group(4), text)
    text = _TOKEN.sub("[REDACTED]", text)
    return _URL_AUTH.sub(r"\1[REDACTED]@", text)
