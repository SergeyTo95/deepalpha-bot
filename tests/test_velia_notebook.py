import asyncio
import base64
import json
import sqlite3
from types import SimpleNamespace
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
import pytest
from services import velia_notebook_routes as notes


@pytest.fixture
def database(monkeypatch,tmp_path):
    path=str(tmp_path/'notes.sqlite')
    class Cursor:
        def __init__(self,value):self.value=value
        def execute(self,sql,params=()):return self.value.execute(sql.replace('%s','?'),params)
        def fetchone(self):return self.value.fetchone()
        def close(self):self.value.close()
    class Connection:
        def __init__(self):self.value=sqlite3.connect(path)
        def cursor(self):return Cursor(self.value.cursor())
        def commit(self):self.value.commit()
        def rollback(self):self.value.rollback()
        def close(self):self.value.close()
    monkeypatch.setattr(notes,'get_connection',Connection);monkeypatch.setattr(notes,'_READY',False)
    monkeypatch.setenv('MASTER_ENCRYPTION_KEY','operator-master-key-must-stay-stable')
    return path


def record(value=b'ciphertext123456789'):
    return {'iv':base64.b64encode(b'0'*12).decode(),'data':base64.b64encode(value).decode(),'version':1}


def test_snapshots_are_user_owned_and_compare_revisions(database):
    first=notes.read_notebook(7);assert first['revision']==0 and first['record'] is None
    assert notes.write_notebook(7,0,record())==1
    assert notes.write_notebook(7,0,record(b'different-ciphertext')) is None
    assert notes.read_notebook(7)['record']==record()
    assert notes.read_notebook(8)['record'] is None
    assert notes.write_notebook(8,1,record()) is None
    assert notes.write_notebook(7,1,record(b'different-ciphertext'))==2
    assert notes.read_notebook(7)['encryption_key']!=notes.read_notebook(8)['encryption_key']
    with sqlite3.connect(database) as connection:
        stored=connection.execute('SELECT record_json FROM velia_notebooks WHERE user_id=7').fetchone()[0]
    assert 'encryption_key' not in stored and first['encryption_key'] not in stored


def test_routes_require_auth_and_reject_another_owner(database):
    async def scenario():
        module=SimpleNamespace(_mobile_api_available=lambda:True,_require_mobile_auth=lambda request:{'user_id':7} if request.headers.get('X-Account') else None,
            _json_response=lambda result,status=200:web.json_response(result,status=status))
        app=web.Application();notes.setup_notebook_routes(app,module)
        async with TestServer(app) as server,ClientSession() as client:
            url=server.make_url('/mobile-api/v1/notebook')
            assert (await client.get(url)).status==401
            assert (await client.put(url,json={'owner':'8','revision':0,'record':record()},headers={'X-Account':'7'})).status==403
            response=await client.put(url,json={'owner':'7','revision':0,'record':record()},headers={'X-Account':'7'})
            assert response.status==200 and (await response.json())['revision']==1
            assert (await client.put(url,json={'owner':'7','revision':0,'record':record()},headers={'X-Account':'7'})).status==409
            response=await client.get(url,headers={'X-Account':'7'});result=await response.json()
            assert result['owner']=='7' and response.headers['Cache-Control']=='no-store'
    asyncio.run(scenario())


def test_record_size_and_nonce_are_bounded():
    with pytest.raises(ValueError):notes.validate_record({**record(),'iv':'bad'})
    with pytest.raises(ValueError):notes.validate_record({**record(),'data':'A'*700001})
