"""Persistent, user-owned reminders and an inbox/outbox in the existing backend."""
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
import threading
import time
import uuid
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet
from db.database import get_connection

_READY=False
_LOCK=threading.Lock()
ID=re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[45][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}\Z')
TIME=re.compile(r'(?:[01]\d|2[0-3]):[0-5]\d\Z')
DEFAULTS={'timezone':'UTC','quiet':True,'quiet_start':'22:00','quiet_end':'08:00','telegram':False,'preview':False,'digest':False,'digest_time':'09:00'}

class PulseError(ValueError):
    def __init__(self,code,status=400):self.code,self.status=code,status;super().__init__(code)

def now():return int(time.time())
def enabled():return os.getenv('VELIA_PULSE_ENABLED','true').lower() in ('1','true','yes','on')
def zone(value):
    if not isinstance(value,str) or len(value)>100:raise PulseError('pulse_timezone_invalid')
    try:return ZoneInfo(value)
    except Exception:raise PulseError('pulse_timezone_invalid') from None

def local_epoch(day,clock,tz):
    if not isinstance(day,str) or not re.fullmatch(r'20\d\d-\d{2}-\d{2}',day) or not isinstance(clock,str) or not TIME.fullmatch(clock):raise PulseError('pulse_time_invalid')
    try:naive=datetime.fromisoformat(day+'T'+clock)
    except ValueError:raise PulseError('pulse_time_invalid') from None
    target=naive.replace(tzinfo=zone(tz),fold=0)
    if datetime.fromtimestamp(target.timestamp(),zone(tz)).replace(tzinfo=None)!=naive:raise PulseError('pulse_dst_gap')
    return int(target.timestamp())

def next_local(schedule,tz,after):
    local=datetime.fromtimestamp(after,zone(tz))
    for offset in range(371):
        day=local.date()+timedelta(days=offset)
        if schedule['kind']=='weekly' and day.weekday() not in schedule['weekdays']:continue
        try:value=local_epoch(day.isoformat(),schedule['time'],tz)
        except PulseError:continue
        if value>after:return value
    raise PulseError('pulse_time_invalid')

def quiet_time(at,prefs,respect=True):
    if not respect or not prefs['quiet'] or prefs['quiet_start']==prefs['quiet_end']:return at
    local=datetime.fromtimestamp(at,zone(prefs['timezone']));clock=local.strftime('%H:%M');start,end=prefs['quiet_start'],prefs['quiet_end']
    inside=start<=clock<end if start<end else clock>=start or clock<end
    if not inside:return at
    day=local.date()+timedelta(days=int(start>end and clock>=start))
    for extra in range(180):
        candidate=datetime.combine(day,datetime.min.time())+timedelta(minutes=int(end[:2])*60+int(end[3:])+extra)
        try:return local_epoch(candidate.date().isoformat(),candidate.strftime('%H:%M'),prefs['timezone'])
        except PulseError:continue
    raise PulseError('pulse_time_invalid')

def _key(user):
    secret=os.getenv('VELIA_NOTEBOOK_SECRET') or os.getenv('MASTER_ENCRYPTION_KEY','')
    if len(secret)<24:raise PulseError('pulse_key_unavailable',503)
    raw=hmac.new(secret.encode(),('velia-pulse-user-v1:'+str(user)).encode(),hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(raw))
def pack(user,value):return _key(user).encrypt(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()).decode()
def unpack(user,value):return json.loads(_key(user).decrypt(value.encode()))

@contextmanager
def transaction():
    connection=get_connection();cursor=connection.cursor()
    try:yield cursor;connection.commit()
    except Exception:connection.rollback();raise
    finally:cursor.close();connection.close()

