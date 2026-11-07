"""User-owned encrypted notebook snapshots with optimistic revisions."""
import asyncio
import base64
import json
import threading
import os
import hashlib
import hmac
from aiohttp import web
from db.database import get_connection

_READY=False
_LOCK=threading.Lock()


def ensure_schema():
    global _READY
    if _READY:return
    with _LOCK:
        if _READY:return
        connection=get_connection();cursor=connection.cursor()
        try:
            cursor.execute('CREATE TABLE IF NOT EXISTS velia_notebooks (user_id BIGINT PRIMARY KEY, revision BIGINT NOT NULL, record_json TEXT NOT NULL)')
            connection.commit();_READY=True
        except Exception:
            connection.rollback();raise
        finally:cursor.close();connection.close()


def read_notebook(user_id):
    ensure_schema();connection=get_connection();cursor=connection.cursor()
    try:
        cursor.execute('SELECT revision, record_json FROM velia_notebooks WHERE user_id=%s',(int(user_id),));row=cursor.fetchone()
        secret=os.getenv('VELIA_NOTEBOOK_SECRET') or os.getenv('MASTER_ENCRYPTION_KEY','')
        if len(secret)<24:raise RuntimeError('notebook_key_not_configured')
        key=hmac.new(secret.encode(),('velia-notebook-user-v1:'+str(user_id)).encode(),hashlib.sha256).hexdigest()
        return {'owner':str(user_id),'encryption_key':key,'revision':row[0] if row else 0,'record':json.loads(row[1]) if row else None}
    finally:cursor.close();connection.close()


def validate_record(record):
    if not isinstance(record,dict) or set(record)!={'iv','data','version'} or record.get('version')!=1:raise ValueError()
    if not all(isinstance(record.get(key),str) for key in ('iv','data')) or len(record['data'])>700000:raise ValueError()
    iv=base64.b64decode(record['iv'],validate=True);cipher=base64.b64decode(record['data'],validate=True)
    if len(iv)!=12 or not 16<=len(cipher)<=524304:raise ValueError()
    return record


def write_notebook(user_id,revision,record):
    ensure_schema();record=validate_record(record)
    if isinstance(revision,bool) or not isinstance(revision,int) or not 0<=revision<=2000000000:raise ValueError()
    connection=get_connection();cursor=connection.cursor()
    try:
        value=json.dumps(record,separators=(',',':'))
        if revision==0:
            cursor.execute('INSERT INTO velia_notebooks (user_id,revision,record_json) VALUES (%s,1,%s) ON CONFLICT (user_id) DO NOTHING RETURNING revision',(int(user_id),value))
        else:
            cursor.execute('UPDATE velia_notebooks SET record_json=%s,revision=revision+1 WHERE user_id=%s AND revision=%s RETURNING revision',(value,int(user_id),revision))
        row=cursor.fetchone()
        if not row:connection.rollback();return None
        connection.commit();return row[0]
    except Exception:connection.rollback();raise
    finally:cursor.close();connection.close()


def setup_notebook_routes(app,routes_module):
    slots=asyncio.Semaphore(8)
    async def handle(request):
        if not routes_module._mobile_api_available():return routes_module._disabled_response()
        auth=routes_module._require_mobile_auth(request)
        if not auth:return routes_module._json_response({'ok':False,'error':'unauthorized'},status=401)
        if slots.locked():return routes_module._json_response({'ok':False,'error':'notebook_busy'},status=429)
        try:
            async with slots:
                user=auth['user_id']
                if request.method=='GET':result=await asyncio.to_thread(read_notebook,user)
                else:
                    if request.content_length and request.content_length>710000:raise ValueError()
                    body=await request.json()
                    if not isinstance(body,dict) or str(body.get('owner'))!=str(user):
                        return routes_module._json_response({'ok':False,'error':'notebook_owner_changed'},status=403)
                    revision=await asyncio.to_thread(write_notebook,user,body.get('revision'),body.get('record'))
                    if revision is None:return routes_module._json_response({'ok':False,'error':'notebook_revision_conflict'},status=409)
                    result={'owner':str(user),'revision':revision}
            response=routes_module._json_response({'ok':True,**result});response.headers['Cache-Control']='no-store';return response
        except (ValueError,TypeError):return routes_module._json_response({'ok':False,'error':'invalid_notebook'},status=400)
        except Exception:return routes_module._json_response({'ok':False,'error':'notebook_unavailable'},status=503)
    app.router.add_get('/mobile-api/v1/notebook',handle)
    app.router.add_put('/mobile-api/v1/notebook',handle)
