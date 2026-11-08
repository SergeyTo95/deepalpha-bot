import asyncio
import base64
import json
import sqlite3
from types import SimpleNamespace
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
import pytest
from services import velia_notebook_materials as materials

MID='11111111-1111-4111-8111-111111111111'
MID2='22222222-2222-4222-8222-222222222222'
def record(data=b'encrypted-original-bytes'):
    return {'version':1,'iv':base64.b64encode(b'0'*12).decode(),'data':base64.b64encode(data).decode()}

@pytest.fixture
def database(monkeypatch,tmp_path):
    path=str(tmp_path/'materials.sqlite')
    class Cursor:
        def __init__(self,value):self.value=value
        def execute(self,sql,params=()):return self.value.execute(sql.replace('%s','?').replace(' FOR UPDATE',''),params)
        def fetchone(self):return self.value.fetchone()
        def close(self):self.value.close()
    class Connection:
        def __init__(self):self.value=sqlite3.connect(path)
        def cursor(self):return Cursor(self.value.cursor())
        def commit(self):self.value.commit()
        def rollback(self):self.value.rollback()
        def close(self):self.value.close()
    monkeypatch.setattr(materials,'get_connection',Connection);monkeypatch.setattr(materials,'_READY',False)
    return path

def test_materials_are_immutable_user_owned_and_deletable(database):
    assert materials.material_operation(7,MID,'PUT',record())['saved']
    assert materials.material_operation(7,MID,'PUT',record())['saved']
    with pytest.raises(materials.MaterialError) as error:materials.material_operation(7,MID,'PUT',record(b'different-private-ciphertext'))
    assert error.value.status==409
    with pytest.raises(materials.MaterialError) as error:materials.material_operation(8,MID,'GET')
    assert error.value.status==404
    materials.material_operation(8,MID,'DELETE')
    assert materials.material_operation(7,MID,'GET')['record']==record()
    materials.material_operation(7,MID,'DELETE')
    materials.material_operation(7,MID,'DELETE')
    with pytest.raises(materials.MaterialError):materials.material_operation(7,MID,'GET')

def test_quota_is_counted_per_user_and_released_on_delete(database,monkeypatch):
    monkeypatch.setattr(materials,'MAX_TOTAL',len(b'encrypted-original-bytes')+1)
    materials.material_operation(7,MID,'PUT',record())
    with pytest.raises(materials.MaterialError) as error:materials.material_operation(7,MID2,'PUT',record())
    assert error.value.code=='notebook_material_quota'
    materials.material_operation(8,MID2,'PUT',record())
    materials.material_operation(7,MID,'DELETE');materials.material_operation(7,MID2,'PUT',record())

def test_cipher_and_identifiers_are_strictly_bounded():
    for value in [{**record(),'iv':'bad'},{**record(),'version':True},{**record(),'data':'not base64!'},{**record(),'data':base64.b64encode(b'x'*16).decode()}]:
        with pytest.raises(ValueError):materials.validate_record(value)
    with pytest.raises(ValueError):materials.material_operation(7,'../foreign','GET')

def test_routes_auth_owner_large_body_and_idempotent_cleanup(database):
    async def scenario():
        module=SimpleNamespace(_mobile_api_available=lambda:True,_require_mobile_auth=lambda r:{'user_id':int(r.headers['X-Account'])} if r.headers.get('X-Account') else None,_json_response=lambda d,status=200:web.json_response(d,status=status))
        app=web.Application(client_max_size=16384);materials.setup_material_routes(app,module)
        async with TestServer(app) as server,ClientSession() as client:
            url=server.make_url('/mobile-api/v1/notebook/materials/'+MID)
            assert (await client.get(url)).status==401
            assert (await client.put(url,json={'owner':'8','record':record()},headers={'X-Account':'7'})).status==403
            payload=record(b'x'*100000)
            assert (await client.put(url,json={'owner':'7','record':payload},headers={'X-Account':'7'})).status==200
            assert (await client.get(url,headers={'X-Account':'8'})).status==404
            result=await client.get(url,headers={'X-Account':'7'});assert result.headers['Cache-Control']=='no-store';assert (await result.json())['record']==payload
            assert (await client.delete(url,json={'owner':'7'},headers={'X-Account':'7'})).status==200
            assert (await client.get(url,headers={'X-Account':'7'})).status==404
    asyncio.run(scenario())
