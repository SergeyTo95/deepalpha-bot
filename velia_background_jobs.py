"""Durable, owner-scoped chat jobs. No credentials, model routing or browser state."""
from contextlib import contextmanager
import hashlib
import json
import time
import uuid

ACTIVE = ('queued', 'running')


class JobError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class JobStore:
    def __init__(self, connect, *, sqlite=False, prepare=None, publish=None):
        self.connect, self.sqlite = connect, sqlite
        self.prepare, self.publish = prepare, publish

    def execute(self, cur, sql, args=()):
        return cur.execute(sql if self.sqlite else sql.replace('?', '%s'), args)

    @contextmanager
    def transaction(self):
        conn = self.connect()
        cur = conn.cursor()
        try:
            if self.sqlite:
                cur.execute('BEGIN IMMEDIATE')
            else:
                # Metadata only; never held across inference. Serializes admission,
                # revisions, leases and publication across backend replicas.
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('velia:background:metadata',0))")
            yield cur
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def initialize(self):
        with self.transaction() as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS velia_background_chat (
                id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, conversation_id TEXT NOT NULL,
                model TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                request_key TEXT NOT NULL, request_hash TEXT NOT NULL, revision INTEGER NOT NULL,
                attempts INTEGER NOT NULL, lease TEXT, lease_until DOUBLE PRECISION NOT NULL,
                created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL,
                result TEXT NOT NULL, error TEXT NOT NULL, UNIQUE(user_id, request_key))''')
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS velia_background_one_active ON velia_background_chat(user_id) WHERE status IN ('queued','running')")
            cur.execute('CREATE INDEX IF NOT EXISTS velia_background_owner_created ON velia_background_chat(user_id,created_at DESC)')
            cur.execute('CREATE INDEX IF NOT EXISTS velia_background_status_created ON velia_background_chat(status,created_at)')

    @staticmethod
    def row(cur, row):
        if row is None:
            return None
        result = dict(zip([d[0] for d in cur.description], row))
        result['payload'] = json.loads(result['payload'])
        return result

    def get(self, user, job_id):
        with self.transaction() as cur:
            self.execute(cur, 'SELECT * FROM velia_background_chat WHERE id=? AND user_id=?', (job_id, int(user)))
            job = self.row(cur, cur.fetchone())
            if not job:
                raise JobError('background_task_not_found', 404)
            return job

    def listing(self, user, *, cid=None, limit=30):
        with self.transaction() as cur:
            args=(int(user),)+( (cid,) if cid is not None else () )+(min(30,max(1,int(limit))),)
            self.execute(cur, 'SELECT * FROM velia_background_chat WHERE user_id=?'+(' AND conversation_id=?' if cid is not None else '')+' ORDER BY created_at DESC LIMIT ?', args)
            return [self.row(cur, row) for row in cur.fetchall()]

    def create(self, user, cid, model, key, data):
        fingerprint = digest({'conversation_id':cid, 'model':model, **data})
        with self.transaction() as cur:
            self.execute(cur, 'SELECT * FROM velia_background_chat WHERE user_id=? AND request_key=?', (int(user), key))
            old = self.row(cur, cur.fetchone())
            if old:
                if old['request_hash'] != fingerprint:
                    raise JobError('idempotency_mode_mismatch', 409)
                return old
            self.execute(cur, "SELECT id FROM velia_background_chat WHERE user_id=? AND status IN ('queued','running')", (int(user),))
            if cur.fetchone():
                raise JobError('background_task_active', 409)
            now = time.time()
            job = dict(id=str(uuid.uuid4()), user_id=int(user), conversation_id=cid, model=model,
                status='queued', payload=dict(data), request_key=key, request_hash=fingerprint,
                revision=1, attempts=0, lease=None, lease_until=0, created_at=now, updated_at=now, result='', error='')
            if self.prepare:
                job['payload'] = self.prepare(cur, job)
            columns = list(job)
            self.execute(cur, 'INSERT INTO velia_background_chat ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',
                tuple(json.dumps(job[k], ensure_ascii=False) if k == 'payload' else job[k] for k in columns))
            return job

    def claim(self):
        with self.transaction() as cur:
            now = time.time()
            self.execute(cur, "SELECT * FROM velia_background_chat WHERE status='running' AND lease_until<?", (now,))
            expired = [self.row(cur, row) for row in cur.fetchall()]
            for job in expired:
                status = 'queued' if job['attempts'] < 4 else 'error'
                self.execute(cur, 'UPDATE velia_background_chat SET status=?,lease=NULL,error=?,updated_at=? WHERE id=?',
                    (status, '' if status == 'queued' else 'background_task_interrupted', now, job['id']))
                if status == 'error' and self.publish:
                    job.update(status=status, error='background_task_interrupted')
                    self.publish(cur, job)
            self.execute(cur, "SELECT * FROM velia_background_chat WHERE status='queued' ORDER BY created_at LIMIT 1")
            job = self.row(cur, cur.fetchone())
            if job is None:
                return None
            if job['attempts'] >= 4:
                job.update(status='error', error='background_task_interrupted')
                if self.publish:
                    self.publish(cur, job)
                self.execute(cur, "UPDATE velia_background_chat SET status='error',error='background_task_interrupted',updated_at=? WHERE id=?", (now,job['id']))
                return None
            job.update(status='running', lease=uuid.uuid4().hex, lease_until=now+600, attempts=job['attempts']+1, updated_at=now)
            self.execute(cur, "UPDATE velia_background_chat SET status='running',lease=?,lease_until=?,attempts=?,updated_at=? WHERE id=?",
                (job['lease'], job['lease_until'], job['attempts'], now, job['id']))
            return job

    def alive(self, job):
        current = self.get(job['user_id'], job['id'])
        return (current['status'] == 'running' and current['lease'] == job['lease'] and current['revision'] == job['revision'])

    def finish(self, job, text='', error=''):
        with self.transaction() as cur:
            self.execute(cur, 'SELECT * FROM velia_background_chat WHERE id=? AND user_id=?', (job['id'], job['user_id']))
            current = self.row(cur, cur.fetchone())
            if not current or current['status'] != 'running' or current['lease'] != job['lease']:
                return False
            if current['revision'] != job['revision']:
                self.execute(cur, "UPDATE velia_background_chat SET status='queued',lease=NULL,updated_at=? WHERE id=?", (time.time(), job['id']))
                return False
            current.update(status='completed' if text and not error else 'error', result=text if not error else '', error=error)
            if self.publish:
                self.publish(cur, current)
            current['payload'].pop('messages',None)
            self.execute(cur, 'UPDATE velia_background_chat SET status=?,result=?,error=?,payload=?,lease=NULL,updated_at=? WHERE id=?',
                (current['status'], current['result'], current['error'], json.dumps(current['payload'],ensure_ascii=False), time.time(), job['id']))
            return True

    def change(self, user, job_id, *, note=None, key=None):
        with self.transaction() as cur:
            self.execute(cur, 'SELECT * FROM velia_background_chat WHERE id=? AND user_id=?', (job_id, int(user)))
            job = self.row(cur, cur.fetchone())
            if not job:
                raise JobError('background_task_not_found', 404)
            notes = job['payload'].get('notes', [])
            if note is not None:
                for old in notes:
                    if old['key'] == key:
                        if old['text'] != note:
                            raise JobError('idempotency_mode_mismatch', 409)
                        return job
            if job['status'] not in ACTIVE:
                if note is None and job['status'] == 'cancelled':
                    return job
                raise JobError('background_task_finished', 409)
            if note is None:
                job.update(status='cancelled', error='background_task_cancelled')
                job['payload'].pop('messages',None)
                if self.publish:
                    self.publish(cur, job)
            else:
                if len(notes) >= 3:
                    raise JobError('background_revision_limit', 429)
                notes.append({'key':key, 'text':note})
                job['payload']['notes'] = notes
                job['revision'] += 1
            self.execute(cur, 'UPDATE velia_background_chat SET status=?,payload=?,revision=?,error=?,updated_at=? WHERE id=? AND user_id=?',
                (job['status'], json.dumps(job['payload'], ensure_ascii=False), job['revision'], job['error'], time.time(), job_id, int(user)))
            return job


def public_job(job):
    return {k:job[k] for k in ('id','conversation_id','model','status','revision','created_at','updated_at','error')} | {
        'title':job['payload']['content'][:160], 'result':job['result'],
        'assistant_message_id':job['payload'].get('assistant_message_id'),
        'notes':[n['text'] for n in job['payload'].get('notes', [])]}
