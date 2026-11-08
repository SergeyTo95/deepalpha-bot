import asyncio
import base64
import json
import sqlite3
import uuid
from types import SimpleNamespace

import http_ece
import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from services import velia_pulse_routes as routes
from services import velia_webpush_service as push
from services import velia_pulse_service as pulse
from test_velia_pulse import database, draft, complete

ORIGIN='https://velia.example'
def device(**patch):
    private=ec.generate_private_key(ec.SECP256R1());auth=b'0123456789abcdef'
    public=private.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    return {'subscription':{'endpoint':'https://fcm.googleapis.com/fcm/send/'+str(uuid.uuid4()),'keys':{'p256dh':push.b64(public),'auth':push.b64(auth)}},'binding_id':str(uuid.uuid4()),'origin':ORIGIN,**patch},private,auth

def queued(clock):
    data,private,auth=device();saved=push.register(7,data);item=pulse.create(7,draft());clock[0]+=3600;pulse.tick();return saved,push.claim(),private,auth,item

def status(path):
    with sqlite3.connect(path) as c:return c.execute('SELECT status FROM velia_push_deliveries ORDER BY delivery_id').fetchall()

def test_stable_vapid_key_is_domain_separated(database,monkeypatch):
    assert len(base64.urlsafe_b64decode(push.config()['public_key']+'='))==65
    first=push.config()['public_key'];assert push.config()['public_key']==first
    monkeypatch.setenv('MASTER_ENCRYPTION_KEY','another-stable-secret-for-vapid-test');assert push.config()['public_key']!=first

def test_second_precise_reminder_reaches_push_without_an_open_page(database):
    _,clock=database;clock[0]+=23
    data,_,_=device();push.register(7,data)
    item=pulse.create(7,draft(schedule={'kind':'once','date':'2026-11-08','time':'09:01','second':23}))
    assert item['due_at']==clock[0]+60
    clock[0]+=59;pulse.tick();assert push.claim() is None
    clock[0]+=1;pulse.tick();notice=push.claim()
    assert notice and notice['device_id'] and notice['subscription']==data['subscription']
    assert push.wire(notice)[1]['Urgency']=='high'

@pytest.mark.parametrize('endpoint',[
    'http://fcm.googleapis.com/x','https://localhost/x','https://127.0.0.1/x','https://169.254.169.254/x',
    'https://fcm.googleapis.com.attacker.org/x','https://web.push.apple.com.attacker.org/x',
    'https://user:pass@fcm.googleapis.com/x','https://fcm.googleapis.com:8443/x',
    'https://fcm.googleapis.com/x#fragment','https://fcm.googleapis.com/x\nHeader: secret',
])
def test_arbitrary_endpoint_cannot_turn_push_into_ssrf(endpoint):
    data,_,_=device();data['subscription']['endpoint']=endpoint
    with pytest.raises(pulse.PulseError):push.subscription(data['subscription'])

def test_subscription_curve_keys_and_origin_validation():
    data,_,_=device();data['subscription']['keys']['p256dh']=push.b64(b'\4'+b'0'*64)
    with pytest.raises(pulse.PulseError):push.subscription(data['subscription'])
    for origin in ('http://app.example','https://app.example/path','https://app.example?x=1'):
        with pytest.raises(pulse.PulseError):push.valid_origin(origin)

def test_subscriptions_are_encrypted_owned_bounded_and_never_return_secrets(database):
    path,_=database;data,_,_=device();saved=push.register(7,data)
    assert set(saved)=={'device_id','binding_id'};assert push.register(7,data)==saved
    with pytest.raises(pulse.PulseError) as error:push.register(8,data)
    assert error.value.status==409
    with sqlite3.connect(path) as c:raw=c.execute('SELECT payload FROM velia_push_devices').fetchone()[0]
    assert data['subscription']['endpoint'] not in raw and data['subscription']['keys']['auth'] not in raw
    assert pulse.unpack(7,raw)['subscription']==data['subscription']
    with pytest.raises(Exception):pulse.unpack(8,raw)
    push.remove(8,saved['device_id'],saved['binding_id']);assert push.register(7,data)==saved
    for _ in range(19):push.register(7,device()[0])
    with pytest.raises(pulse.PulseError) as error:push.register(7,device()[0])
    assert error.value.code=='pulse_push_limit'

def test_encrypted_wire_payload_and_vapid_signature(database):
    _,clock=database;_,notice,private,auth,_=queued(clock)
    assert notice['payload']['body']=='' and notice['payload']['preview'] is False
    endpoint,headers,body=push.wire(notice)
    assert endpoint.startswith('https://fcm.googleapis.com/') and b'notification_id' not in body
    decoded=http_ece.decrypt(body,private_key=private,auth_secret=auth,version='aes128gcm')
    assert json.loads(decoded)==notice['payload']
    from py_vapid import Vapid
    assert Vapid.verify(headers['Authorization'])
    token=headers['Authorization'].split('t=',1)[1].split(',',1)[0]
    claims=json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'='*(-len(token.split('.')[1])%4)))
    assert claims['aud']=='https://fcm.googleapis.com' and claims['sub']==ORIGIN