def ensure_schema():
    global _READY
    if _READY:return
    with _LOCK:
        if _READY:return
        with transaction() as c:
            c.execute('CREATE TABLE IF NOT EXISTS velia_pulse_users (user_id BIGINT PRIMARY KEY, prefs TEXT NOT NULL, digest_at BIGINT)')
            c.execute('CREATE TABLE IF NOT EXISTS velia_pulse_items (user_id BIGINT NOT NULL, item_id TEXT NOT NULL, revision BIGINT NOT NULL, state TEXT NOT NULL, due_at BIGINT, notify_at BIGINT, payload TEXT NOT NULL, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL, PRIMARY KEY(user_id,item_id))')
            c.execute('CREATE TABLE IF NOT EXISTS velia_pulse_notifications (user_id BIGINT NOT NULL, notification_id TEXT NOT NULL, item_id TEXT NOT NULL, occurrence_at BIGINT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, created_at BIGINT NOT NULL, delivery TEXT NOT NULL, delivery_at BIGINT, followup_at BIGINT, PRIMARY KEY(user_id,notification_id), UNIQUE(user_id,item_id,occurrence_at,kind))')
            c.execute('CREATE INDEX IF NOT EXISTS velia_pulse_due ON velia_pulse_items(state,notify_at)')
            c.execute('CREATE INDEX IF NOT EXISTS velia_pulse_outbox ON velia_pulse_notifications(delivery,delivery_at)')
            c.execute('CREATE INDEX IF NOT EXISTS velia_pulse_digest ON velia_pulse_users(digest_at)')
            c.execute('CREATE INDEX IF NOT EXISTS velia_pulse_notice_age ON velia_pulse_notifications(created_at)')
            c.execute('CREATE TABLE IF NOT EXISTS velia_pulse_meta (name TEXT PRIMARY KEY, value BIGINT NOT NULL)')
            from services import velia_webpush_service as push
            push.schema(c)
        _READY=True

def _user(c,user,lock=False,tz='UTC'):
    if not lock:
        c.execute('SELECT prefs FROM velia_pulse_users WHERE user_id=%s',(int(user),));row=c.fetchone()
        if row:return unpack(user,row[0])
    prefs={**DEFAULTS,'timezone':tz,'_timezone_set':tz!='UTC'}
    c.execute('INSERT INTO velia_pulse_users(user_id,prefs) VALUES (%s,%s) ON CONFLICT(user_id) DO NOTHING',(int(user),pack(user,prefs)))
    c.execute('SELECT prefs FROM velia_pulse_users WHERE user_id=%s'+(' FOR UPDATE' if lock else ''),(int(user),))
    return unpack(user,c.fetchone()[0])

def settings(user,patch=None):
    ensure_schema()
    with transaction() as c:
        prefs=_user(c,user,True)
        if patch is not None:
            if not isinstance(patch,dict) or set(patch)-set(DEFAULTS):raise PulseError('pulse_settings_invalid')
            prefs.update(patch);zone(prefs['timezone'])
            if 'timezone' in patch:prefs['_timezone_set']=True
            for field in ('quiet_start','quiet_end','digest_time'):
                if not isinstance(prefs[field],str) or not TIME.fullmatch(prefs[field]):raise PulseError('pulse_time_invalid')
            for field in ('quiet','telegram','preview','digest'):
                if type(prefs[field]) is not bool:raise PulseError('pulse_settings_invalid')
            digest_at=next_local({'kind':'daily','time':prefs['digest_time']},prefs['timezone'],now()) if prefs['digest'] else None
            c.execute('UPDATE velia_pulse_users SET prefs=%s,digest_at=%s WHERE user_id=%s',(pack(user,prefs),digest_at,int(user)))
            c.execute("SELECT item_id,due_at,payload FROM velia_pulse_items WHERE user_id=%s AND state='active' AND due_at IS NOT NULL",(int(user),))
            for iid,due,raw in c.fetchall():
                value=unpack(user,raw);c.execute('UPDATE velia_pulse_items SET notify_at=%s WHERE user_id=%s AND item_id=%s',(quiet_time(max(now(),due),prefs,value['respect_quiet']),int(user),iid))
            c.execute("SELECT notification_id,payload FROM velia_pulse_notifications WHERE user_id=%s AND delivery='pending'",(int(user),))
            for nid,raw in c.fetchall():
                value=unpack(user,raw);c.execute('UPDATE velia_pulse_notifications SET delivery_at=%s WHERE user_id=%s AND notification_id=%s',(quiet_time(now(),prefs,value.get('respect_quiet',True)),int(user),nid))
            if not prefs['telegram']:c.execute("UPDATE velia_pulse_notifications SET delivery='off' WHERE user_id=%s AND delivery='pending'",(int(user),))
        return prefs

