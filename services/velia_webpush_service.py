"""Account-bound Web Push subscriptions and a durable, bounded Pulse outbox.

Only standard browser push hosts are accepted. Subscription secrets are encrypted
with the existing account key; neither endpoints nor keys appear in responses.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import uuid
from urllib.parse import urlsplit

from aiohttp import ClientTimeout
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from services import velia_pulse_service as pulse


def b64(raw):return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def vapid():
    from py_vapid import Vapid
    secret=os.getenv('VELIA_NOTEBOOK_SECRET') or os.getenv('MASTER_ENCRYPTION_KEY','')
    if len(secret)<24:raise pulse.PulseError('pulse_key_unavailable',503)
    # Domain-separated, stable across replicas/restarts; never sent to a client.
    order=0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
    for counter in range(100):
        scalar=int.from_bytes(hmac.new(secret.encode(),f'velia-webpush-vapid-v1:{counter}'.encode(),hashlib.sha256).digest(),'big')
        if 0<scalar<order:return Vapid(private_key=ec.derive_private_key(scalar,ec.SECP256R1()))
    raise pulse.PulseError('pulse_key_unavailable',503)


def config():
    key=vapid().public_key.public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    return {'public_key':b64(key),'available':pulse.enabled()}


def schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS velia_push_devices (device_id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, endpoint_hash TEXT NOT NULL UNIQUE, binding_id TEXT NOT NULL, origin TEXT NOT NULL, payload TEXT NOT NULL, expires_at BIGINT NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS velia_push_owner ON velia_push_devices(user_id,expires_at)')
    c.execute('CREATE INDEX IF NOT EXISTS velia_push_expiry ON velia_push_devices(expires_at)')
    c.execute('CREATE TABLE IF NOT EXISTS velia_push_deliveries (delivery_id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, notification_id TEXT NOT NULL, device_id TEXT NOT NULL, binding_id TEXT NOT NULL, status TEXT NOT NULL, due_at BIGINT NOT NULL, expires_at BIGINT NOT NULL, attempts BIGINT NOT NULL, updated_at BIGINT NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS velia_push_outbox ON velia_push_deliveries(status,due_at)')
    c.execute('CREATE INDEX IF NOT EXISTS velia_push_delivery_age ON velia_push_deliveries(expires_at)')


def valid_origin(value):
    if not isinstance(value,str) or len(value)>300:raise pulse.PulseError('pulse_push_invalid')
    url=urlsplit(value)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/') or url.port not in (None,443):raise pulse.PulseError('pulse_push_invalid')
    # The authenticated gateway supplies its configured origin, never user input.
    return value.rstrip('/')


def subscription(value):
    if not isinstance(value,dict) or set(value)-{'endpoint','keys','expirationTime'}:raise pulse.PulseError('pulse_push_invalid')
    endpoint=value.get('endpoint');keys=value.get('keys')
    if not isinstance(endpoint,str) or len(endpoint)>4096 or any(ord(ch)<33 or ord(ch)>126 for ch in endpoint):raise pulse.PulseError('pulse_push_invalid')
    url=urlsplit(endpoint);host=url.hostname or ''
    allowed=host in ('fcm.googleapis.com','updates.push.services.mozilla.com') or host.endswith('.push.apple.com') or host.endswith('.notify.windows.com')
    if not allowed or url.scheme!='https' or url.username or url.password or url.fragment or url.port not in (None,443) or not url.path.startswith('/'):raise pulse.PulseError('pulse_push_invalid')
    if not isinstance(keys,dict) or set(keys)!={'auth','p256dh'}:raise pulse.PulseError('pulse_push_invalid')
    decoded={}
    for name,size in [('auth',16),('p256dh',65)]:
        raw=keys[name]
        if not isinstance(raw,str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,90}={0,2}',raw):raise pulse.PulseError('pulse_push_invalid')
        try:decoded[name]=base64.urlsafe_b64decode(raw+'='*(-len(raw)%4))
        except ValueError:raise pulse.PulseError('pulse_push_invalid') from None
        if len(decoded[name])!=size:raise pulse.PulseError('pulse_push_invalid')
    try:ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),decoded['p256dh'])
    except ValueError:raise pulse.PulseError('pulse_push_invalid') from None
    return {'endpoint':endpoint,'keys':{name:b64(raw) for name,raw in decoded.items()}}


def register(user,data):
    if set(data)-{'subscription','binding_id','origin','label'}:raise pulse.PulseError('pulse_push_invalid')
    sub=subscription(data.get('subscription'));binding=data.get('binding_id');origin=valid_origin(data.get('origin'))
    if not isinstance(binding,str) or not pulse.ID.fullmatch(binding):raise pulse.PulseError('pulse_push_invalid')
    label=data.get('label','Браузер')
    if not isinstance(label,str) or len(label)>80:raise pulse.PulseError('pulse_push_invalid')
    config();pulse.ensure_schema();at=pulse.now();digest=hashlib.sha256(sub['endpoint'].encode()).hexdigest()
    with pulse.transaction() as c:
        pulse._user(c,user,True)
        c.execute('SELECT device_id,user_id,binding_id FROM velia_push_devices WHERE endpoint_hash=%s FOR UPDATE',(digest,));row=c.fetchone()
        if row and row[1]!=int(user):raise pulse.PulseError('pulse_push_owner_conflict',409)
        c.execute('SELECT COUNT(*) FROM velia_push_devices WHERE user_id=%s AND expires_at>%s',(int(user),at))
        if not row and c.fetchone()[0]>=20:raise pulse.PulseError('pulse_push_limit',409)
        device=row[0] if row else str(uuid.uuid4());raw=pulse.pack(user,{'subscription':sub,'label':label})
        if row:
            c.execute('UPDATE velia_push_devices SET binding_id=%s,origin=%s,payload=%s,expires_at=%s WHERE device_id=%s',(binding,origin,raw,253402300799,device))
            if row[2]!=binding:c.execute("UPDATE velia_push_deliveries SET status='skipped' WHERE device_id=%s AND status='pending'",(device,))
        else:c.execute('INSERT INTO velia_push_devices(device_id,user_id,endpoint_hash,binding_id,origin,payload,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',(device,int(user),digest,binding,origin,raw,253402300799))
        return {'device_id':device,'binding_id':binding}


def remove(user,device,binding):
    if any(not isinstance(value,str) or not pulse.ID.fullmatch(value) for value in (device,binding)):raise pulse.PulseError('pulse_push_invalid')
    pulse.ensure_schema()
    with pulse.transaction() as c:
        c.execute('DELETE FROM velia_push_devices WHERE user_id=%s AND device_id=%s AND binding_id=%s',(int(user),device,binding))
        c.execute("UPDATE velia_push_deliveries SET status='skipped',updated_at=%s WHERE user_id=%s AND device_id=%s AND binding_id=%s AND status='pending'",(pulse.now(),int(user),device,binding))


def enqueue(c,user,nid,at):
    c.execute('SELECT device_id,binding_id FROM velia_push_devices WHERE user_id=%s AND expires_at>%s',(int(user),at))
    for device,binding in c.fetchall():
        job=str(uuid.uuid5(uuid.NAMESPACE_URL,f'velia:push:{nid}:{device}:{binding}'))
        c.execute("INSERT INTO velia_push_deliveries(delivery_id,user_id,notification_id,device_id,binding_id,status,due_at,expires_at,attempts,updated_at) VALUES (%s,%s,%s,%s,%s,'pending',%s,%s,0,%s) ON CONFLICT(delivery_id) DO NOTHING",(job,int(user),nid,device,binding,at,at+86400,at))


def claim():
    pulse.ensure_schema();at=pulse.now()
    with pulse.transaction() as c:
        c.execute("UPDATE velia_push_deliveries SET status='unknown' WHERE status='sending' AND updated_at<%s",(at-180,))
        c.execute('DELETE FROM velia_push_deliveries WHERE expires_at<%s',(at-7*86400,))
        c.execute('DELETE FROM velia_push_devices WHERE expires_at<%s',(at,))
        for _ in range(20):
            c.execute("SELECT delivery_id,user_id,notification_id,device_id,binding_id,expires_at,attempts FROM velia_push_deliveries WHERE status='pending' AND due_at<=%s ORDER BY due_at LIMIT 1 FOR UPDATE SKIP LOCKED",(at,));row=c.fetchone()
            if not row:return None
            job,user,nid,device,binding,expiry,attempts=row
            c.execute('SELECT payload,origin FROM velia_push_devices WHERE user_id=%s AND device_id=%s AND binding_id=%s AND expires_at>%s',(user,device,binding,at));target=c.fetchone()
            c.execute('SELECT payload,status FROM velia_pulse_notifications WHERE user_id=%s AND notification_id=%s',(user,nid));notice=c.fetchone()
            if not target or not notice or notice[1] not in ('unread','read') or expiry<=at:
                c.execute("UPDATE velia_push_deliveries SET status='skipped',updated_at=%s WHERE delivery_id=%s",(at,job));continue
            prefs=pulse._user(c,user);value=pulse.unpack(user,notice[0]);due=pulse.quiet_time(at,prefs,value.get('respect_quiet',True))
            if due>at:
                c.execute('UPDATE velia_push_deliveries SET due_at=%s WHERE delivery_id=%s',(due,job));continue
            sub=pulse.unpack(user,target[0])['subscription'];subscription(sub)
            c.execute("UPDATE velia_push_deliveries SET status='sending',attempts=attempts+1,updated_at=%s WHERE delivery_id=%s",(at,job))
            # Private content is supplied only when explicitly enabled in preferences.
            payload={'owner':hashlib.sha256((target[1]+':'+str(user)).encode()).hexdigest()[:24],
                'device_id':device,'binding_id':binding,'notification_id':nid,'delivery_id':job,
                'body':value['title'][:500] if prefs['preview'] else '', 'preview':prefs['preview']}
            return {'id':job,'user_id':user,'device_id':device,'binding_id':binding,'subscription':sub,'payload':payload,'origin':target[1],'attempts':attempts+1}
        return None


def wire(notice):
    from pywebpush import WebPusher
    sub=subscription(notice['subscription']);url=urlsplit(sub['endpoint'])
    headers=vapid().sign({'aud':url.scheme+'://'+url.netloc,'sub':valid_origin(notice['origin']),'exp':pulse.now()+3600})
    headers.update({'Content-Encoding':'aes128gcm','Content-Type':'application/octet-stream','TTL':'3600','Urgency':'high'})
    body=WebPusher(sub).encode(json.dumps(notice['payload'],ensure_ascii=False,separators=(',',':')).encode())['body']
    return sub['endpoint'],headers,body


def result(notice,status):
    at=pulse.now();state='accepted' if 200<=status<300 else 'failed' if status else 'unknown'
    if (status==429 or 500<=status<600) and notice['attempts']<3:state='pending'
    with pulse.transaction() as c:
        if status in (404,410):
            c.execute('DELETE FROM velia_push_devices WHERE user_id=%s AND device_id=%s AND binding_id=%s',(notice['user_id'],notice['device_id'],notice['binding_id']))
        c.execute("UPDATE velia_push_deliveries SET status=%s,due_at=%s,updated_at=%s WHERE delivery_id=%s AND status='sending'",(state,at+60*(2**notice['attempts']),at,notice['id']))


def receipt(user,data):
    if set(data)!={'delivery_id','device_id','binding_id'} or any(not isinstance(v,str) or not pulse.ID.fullmatch(v) for v in data.values()):raise pulse.PulseError('pulse_push_invalid')
    pulse.ensure_schema()
    with pulse.transaction() as c:
        c.execute("UPDATE velia_push_deliveries SET status='displayed',updated_at=%s WHERE user_id=%s AND delivery_id=%s AND device_id=%s AND binding_id=%s AND status IN ('sending','accepted','unknown') AND EXISTS (SELECT 1 FROM velia_push_devices WHERE user_id=%s AND device_id=%s AND binding_id=%s)",(pulse.now(),int(user),data['delivery_id'],data['device_id'],data['binding_id'],int(user),data['device_id'],data['binding_id']))


async def deliver(session,notice):
    import asyncio
    from db.database import is_user_banned
    status=0
    try:
        if await asyncio.to_thread(is_user_banned,notice['user_id']):status=403
        else:
            endpoint,headers,body=await asyncio.to_thread(wire,notice)
            async with session.post(endpoint,data=body,headers=headers,allow_redirects=False,timeout=ClientTimeout(total=15,connect=5)) as response:status=response.status
    except asyncio.CancelledError:raise
    except Exception:pass  # No endpoints, key material or reminder text in logs.
    await asyncio.to_thread(result,notice,status)