def test_due_outbox_is_idempotent_receipt_is_owned_and_not_completion(database):
    path,clock=database;saved,notice,_,_,_=queued(clock);pulse.tick();assert push.claim() is None
    receipt={'delivery_id':notice['id'],**saved};push.receipt(8,receipt);assert status(path)==[('sending',)]
    push.receipt(7,receipt);push.result(notice,201);assert status(path)==[('displayed',)]
    assert pulse.listing(7)['items'][0]['state']=='active' and pulse.listing(7)['notifications'][0]['status']=='unread'

def test_completion_opt_out_and_rebinding_invalidate_pending_deliveries(database):
    path,clock=database;data,_,_=device();saved=push.register(7,data);item=pulse.create(7,draft());clock[0]+=3600;pulse.tick()
    pulse.action(7,item['item_id'],complete(pulse.listing(7)['items'][0]));assert push.claim() is None;assert status(path)==[('skipped',)]
    clock[0]-=3600;item=pulse.create(7,draft());clock[0]+=3600;pulse.tick()
    push.register(7,{**data,'binding_id':str(uuid.uuid4())});assert push.claim() is None
    push.remove(7,saved['device_id'],saved['binding_id']) # A late logout cannot remove a newer binding.
    with sqlite3.connect(path) as c:assert c.execute('SELECT COUNT(*) FROM velia_push_devices').fetchone()[0]==1

def test_no_backfill_before_opt_in_no_delivery_after_opt_out(database):
    path,clock=database;pulse.create(7,draft());clock[0]+=3600;pulse.tick();data,_,_=device();saved=push.register(7,data)
    assert push.claim() is None and not status(path)
    clock[0]-=3600;pulse.create(7,draft());clock[0]+=3600;pulse.tick();push.remove(7,saved['device_id'],saved['binding_id']);assert push.claim() is None

@pytest.mark.parametrize('retry_status',[429,503])
def test_expired_subscription_429_retry_and_ambiguous_send(database,retry_status):
    path,clock=database;saved,notice,_,_,_=queued(clock);push.result(notice,retry_status);assert status(path)==[('pending',)]
    assert push.claim() is None;clock[0]+=121;retry=push.claim();assert retry['attempts']==2
    push.result(retry,0);clock[0]+=1000;assert push.claim() is None;assert status(path)==[('unknown',)]
    with pulse.transaction() as c:c.execute("UPDATE velia_push_deliveries SET status='pending'")
    gone=push.claim();push.result(gone,410)
    with sqlite3.connect(path) as c:assert c.execute('SELECT COUNT(*) FROM velia_push_devices').fetchone()[0]==0

def test_quiet_hours_are_rechecked_and_preview_respects_current_preferences(database):
    _,clock=database;_,notice,_,_,_=queued(clock);push.result(notice,429)
    pulse.settings(7,{'quiet_start':'09:00','quiet_end':'11:00','preview':True});clock[0]+=121
    assert push.claim() is None;clock[0]=pulse.local_epoch('2026-11-08','11:00','UTC');notice=push.claim()
    assert notice['payload']['preview'] is True and '5000' in notice['payload']['body']

def test_routes_authentication_registration_and_deletion(database):
    async def run():
        module=SimpleNamespace(_mobile_api_available=lambda:True,_require_mobile_auth=lambda r:{'user_id':7} if r.headers.get('X-User') else None,_json_response=lambda data,status=200:web.json_response(data,status=status))
        app=web.Application();routes.setup_pulse_routes(app,module)
        async with TestServer(app) as server,ClientSession() as client:
            url=server.make_url('/mobile-api/v1/pulse/push/');headers={'X-User':'7'}
            assert (await client.get(str(url)+'config')).status==401
            config=await client.get(str(url)+'config',headers=headers);assert config.status==200 and config.headers['Cache-Control']=='no-store'
            data,_,_=device();response=await client.post(str(url)+'devices',json=data,headers=headers);assert response.status==200
            saved=await response.json();assert 'subscription' not in saved
            response=await client.delete(str(url)+'devices/'+saved['device_id'],json={'binding_id':saved['binding_id']},headers=headers);assert response.status==200
    asyncio.run(run())

def test_transport_uses_encrypted_body_no_redirects_bounded_timeout(database,monkeypatch):
    _,clock=database;_,notice,_,_,_=queued(clock)
    from db import database as db
    monkeypatch.setattr(db,'is_user_banned',lambda user:False)
    calls=[]
    class Response:
        status=307
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
    class Session:
        def post(self,url,**kwargs):calls.append((url,kwargs));return Response()
    asyncio.run(push.deliver(Session(),notice));_,request=calls[0]
    assert request['allow_redirects'] is False and request['timeout'].total==15
    assert isinstance(request['data'],bytes) and request['headers']['Content-Encoding']=='aes128gcm'