def normalize(data,at,allow_past=False):
    allowed={'id','title','timezone','schedule','resource','followup_minutes','respect_quiet','after_id','after_minutes'}
    if not isinstance(data,dict) or set(data)-allowed:raise PulseError('pulse_invalid')
    item_id=data.get('id');title=data.get('title');tz=data.get('timezone')
    if not isinstance(item_id,str) or not ID.fullmatch(item_id) or not isinstance(title,str) or not title.strip() or len(title)>500:raise PulseError('pulse_invalid')
    zone(tz);schedule=data.get('schedule');after=data.get('after_id')
    if after:
        if not isinstance(after,str) or not ID.fullmatch(after) or after==item_id:raise PulseError('pulse_dependency_invalid')
        delay=data.get('after_minutes',0)
        if type(delay) is not int or not 0<=delay<=10080:raise PulseError('pulse_dependency_invalid')
        schedule={'kind':'after','minutes':delay};due=None
    elif isinstance(schedule,dict) and schedule.get('kind')=='once':
        if set(schedule)!={'kind','date','time'}:raise PulseError('pulse_invalid')
        due=local_epoch(schedule['date'],schedule['time'],tz)
        if (due<=at and not allow_past) or due>at+366*86400*5:raise PulseError('pulse_time_past')
    elif isinstance(schedule,dict) and schedule.get('kind') in ('daily','weekly'):
        kind=schedule['kind'];allowed_keys={'kind','time'}|({'weekdays'} if kind=='weekly' else set())
        if set(schedule)!=allowed_keys or not isinstance(schedule.get('time'),str) or not TIME.fullmatch(schedule['time']):raise PulseError('pulse_invalid')
        if kind=='weekly' and (not isinstance(schedule['weekdays'],list) or not 1<=len(schedule['weekdays'])<=7 or any(type(d) is not int or not 0<=d<=6 for d in schedule['weekdays']) or len(set(schedule['weekdays']))!=len(schedule['weekdays'])):raise PulseError('pulse_invalid')
        due=next_local(schedule,tz,at)
    else:raise PulseError('pulse_invalid')
    resource=data.get('resource')
    if resource is not None and (not isinstance(resource,dict) or set(resource)!={'kind','id'} or resource['kind'] not in ('note','place','visit','material','chat') or not isinstance(resource['id'],str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}',resource['id'])):raise PulseError('pulse_resource_invalid')
    follow=data.get('followup_minutes',0);respect=data.get('respect_quiet',True)
    if type(follow) is not int or not (follow==0 or 5<=follow<=10080) or type(respect) is not bool:raise PulseError('pulse_invalid')
    return item_id,{'title':title.strip(),'timezone':tz,'schedule':schedule,'resource':resource,'followup_minutes':follow,'respect_quiet':respect,'after_id':after},due

COLS='item_id,revision,state,due_at,notify_at,payload,created_at,updated_at'
def _item(user,row):
    keys=COLS.split(',');value=dict(zip(keys,row));value.update(unpack(user,value.pop('payload')));return value

def _get(c,user,item_id,lock=False):
    c.execute('SELECT '+COLS+' FROM velia_pulse_items WHERE user_id=%s AND item_id=%s'+(' FOR UPDATE' if lock else ''),(int(user),str(item_id)))
    row=c.fetchone()
    if not row:raise PulseError('pulse_not_found',404)
    return _item(user,row)

def create(user,data):
    at=now();item_id,payload,due=normalize(data,at,allow_past=True);ensure_schema()
    with transaction() as c:
        prefs=_user(c,user,True,payload['timezone'])
        if not prefs.get('_timezone_set'):
            prefs['timezone']=payload['timezone'];prefs['_timezone_set']=True;c.execute('UPDATE velia_pulse_users SET prefs=%s WHERE user_id=%s',(pack(user,prefs),int(user)))
        c.execute('SELECT '+COLS+' FROM velia_pulse_items WHERE user_id=%s AND item_id=%s',(int(user),item_id));existing=c.fetchone()
        if existing:
            value=_item(user,existing)
            if any(value.get(k)!=v for k,v in payload.items()):raise PulseError('pulse_id_conflict',409)
            return value
        if payload['schedule']['kind']=='once' and due<=at:raise PulseError('pulse_time_past')
        c.execute('SELECT COUNT(*) FROM velia_pulse_items WHERE user_id=%s',(int(user),))
        if c.fetchone()[0]>=1000:raise PulseError('pulse_history_limit',409)
        c.execute("SELECT COUNT(*) FROM velia_pulse_items WHERE user_id=%s AND state IN ('active','waiting')",(int(user),))
        if c.fetchone()[0]>=100:raise PulseError('pulse_limit',409)
        state='active'
        if payload['after_id']:
            parent=_get(c,user,payload['after_id'])
            if parent['state']=='cancelled':raise PulseError('pulse_dependency_cancelled',409)
            if parent['state']=='done':due=at+payload['schedule']['minutes']*60
            else:state='waiting'
        notification_at=quiet_time(due,prefs,payload['respect_quiet']) if due is not None else None
        c.execute('INSERT INTO velia_pulse_items(user_id,item_id,revision,state,due_at,notify_at,payload,created_at,updated_at) VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s)',(int(user),item_id,state,due,notification_at,pack(user,payload),at,at))
        return _get(c,user,item_id)

def listing(user):
    ensure_schema()
    with transaction() as c:
        prefs=_user(c,user)
        c.execute('SELECT '+COLS+" FROM velia_pulse_items WHERE user_id=%s ORDER BY CASE WHEN state IN ('active','waiting') THEN 0 ELSE 1 END,COALESCE(due_at,0),created_at DESC LIMIT 300",(int(user),))
        items=[_item(user,row) for row in c.fetchall()]
        c.execute('SELECT notification_id,item_id,occurrence_at,kind,status,payload,created_at,delivery FROM velia_pulse_notifications WHERE user_id=%s ORDER BY created_at DESC,notification_id LIMIT 100',(int(user),))
        inbox=[]
        for row in c.fetchall():
            value=dict(zip(['id','item_id','occurrence_at','kind','status','payload','created_at','delivery'],row));value.update(unpack(user,value.pop('payload')));inbox.append(value)
        c.execute("SELECT item_id,MAX(occurrence_at) FROM velia_pulse_notifications WHERE user_id=%s AND kind='base' AND status IN ('unread','read') GROUP BY item_id",(int(user),));pending=dict(c.fetchall())
        for item in items:item['action_at']=pending.get(item['item_id'],item['due_at'])
        c.execute("SELECT value FROM velia_pulse_meta WHERE name='heartbeat'");heartbeat=c.fetchone()
        return {'items':items,'notifications':inbox,'settings':prefs,'worker_enabled':enabled(),'worker_last_tick':heartbeat[0] if heartbeat else None}

def _emit(c,user,item_id,occurrence,kind,payload,prefs,at,followup=None):
    if kind=='base':c.execute("UPDATE velia_pulse_notifications SET status='superseded',followup_at=NULL,delivery=CASE WHEN delivery='pending' THEN 'off' ELSE delivery END WHERE user_id=%s AND item_id=%s AND occurrence_at<%s AND status IN ('unread','read')",(int(user),item_id,occurrence))
    nid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'velia:pulse:{user}:{item_id}:{occurrence}:{kind}'))
    c.execute('INSERT INTO velia_pulse_notifications(user_id,notification_id,item_id,occurrence_at,kind,status,payload,created_at,delivery,delivery_at,followup_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,item_id,occurrence_at,kind) DO NOTHING',(int(user),nid,item_id,occurrence,kind,'unread',pack(user,payload),at,'pending' if prefs['telegram'] else 'off',quiet_time(at,prefs,payload.get('respect_quiet',True)),followup))
    from services import velia_webpush_service as push
    push.enqueue(c,user,nid,at)

