"""Persistent web sign-in challenges and account ownership."""
import hashlib
import hmac
import secrets
from contextlib import closing
from datetime import datetime, timezone

from db.database import get_connection


class SignInError(Exception):
    def __init__(self, code, status):
        self.code = code
        self.status = status
        super().__init__(code)


def initialize():
    with closing(get_connection()) as conn, conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('velia-web-signin-schema', 0))")
        cur.execute("""
            CREATE SEQUENCE IF NOT EXISTS velia_web_user_id_seq
            AS BIGINT START WITH 4503599627370496 MAXVALUE 9007199254740991 NO CYCLE
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS velia_web_email_challenges (
                challenge TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                code_mac TEXT NOT NULL,
                link_user_id BIGINT,
                attempts SMALLINT NOT NULL DEFAULT 0,
                consumed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS velia_web_email_challenges_created
            ON velia_web_email_challenges(created_at)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS velia_web_email_challenges_address
            ON velia_web_email_challenges(email, created_at)
        """)


def _mac(key, challenge, code):
    return hmac.digest(key.encode(), f"velia-email-code:{challenge}:{code}".encode(), "sha256").hex()


def start_email(email, link_user_id, key):
    challenge = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(100_000_000):08d}"
    with closing(get_connection()) as conn, conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('velia-email-admission', 0))")
        cur.execute("DELETE FROM velia_web_email_challenges WHERE created_at < NOW() - INTERVAL '1 hour'")
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 minute'),
                   COUNT(*) FILTER (WHERE email=%s),
                   COUNT(*) FILTER (WHERE email=%s AND created_at > NOW() - INTERVAL '1 minute')
            FROM velia_web_email_challenges
        """, (email, email))
        total, recipient_count, recent = cur.fetchone()
        if total >= 30 or recipient_count >= 4 or recent:
            raise SignInError("signin_rate_limit", 429)
        cur.execute("""
            INSERT INTO velia_web_email_challenges(challenge, email, code_mac, link_user_id)
            VALUES (%s, %s, %s, %s)
        """, (challenge, email, _mac(key, challenge, code), link_user_id))
    return challenge, code


def confirm_email(challenge, code, link_user_id, key):
    with closing(get_connection()) as conn, conn, conn.cursor() as cur:
        cur.execute("""
            SELECT email, code_mac, attempts
            FROM velia_web_email_challenges
            WHERE challenge=%s AND link_user_id IS NOT DISTINCT FROM %s
              AND NOT consumed AND attempts < 5
              AND created_at > NOW() - INTERVAL '10 minutes'
            FOR UPDATE
        """, (challenge, link_user_id))
        row = cur.fetchone()
        if not row:
            return None
        email, expected, attempts = row
        valid = hmac.compare_digest(expected, _mac(key, challenge, code))
        cur.execute("""
            UPDATE velia_web_email_challenges SET attempts=%s, consumed=%s WHERE challenge=%s
        """, (attempts + 1, valid, challenge))
        return email if valid else None


def google_legacy_user_id(subject):
    digest = hashlib.sha256(("google:" + subject).encode()).digest()
    return (1 << 61) + (int.from_bytes(digest[:8], "big") & ((1 << 62) - 1))


def resolve_identity(provider, subject, *, email="", name="", avatar_url=None, link_user_id=None):
    now = datetime.now(timezone.utc).isoformat()
    with closing(get_connection()) as conn, conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"velia-signin:{provider}:{subject}",))
        cur.execute("SELECT user_id FROM web_accounts WHERE provider=%s AND provider_sub=%s FOR UPDATE",
                    (provider, subject))
        account = cur.fetchone()
        if account:
            if link_user_id is not None and link_user_id != account[0]:
                raise SignInError("signin_identity_in_use", 409)
            if avatar_url is not None:
                cur.execute("""
                    UPDATE web_accounts SET email=%s, name=%s, avatar_url=%s, updated_at=%s
                    WHERE provider=%s AND provider_sub=%s
                """, (email, name, avatar_url, now, provider, subject))
            return account[0]
        if link_user_id is not None:
            user_id = link_user_id
        else:
            if provider == "google":
                # Keep the same allocation as the original DeepAlpha Google sign-in.
                user_id = google_legacy_user_id(subject)
            else:
                cur.execute("SELECT nextval('velia_web_user_id_seq')")
                user_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO users(user_id, username, first_name, created_at, updated_at)
                VALUES (%s, '', %s, %s, %s)
            """, (user_id, name or "Аккаунт VELIA", now, now))
        cur.execute("""
            INSERT INTO web_accounts(user_id, provider, provider_sub, email, name, avatar_url, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (user_id, provider, subject, email, name, avatar_url or "", now, now))
        return user_id
