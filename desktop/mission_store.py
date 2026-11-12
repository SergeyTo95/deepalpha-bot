"""Encrypted owner-scoped checkpoints, leases and single-use approval receipts."""
import base64
from contextlib import closing
from copy import deepcopy
import hashlib
import hmac
import json
import secrets
import time
import uuid
from cryptography.fernet import Fernet
from desktop.guest_store import GuestStore
from desktop.mission_schema import MissionError,require,validate_plan,timezone,MODELS,TOOLS,digest,text

ACTIVE={'running','pausing','cancelling'}
MAX_BYTES=4*1024*1024

class MissionStore(GuestStore):
    def __init__(self,dsn=None,*,session_key,sqlite_path=None,clock=time.time):
        super().__init__(dsn,sqlite_path=sqlite_path);self.clock=clock
        self.secret=hashlib.sha256(b'velia-mission-owner-v1:'+session_key.encode()).digest()
        self.cipher=Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'velia-mission-data-v1:'+session_key.encode()).digest()))
    def owner(self,user):return hmac.new(self.secret,str(user).encode(),hashlib.sha256).hexdigest()
    def initialize(self):
        with closing(self.connect()) as conn,closing(conn.cursor()) as cur:
            cur.execute('CREATE TABLE IF NOT EXISTS velia_mission_workspace (owner_hash TEXT PRIMARY KEY, encrypted TEXT NOT NULL)');conn.commit()
    def transaction(self,user,change=None):
        owner=self.owner(user)
        with closing(self.connect()) as conn,closing(conn.cursor()) as cur:
            try:
                if self.sqlite_path is not None:cur.execute('BEGIN IMMEDIATE')
                initial=self.cipher.encrypt(json.dumps({'owner':owner,'missions':[]}).encode()).decode()
                self.execute(cur,'INSERT INTO velia_mission_workspace(owner_hash,encrypted) VALUES (?,?) ON CONFLICT(owner_hash) DO NOTHING',(owner,initial))
                self.execute(cur,'SELECT encrypted FROM velia_mission_workspace WHERE owner_hash=?'+(' FOR UPDATE' if self.sqlite_path is None else ''),(owner,))
                doc=json.loads(self.cipher.decrypt(cur.fetchone()[0].encode()));require(doc.get('owner')==owner,'mission_storage_unavailable',503)
                before=json.dumps(doc,ensure_ascii=False)
                for m in doc['missions']:
                    if m['status'] in ACTIVE and m.get('lease_until',0)<self.clock():
                        running=next((s for s in m['steps'] if s['status']=='running'),None)
                        uncertain=bool(running and running.get('dispatched') and TOOLS[running['tool']][1]=='external')
                        if running:running.update(status='uncertain' if uncertain else 'pending',error='mission_execution_uncertain' if uncertain else None)
                        m.update(status='uncertain' if uncertain else 'interrupted',lease=None,lease_until=0,updated_at=self.clock(),revision=m['revision']+1)
                result=change(doc) if change else doc
                encoded=json.dumps(doc,ensure_ascii=False,allow_nan=False)
                require(len(encoded.encode())<=MAX_BYTES,'mission_storage_full',413)
                # Reserve checkpoint capacity before any worker can dispatch an effect.
                reserved=sum(max(0,2*1024*1024-len(json.dumps(m,ensure_ascii=False).encode())) for m in doc['missions'] if m['status'] in ACTIVE)
                require(len(encoded.encode())+reserved<=MAX_BYTES-4096,'mission_storage_full',413)
                if encoded!=before:self.execute(cur,'UPDATE velia_mission_workspace SET encrypted=? WHERE owner_hash=?',(self.cipher.encrypt(encoded.encode()).decode(),owner))
                conn.commit();return deepcopy(result)
            except BaseException:conn.rollback();raise
    @staticmethod
    def find(doc,id):
        m=next((m for m in doc['missions'] if m['id']==id),None);require(m is not None,'mission_not_found',404);return m
    @staticmethod
    def touch(m,now):m.update(revision=m['revision']+1,updated_at=now)
    def create(self,user,goal,plan,model,zone,key):
        validate_plan(plan,goal);timezone(zone);require(model in MODELS and isinstance(key,str) and len(key)<=100 and bool(key.strip()))
        fingerprint=digest([goal,plan,model,zone])
        def change(doc):
            old=next((m for m in doc['missions'] if m['request_id']==key),None)
            if old:require(old['request_hash']==fingerprint,'mission_request_conflict',409);return old
            require(len(doc['missions'])<10,'mission_limit',429)
            now=self.clock();m={'id':str(uuid.uuid4()),'goal':goal,'title':plan['title'],'model':model,'time_zone':zone,'request_id':key,'request_hash':fingerprint,'revision':1,'created_at':now,'updated_at':now,'status':'draft','lease':None,'lease_until':0,'conversation_id':None,
              'steps':[{**deepcopy(s),'status':'pending','result':None,'error':None,'approval':None,'approved':None} for s in plan['steps']]}
            doc['missions'].insert(0,m);return m
        return self.transaction(user,change)
    def detail(self,user,id):return self.transaction(user,lambda doc:self.find(doc,id))
    def claim(self,user,id,revision):
        def change(doc):
            m=self.find(doc,id)
            if m['status']=='running':return None
            require(m['revision']==revision,'mission_revision_conflict',409)
            require(m['status'] in {'draft','paused','failed','interrupted'},'mission_cannot_resume',409)
            require(not any(o['id']!=id and o['status'] in ACTIVE for o in doc['missions']),'mission_busy',429)
            require(not any(s['status']=='uncertain' for s in m['steps']),'mission_execution_uncertain',409)
            for s in m['steps']:
                if s['status']=='failed':s.update(status='pending',error=None)
            m.update(status='running',lease=secrets.token_hex(16),lease_until=self.clock()+180);self.touch(m,self.clock());return m
        return self.transaction(user,change)
    def checkpoint(self,user,id,lease,change):
        def update(doc):
            m=self.find(doc,id);require(m['lease']==lease and m['status'] in ACTIVE,'mission_lease_lost',409)
            result=change(m);self.touch(m,self.clock());return result if result is not None else m
        return self.transaction(user,update)
    def begin(self,user,id,lease):
        def change(m):
            if m['status'] in {'pausing','cancelling'}:
                m.update(status='paused' if m['status']=='pausing' else 'cancelled',lease=None,lease_until=0);return {'stop':True}
            step=next((s for s in m['steps'] if s['status']!='completed'),None)
            if step is None:m.update(status='completed',lease=None,lease_until=0);return {'stop':True}
            require(step['status']=='pending','mission_cannot_resume',409)
            step.update(status='running',error=None,dispatched=False);m['lease_until']=self.clock()+180
            return {'mission':m,'step':step}
        return self.checkpoint(user,id,lease,change)
    def wait_approval(self,user,id,lease,step_id,preview):
        def change(m):
            step=next(s for s in m['steps'] if s['id']==step_id)
            if m['status']!='running':
                step['status']='pending';m.update(status='paused' if m['status']=='pausing' else 'cancelled',lease=None,lease_until=0);return
            step.update(status='awaiting_approval',approval={'token':secrets.token_urlsafe(32),'expires_at':self.clock()+600,'hash':digest(preview),'preview':preview},approved=None)
            m.update(status='awaiting_approval',lease=None,lease_until=0)
        return self.checkpoint(user,id,lease,change)
    def ready(self,user,id,lease,step_id):
        def change(m):
            step=next(s for s in m['steps'] if s['id']==step_id)
            if m['status']!='running':
                step['status']='pending';m.update(status='paused' if m['status']=='pausing' else 'cancelled',lease=None,lease_until=0);return False
            step['dispatched']=True;return True
        return self.checkpoint(user,id,lease,change)
    def approve(self,user,id,revision,token,*,refresh=False):
        def change(doc):
            m=self.find(doc,id);require(m['revision']==revision,'mission_revision_conflict',409);require(m['status']=='awaiting_approval','mission_approval_required',409)
            step=next(s for s in m['steps'] if s['status']=='awaiting_approval');a=step['approval']
            if refresh:a.update(token=secrets.token_urlsafe(32),expires_at=self.clock()+600);self.touch(m,self.clock());return m
            require(isinstance(token,str) and hmac.compare_digest(token,a['token']),'mission_approval_invalid',409)
            require(a['expires_at']>self.clock(),'mission_approval_expired',409)
            step.update(status='pending',approved={'hash':a['hash'],'until':self.clock()+600,'at':self.clock()},approval=None)
            m.update(status='paused');self.touch(m,self.clock());return m
        return self.transaction(user,change)
    def control(self,user,id,revision,action):
        def change(doc):
            m=self.find(doc,id);require(m['revision']==revision,'mission_revision_conflict',409)
            if action=='delete':require(m['status'] not in ACTIVE,'mission_busy',409);doc['missions'].remove(m);return {'deleted':True}
            require(m['status'] not in {'completed','cancelled'},'mission_cannot_resume',409)
            require(action in {'pause','cancel'},'mission_invalid_request')
            require(action!='pause' or m['status']!='uncertain','mission_execution_uncertain',409)
            busy=any(s['status']=='running' for s in m['steps'])
            m['status']=('pausing' if action=='pause' else 'cancelling') if busy else ('paused' if action=='pause' else 'cancelled')
            for s in m['steps']:
                if s['status']=='awaiting_approval':s.update(status='pending',approval=None)
                if s['status']!='running':s['approved']=None
            if not busy:m.update(lease=None,lease_until=0)
            self.touch(m,self.clock());return m
        return self.transaction(user,change)
    @staticmethod
    def public(m,*,summary=False):
        value=deepcopy(m)
        for k in ('lease','lease_until','conversation_id','request_id','request_hash'):value.pop(k,None)
        for step in value['steps']:
            step.pop('approved',None)
            step.pop('dispatched',None)
            if summary:step.pop('result',None);step.pop('arguments',None);step.pop('approval',None)
            elif step.get('result'):step['result'].pop('file',None)
        return value