def action(user,item_id,data):
    ensure_schema();at=now()
    if not isinstance(data,dict) or set(data)-{'action','revision','occurrence_at','date','time','timezone','result'}:raise PulseError('pulse_invalid')
    verb=data.get('action')
    if verb not in ('complete','snooze','cancel'):raise PulseError('pulse_invalid')
    if type(data.get('revision')) is not int:raise PulseError('pulse_revision_invalid')
    with transaction() as c:
        prefs=_user(c,user,True);item=_get(c,user,item_id,True)
        occurrence=data.get('occurrence_at')
        if type(occurrence) is not int and verb!='cancel':raise PulseError('pulse_occurrence_invalid')
        c.execute("SELECT status FROM velia_pulse_notifications WHERE user_id=%s AND item_id=%s AND occurrence_at=%s AND kind='base'",(int(user),item_id,occurrence if type(occurrence) is int else 0));notice=c.fetchone()
        if verb=='complete' and (item['state']=='done' or notice and notice[0]=='done'):return item
        if item['revision']!=data['revision']:raise PulseError('pulse_revision_conflict',409)
        if item['state'] not in ('active','waiting'):raise PulseError('pulse_not_active',409)
        payload={key:item.get(key) for key in ('title','timezone','schedule','resource','followup_minutes','respect_quiet','after_id')}
        state=item['state'];due=item['due_at'];notify=item['notify_at']
        if verb!='cancel' and (state=='waiting' or occurrence!=due and not notice):raise PulseError('pulse_occurrence_invalid')
        if verb=='cancel':
            state='cancelled';notify=None;due=None
            c.execute("SELECT item_id,payload FROM velia_pulse_items WHERE user_id=%s AND state='waiting'",(int(user),));waiting=[(iid,unpack(user,raw).get('after_id')) for iid,raw in c.fetchall()];cancelled={item_id}
            for _ in range(len(waiting)):
                for iid,parent in waiting:
                    if parent in cancelled:cancelled.add(iid)
            for iid in cancelled-{item_id}:c.execute("UPDATE velia_pulse_items SET state='cancelled',revision=revision+1,updated_at=%s WHERE user_id=%s AND item_id=%s",(at,int(user),iid))
            c.execute("UPDATE velia_pulse_notifications SET status='done',delivery=CASE WHEN delivery='pending' THEN 'off' ELSE delivery END,followup_at=NULL WHERE user_id=%s AND item_id=%s",(int(user),item_id))
        elif verb=='snooze':
            tz=data.get('timezone',item['timezone']);due=local_epoch(data.get('date'),data.get('time'),tz)
            if due<=at or due>at+366*86400*5:raise PulseError('pulse_time_past')
            if item['schedule']['kind']=='once':payload['schedule']={'kind':'once','date':data['date'],'time':data['time']};payload['timezone']=tz
            notify=quiet_time(due,prefs,item['respect_quiet']);state='active'
            c.execute("UPDATE velia_pulse_notifications SET status='snoozed',delivery=CASE WHEN delivery='pending' THEN 'off' ELSE delivery END,followup_at=NULL WHERE user_id=%s AND item_id=%s AND occurrence_at=%s",(int(user),item_id,occurrence))
        elif verb=='complete':
            result=data.get('result','')
            if not isinstance(result,str) or len(result)>2000:raise PulseError('pulse_result_invalid')
            if not notice:_emit(c,user,item_id,occurrence,'base',{**payload,'result':result}, {**prefs,'telegram':False},at)
            else:
                c.execute("SELECT payload FROM velia_pulse_notifications WHERE user_id=%s AND item_id=%s AND occurrence_at=%s AND kind='base'",(int(user),item_id,occurrence));saved=unpack(user,c.fetchone()[0]);saved['result']=result
                c.execute("UPDATE velia_pulse_notifications SET payload=%s WHERE user_id=%s AND item_id=%s AND occurrence_at=%s AND kind='base'",(pack(user,saved),int(user),item_id,occurrence))
            c.execute("UPDATE velia_pulse_notifications SET status='done',delivery=CASE WHEN delivery='pending' THEN 'off' ELSE delivery END,followup_at=NULL WHERE user_id=%s AND item_id=%s AND occurrence_at=%s",(int(user),item_id,occurrence))
            if item['schedule']['kind'] in ('once','after'):state='done';due=None;notify=None;payload['result']=result
            elif occurrence==due:due=next_local(item['schedule'],item['timezone'],max(at,occurrence));notify=quiet_time(due,prefs,item['respect_quiet'])
            # Completing one recurring occurrence keeps the series active.
            c.execute("SELECT "+COLS+" FROM velia_pulse_items WHERE user_id=%s AND state='waiting'",(int(user),))
            for row in c.fetchall():
                child=_item(user,row)
                if child.get('after_id')==item_id:
                    start=at+child['schedule']['minutes']*60
                    c.execute("UPDATE velia_pulse_items SET state='active',due_at=%s,notify_at=%s,revision=revision+1,updated_at=%s WHERE user_id=%s AND item_id=%s",(start,quiet_time(start,prefs,child['respect_quiet']),at,int(user),child['item_id']))
        c.execute('UPDATE velia_pulse_items SET state=%s,due_at=%s,notify_at=%s,payload=%s,revision=revision+1,updated_at=%s WHERE user_id=%s AND item_id=%s',(state,due,notify,pack(user,payload),at,int(user),item_id))
        return _get(c,user,item_id)

