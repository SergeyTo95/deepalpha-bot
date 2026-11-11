"""Encrypted CRM document in the existing database, bound to authenticated owner."""
import base64
from contextlib import closing
import hashlib
import hmac
import json
import secrets
from cryptography.fernet import Fernet, InvalidToken
from desktop.guest_store import GuestStore
from desktop.crm import CrmError, initial, validate, apply_edit


class CrmStore(GuestStore):
    def __init__(self, dsn=None, *, session_key, sqlite_path=None):
        super().__init__(dsn,sqlite_path=sqlite_path)
        if not isinstance(session_key,str) or len(session_key)<32:
            raise ValueError('CRM encryption key required')
        self.secret=hashlib.sha256(b'velia-crm-owner-v1:'+session_key.encode()).digest()
        self.cipher=Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'velia-crm-data-v1:'+session_key.encode()).digest()))

    def key(self,user):
        return hmac.new(self.secret,str(user).encode(),hashlib.sha256).hexdigest()

    def initialize(self):
        with closing(self.connect()) as conn,closing(conn.cursor()) as cur:
            cur.execute('CREATE TABLE IF NOT EXISTS velia_crm_workspace (owner_hash TEXT PRIMARY KEY, revision TEXT NOT NULL, encrypted TEXT NOT NULL)')
            conn.commit()

    def load(self,user):
        with closing(self.connect()) as conn,closing(conn.cursor()) as cur:
            self.execute(cur,'SELECT revision,encrypted FROM velia_crm_workspace WHERE owner_hash=?',(self.key(user),))
            row=cur.fetchone()
        if not row:return {'revision':'','document':initial()}
        try:
            doc=json.loads(self.cipher.decrypt(row[1].encode()))
            if doc['owner']!=self.key(user):raise ValueError('owner mismatch')
            return {'revision':row[0],'document':validate(doc['document'])}
        except (InvalidToken,ValueError,KeyError,TypeError):
            raise CrmError('crm_storage_unavailable',503) from None

    def preview(self,user,edit,revision,operation_id,*,time_zone='UTC'):
        current=self.load(user)
        if current['revision']!=revision:raise CrmError('crm_revision_conflict',409)
        doc,change=apply_edit(current['document'],edit,operation_id,time_zone=time_zone)
        return doc,change

    def commit(self,user,edit,revision,operation_id,*,time_zone='UTC'):
        doc,change=self.preview(user,edit,revision,operation_id,time_zone=time_zone)
        sealed=self.cipher.encrypt(json.dumps({'owner':self.key(user),'document':doc},ensure_ascii=False).encode()).decode()
        next_revision=secrets.token_hex(16)
        with closing(self.connect()) as conn,closing(conn.cursor()) as cur:
            if revision:
                self.execute(cur,'UPDATE velia_crm_workspace SET revision=?,encrypted=? WHERE owner_hash=? AND revision=?',
                    (next_revision,sealed,self.key(user),revision))
            else:
                self.execute(cur,'INSERT INTO velia_crm_workspace(owner_hash,revision,encrypted) VALUES (?,?,?) ON CONFLICT(owner_hash) DO NOTHING',
                    (self.key(user),next_revision,sealed))
            if cur.rowcount!=1:raise CrmError('crm_revision_conflict',409)
            conn.commit()
        return {'revision':next_revision,'change':change},doc
