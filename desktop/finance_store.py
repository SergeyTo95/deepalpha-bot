"""Encrypted financial observations, isolated by authenticated account."""
import base64
from contextlib import closing
import hashlib
import hmac
import json
import secrets
from cryptography.fernet import Fernet, InvalidToken
from desktop.guest_store import GuestStore
from desktop.finance import FinanceError, validate_snapshot


class FinanceStore(GuestStore):
    def __init__(self, dsn=None, *, session_key, sqlite_path=None):
        super().__init__(dsn, sqlite_path=sqlite_path)
        if not isinstance(session_key, str) or len(session_key) < 32:
            raise ValueError('Finance encryption key required')
        self.secret = hashlib.sha256(b'velia-finance-owner-v1:' + session_key.encode()).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'velia-finance-data-v1:' + session_key.encode()).digest()))

    def key(self, user):
        return hmac.new(self.secret, str(user).encode(), hashlib.sha256).hexdigest()

    def initialize(self):
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute('CREATE TABLE IF NOT EXISTS velia_finance_snapshot (owner_hash TEXT PRIMARY KEY, revision TEXT NOT NULL, encrypted TEXT NOT NULL)')
            conn.commit()

    def load(self, user):
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            self.execute(cur, 'SELECT revision,encrypted FROM velia_finance_snapshot WHERE owner_hash=?', (self.key(user),))
            row = cur.fetchone()
        if not row:
            return {'revision': '', 'snapshot': None}
        try:
            doc = json.loads(self.cipher.decrypt(row[1].encode()))
            if doc['owner'] != self.key(user):
                raise ValueError('owner mismatch')
            return {'revision': row[0], 'snapshot': validate_snapshot(doc['snapshot'])}
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise FinanceError('finance_storage_unavailable', 503) from None

    def save(self, user, snapshot, revision):
        snapshot = validate_snapshot(snapshot)
        sealed = self.cipher.encrypt(json.dumps({'owner': self.key(user), 'snapshot': snapshot}, ensure_ascii=False).encode()).decode()
        new_revision = secrets.token_hex(16)
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            if revision:
                self.execute(cur, 'UPDATE velia_finance_snapshot SET revision=?,encrypted=? WHERE owner_hash=? AND revision=?',
                             (new_revision, sealed, self.key(user), revision))
            else:
                self.execute(cur, 'INSERT INTO velia_finance_snapshot(owner_hash,revision,encrypted) VALUES (?,?,?) ON CONFLICT(owner_hash) DO NOTHING',
                             (self.key(user), new_revision, sealed))
            if cur.rowcount != 1:
                raise FinanceError('finance_revision_conflict', 409)
            conn.commit()
        return {'revision': new_revision}

    def delete(self, user, revision):
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            self.execute(cur, 'DELETE FROM velia_finance_snapshot WHERE owner_hash=? AND revision=?', (self.key(user), revision))
            if cur.rowcount != 1:
                raise FinanceError('finance_revision_conflict', 409)
            conn.commit()