def read_notification(user,nid):
    ensure_schema()
    with transaction() as c:
        c.execute("UPDATE velia_pulse_notifications SET status='read' WHERE user_id=%s AND notification_id=%s AND status='unread'",(int(user),str(nid)))

def tick(at=None):
    ensure_schema();at=now() if at is None else at
    with transaction() as c:
        c.execute("SELECT user_id,"+COLS+" FROM velia_pulse_items WHERE state='active' AND notify_at<=%s ORDER BY notify_at LIMIT 100 FOR UPDATE SKIP LOCKED",(at,));rows=c.fetchall()
        for row in rows:
            user=row[0];item=_item(user,row[1:]);prefs=_user(c,user)
            effective=quiet_time(at,prefs,item['respect_quiet'])
            if effective>at:c.execute('UPDATE velia_pulse_items SET notify_at=%s WHERE user_id=%s AND item_id=%s',(effective,int(user),item['item_id']));continue
            payload={key:item.get(key) for key in ('title','timezone','resource','respect_quiet')};follow=at+item['followup_minutes']*60 if item['followup_minutes'] else None
            _emit(c,user,item['item_id'],item['due_at'],'base',payload,prefs,at,follow)
            due=next_local(item['schedule'],item['timezone'],at) if item['schedule']['kind'] in ('daily','weekly') else None
            c.execute('UPDATE velia_pulse_items SET due_at=%s,notify_at=%s,revision=revision+1,updated_at=%s WHERE user_id=%s AND item_id=%s',(due,quiet_time(due,prefs,item['respect_quiet']) if due else None,at,int(user),item['item_id']))
        c.execute("SELECT user_id,notification_id,item_id,occurrence_at,payload FROM velia_pulse_notifications WHERE kind='base' AND status IN ('unread','read') AND followup_at<=%s LIMIT 100 FOR UPDATE SKIP LOCKED",(at,))
        for user,nid,item_id,occurrence,payload in c.fetchall():
            prefs=_user(c,user);value=unpack(user,payload);effective=quiet_time(at,prefs,value.get('respect_quiet',True))
            if effective>at:c.execute('UPDATE velia_pulse_notifications SET followup_at=%s WHERE user_id=%s AND notification_id=%s',(effective,int(user),nid));continue
            _emit(c,user,item_id,occurrence,'followup',value,prefs,at)
            c.execute('UPDATE velia_pulse_notifications SET followup_at=NULL WHERE user_id=%s AND notification_id=%s',(int(user),nid))
        c.execute('SELECT user_id,prefs FROM velia_pulse_users WHERE digest_at<=%s LIMIT 100 FOR UPDATE SKIP LOCKED',(at,))
        for user,payload in c.fetchall():
            prefs=unpack(user,payload);effective=quiet_time(at,prefs)
            if effective>at:c.execute('UPDATE velia_pulse_users SET digest_at=%s WHERE user_id=%s',(effective,int(user)));continue
            day=datetime.fromtimestamp(at,zone(prefs['timezone'])).date().isoformat()
            # Recovery can land on a spring-forward date whose digest clock never
            # existed. Use its first valid minute, so one user cannot roll back
            # every other user's reminders on each scheduler tick.
            requested=datetime.fromisoformat(day+'T'+prefs['digest_time'])
            for extra in range(181):
                candidate=requested+timedelta(minutes=extra)
                try:occ=local_epoch(candidate.date().isoformat(),candidate.strftime('%H:%M'),prefs['timezone']);break
                except PulseError as error:
                    if error.code!='pulse_dst_gap':raise
            else:raise PulseError('pulse_time_invalid')
            _emit(c,user,'digest',occ,'digest',{'title':'План на '+day,'timezone':prefs['timezone'],'resource':None},prefs,at)
            due=next_local({'kind':'daily','time':prefs['digest_time']},prefs['timezone'],at)
            c.execute('UPDATE velia_pulse_users SET digest_at=%s WHERE user_id=%s',(due,int(user)))
        c.execute("DELETE FROM velia_pulse_notifications WHERE created_at<%s AND (status IN ('done','snoozed','superseded') OR (kind='digest' AND status='read'))",(at-90*86400,))
        c.execute("UPDATE velia_pulse_notifications SET delivery='unknown' WHERE delivery='sending' AND delivery_at<%s",(at-180,))
        c.execute("INSERT INTO velia_pulse_meta(name,value) VALUES ('heartbeat',%s) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(at,))

def claim_delivery():
    ensure_schema();at=now()
    with transaction() as c:
        c.execute("SELECT user_id,notification_id,payload,item_id FROM velia_pulse_notifications WHERE delivery='pending' AND delivery_at<=%s AND status IN ('unread','read') ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED",(at,));row=c.fetchone()
        if not row:return None
        user,nid,payload,item_id=row;prefs=_user(c,user)
        if not prefs['telegram']:c.execute("UPDATE velia_pulse_notifications SET delivery='off' WHERE user_id=%s AND notification_id=%s",(int(user),nid));return None
        value=unpack(user,payload);effective=quiet_time(at,prefs,value.get('respect_quiet',True))
        if effective>at:c.execute('UPDATE velia_pulse_notifications SET delivery_at=%s WHERE user_id=%s AND notification_id=%s',(effective,int(user),nid));return None
        c.execute("UPDATE velia_pulse_notifications SET delivery='sending',delivery_at=%s WHERE user_id=%s AND notification_id=%s",(at,int(user),nid))
        return {'user_id':user,'id':nid,'title':value['title'],'preview':prefs['preview']}

def delivery_result(user,nid,status):
    if status not in ('sent','failed','unknown'):raise ValueError()
    with transaction() as c:c.execute("UPDATE velia_pulse_notifications SET delivery=%s WHERE user_id=%s AND notification_id=%s AND delivery='sending'",(status,int(user),nid))


def delete(user,item_id):
    ensure_schema()
    with transaction() as c:
        _user(c,user,True);_get(c,user,item_id,True)
        c.execute("SELECT payload FROM velia_pulse_items WHERE user_id=%s AND state='waiting'",(int(user),))
        if any(unpack(user,row[0]).get('after_id')==item_id for row in c.fetchall()):raise PulseError('pulse_has_dependents',409)
        c.execute('DELETE FROM velia_pulse_notifications WHERE user_id=%s AND item_id=%s',(int(user),item_id))
        c.execute('DELETE FROM velia_pulse_items WHERE user_id=%s AND item_id=%s',(int(user),item_id))
