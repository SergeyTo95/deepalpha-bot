import asyncio, json
from pathlib import Path

class Response:
    def __init__(self,data,status=200): self.text=json.dumps(data); self.status=status
class Request:
    async def json(self): raise AssertionError('presence body must not be read')

def handler_namespace():
    source=Path('web.py').read_text(); start=source.index('async def handle_polywar_presence_api'); end=source.index('\ndef _polywar_read_error_response',start)
    ns={'asyncio':asyncio,'_json_response':lambda data,status=200:Response(data,status),'_polywar_unauthorized':lambda:Response({'ok':False},401)}
    exec(source[start:end],ns); return ns

def payload(response): return json.loads(response.text)

def test_presence_handler_auth_identity_and_no_body():
    ns=handler_namespace(); seen=[]; ns['_current_web_user']=lambda request:{'user_id':77}; ns['record_polywar_presence']=lambda uid:(seen.append(uid) or {'ok':True,'season_id':3,'presence_updated':True}); ns['_polywar_rate_limit']=lambda bucket,uid,limit:None
    response=asyncio.run(ns['handle_polywar_presence_api'](Request()))
    assert response.status==200 and seen==[77] and payload(response)['season_id']==3

def test_presence_handler_rejects_missing_and_invalid_identity():
    ns=handler_namespace(); ns['_current_web_user']=lambda request:None
    assert asyncio.run(ns['handle_polywar_presence_api'](Request())).status==401
    ns['_current_web_user']=lambda request:{'user_id':0}
    assert asyncio.run(ns['handle_polywar_presence_api'](Request())).status==401

def test_presence_handler_rate_limit_and_no_active_season():
    ns=handler_namespace(); ns['_current_web_user']=lambda request:{'user_id':88}; ns['_polywar_rate_limit']=lambda *a,**k:(_ for _ in ()).throw(ValueError('rate_limited'))
    limited=asyncio.run(ns['handle_polywar_presence_api'](Request())); assert limited.status==429 and payload(limited)['error']=='rate_limited'
    ns['_polywar_rate_limit']=lambda *a,**k:None; ns['record_polywar_presence']=lambda uid:(_ for _ in ()).throw(ValueError('no_active_season'))
    absent=asyncio.run(ns['handle_polywar_presence_api'](Request())); assert absent.status==404 and payload(absent)['error']=='no_active_season'
