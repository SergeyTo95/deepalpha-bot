import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

if 'requests' not in sys.modules:
    class _Response:
        status_code = 200
        text = ''
        def json(self): return {}
        def raise_for_status(self): return None
    class _Session:
        def __init__(self): self.headers = {}
        def get(self, *args, **kwargs): return _Response()
        def post(self, *args, **kwargs): return _Response()
    sys.modules['requests'] = types.SimpleNamespace(Session=_Session, get=_Session().get, post=_Session().post)

if 'psycopg2' not in sys.modules:
    extras_mod = types.SimpleNamespace(RealDictCursor=object)
    fake = types.SimpleNamespace(extras=extras_mod, errors=types.SimpleNamespace(), connect=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('psycopg2 is not installed')))
    sys.modules['psycopg2'] = fake
    sys.modules['psycopg2.extras'] = extras_mod
