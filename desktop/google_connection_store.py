"""Encrypted, account-scoped Google OAuth persistence in Velia's existing database.

Never log or return Google access/refresh tokens to the browser. The Fernet key
is domain-separated from the browser session cookie using SHA-256.
"""
import base64
from contextlib import closing
import hashlib
import hmac
import json
import re
import time

from cryptography.fernet import Fernet, InvalidToken

from desktop.guest_store import GuestStore


def _token(value, *, required=True):
    if value is None and not required:
        return None
    if (not isinstance(value, str) or not 10 <= len(value) <= 4096
            or any(ord(character) < 33 or ord(character) > 126 for character in value)):
        raise ValueError("invalid_google_oauth_token")
    return value


class GoogleConnectionStore(GuestStore):
    def __init__(self, dsn, *, session_key, sqlite_path=None):
        super().__init__(dsn, sqlite_path=sqlite_path)
        if not isinstance(session_key, str) or len(session_key) < 32:
            raise ValueError("Google credential encryption requires the session key")
        secret = session_key.encode("utf-8")
        self._account_secret = hashlib.sha256(b"velia-google-account-index-v1:" + secret).digest()
        encryption_key = base64.urlsafe_b64encode(
            hashlib.sha256(b"velia-google-token-encryption-v1:" + secret).digest())
        self._cipher = Fernet(encryption_key)

    def _key(self, user_id):
        return hmac.new(self._account_secret, str(user_id).encode(), hashlib.sha256).hexdigest()

    def _initialize(self, cursor):
        cursor.execute("""CREATE TABLE IF NOT EXISTS velia_google_oauth_connections
            (account_hash TEXT PRIMARY KEY, encrypted TEXT NOT NULL)""")

    def save(self, user_id, *, access_token, refresh_token, expires_at, client_id):
        _token(access_token)
        _token(refresh_token, required=False)
        if not isinstance(expires_at, (int, float)) or not time.time() < expires_at <= time.time() + 90000:
            raise ValueError("invalid_google_expiry")
        if not isinstance(client_id, str) or not 8 <= len(client_id) <= 500:
            raise ValueError("invalid_google_oauth_client_id")
        data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "client_sha256": hashlib.sha256(client_id.encode()).hexdigest(),
        }
        sealed = self._cipher.encrypt(json.dumps(data, separators=(",", ":")).encode()).decode()
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            self._initialize(cur)
            self.execute(cur, """INSERT INTO velia_google_oauth_connections (account_hash, encrypted)
                VALUES (?, ?) ON CONFLICT (account_hash) DO UPDATE SET encrypted=excluded.encrypted""",
                (self._key(user_id), sealed))
            conn.commit()

    def load(self, user_id, *, client_id):
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            self._initialize(cur)
            self.execute(cur, "SELECT encrypted FROM velia_google_oauth_connections WHERE account_hash = ?",
                         (self._key(user_id),))
            row = cur.fetchone()
            conn.commit()
        if not row:
            return None
        try:
            content = json.loads(self._cipher.decrypt(row[0].encode()))
            if content.get("client_sha256") != hashlib.sha256(client_id.encode()).hexdigest():
                return None
            _token(content["access_token"])
            _token(content.get("refresh_token"), required=False)
            if not isinstance(content.get("expires_at"), (int, float)):
                return None
            return content
        except (InvalidToken, KeyError, ValueError, TypeError, UnicodeError):
            return None

    def delete(self, user_id):
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            self._initialize(cur)
            self.execute(cur, "DELETE FROM velia_google_oauth_connections WHERE account_hash = ?",
                         (self._key(user_id),))
            conn.commit()
