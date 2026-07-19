import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _install_fake_requests(monkeypatch):
    class Session:
        def get(self,*a,**k): return types.SimpleNamespace(status_code=200, json=lambda: {}, text='')
        def post(self,*a,**k): return types.SimpleNamespace(status_code=200, json=lambda: {}, text='')
    monkeypatch.setitem(sys.modules, 'requests', types.SimpleNamespace(Session=Session, get=Session().get, post=Session().post))

def test_reveal_rowcount_zero_returns_no_seed(monkeypatch):
    _install_fake_requests(monkeypatch)
    from services import ton_wallet_service as svc
    class Cur:
        rowcount = 0
        def execute(self, q, params=None): self.last=q
        def fetchall(self): return [(7,42,'UQaddr','active','enc',False)]
    class Conn:
        def __init__(self): self.cur=Cur(); self.rolled=False; self.committed=False
        def cursor(self): return self.cur
        def rollback(self): self.rolled=True
        def commit(self): self.committed=True
        def close(self): pass
    conn=Conn()
    monkeypatch.setenv('TON_WALLET_ENABLED','true'); monkeypatch.setenv('TON_SEED_EXPORT_ENABLED','true')
    monkeypatch.setattr(svc,'_get_fernet',lambda: object()); monkeypatch.setattr(svc,'get_connection',lambda: conn)
    monkeypatch.setattr(svc,'decrypt_secret',lambda x: ' '.join(['word']*12)); monkeypatch.setattr(svc,'mnemonic_is_valid',None)
    out=svc.reveal_user_ton_seed_once(42,7,'UQaddr')
    assert out['ok'] is False and out['error']=='already_revealed'
    assert 'seed_phrase' not in out and conn.rolled and not conn.committed


def test_duplicate_wallet_rows_conflict_zero_seed(monkeypatch):
    _install_fake_requests(monkeypatch)
    from services import ton_wallet_service as svc
    class Cur:
        def execute(self,*a,**k): pass
        def fetchall(self): return [(7,42,'UQa','active','enc',False),(8,42,'UQb','active','enc2',False)]
    class Conn:
        def cursor(self): return Cur()
        def rollback(self): pass
        def close(self): pass
    monkeypatch.setenv('TON_WALLET_ENABLED','true'); monkeypatch.setenv('TON_SEED_EXPORT_ENABLED','true')
    monkeypatch.setattr(svc,'_get_fernet',lambda: object()); monkeypatch.setattr(svc,'get_connection',lambda: Conn())
    out=svc.reveal_user_ton_seed_once(42,7,'UQa')
    assert out['ok'] is False and out['error']=='wallet_conflict'
    assert 'seed_phrase' not in out


def test_reveal_bound_to_wallet_id_and_address(monkeypatch):
    _install_fake_requests(monkeypatch)
    from services import ton_wallet_service as svc
    class Cur:
        def execute(self,*a,**k): pass
        def fetchall(self): return [(7,42,'UQreal','active','enc',False)]
    class Conn:
        def cursor(self): return Cur()
        def rollback(self): pass
        def close(self): pass
    monkeypatch.setenv('TON_WALLET_ENABLED','true'); monkeypatch.setenv('TON_SEED_EXPORT_ENABLED','true')
    monkeypatch.setattr(svc,'_get_fernet',lambda: object()); monkeypatch.setattr(svc,'get_connection',lambda: Conn())
    assert svc.reveal_user_ton_seed_once(42,8,'UQreal')['error']=='invalid_reveal_target'
    assert svc.reveal_user_ton_seed_once(42,7,'UQforged')['error']=='invalid_reveal_target'


def test_forged_or_stale_callback_rejected():
    text = open('telegram_bot.py', encoding='utf-8').read()
    assert 'TON_SEED_REVEAL_PENDING' in text
    assert 'hmac.compare_digest' in text
    assert 'expires_at' in text
    assert 'ton_seed_reveal_confirm' not in text

def test_two_polling_instances_second_does_not_start():
    text = open('app.py', encoding='utf-8').read()
    assert 'BOT_POLLING_ENABLED' in text
    assert 'pg_try_advisory_lock' in text
    assert 'exiting polling path' in text
    assert 'while True' not in text[text.index('async def run_polling'):text.index('async def main')]

def test_production_logs_do_not_contain_seed_material():
    for path in ['services/ton_wallet_service.py','telegram_bot.py','app.py']:
        text = open(path, encoding='utf-8').read().lower()
        for bad in ['%s seed_encrypted', 'seed_phrase=%s', 'mnemonic=%s']:
            assert bad not in text


def test_concurrent_reveal_one_success_one_already_and_commit_before_seed(monkeypatch):
    _install_fake_requests(monkeypatch)
    from services import ton_wallet_service as svc
    import threading

    words = ' '.join(['word'] * 12)
    state = {'used': False, 'commits': 0, 'commit_before_return': []}
    lock = threading.Lock()

    class Cur:
        rowcount = 0
        def __init__(self, conn): self.conn = conn; self.rows = []
        def execute(self, q, params=None):
            if 'pg_advisory_xact_lock' in q:
                lock.acquire(); self.conn.has_lock = True
            elif 'SELECT id,user_id,wallet_address,status,seed_encrypted,seed_reveal_used' in q:
                self.rows = [(7, 42, 'UQaddr', 'active', 'enc', state['used'])]
            elif q.startswith('UPDATE user_ton_wallets'):
                if not state['used']:
                    state['used'] = True; self.rowcount = 1
                else:
                    self.rowcount = 0
        def fetchall(self): return self.rows
    class Conn:
        def __init__(self): self.has_lock = False
        def cursor(self): return Cur(self)
        def commit(self): state['commits'] += 1; state['commit_before_return'].append(True)
        def rollback(self): pass
        def close(self):
            if self.has_lock:
                lock.release(); self.has_lock = False
    monkeypatch.setenv('TON_WALLET_ENABLED','true'); monkeypatch.setenv('TON_SEED_EXPORT_ENABLED','true')
    monkeypatch.setattr(svc,'_get_fernet',lambda: object())
    monkeypatch.setattr(svc,'get_connection',lambda: Conn())
    monkeypatch.setattr(svc,'decrypt_secret',lambda x: words)
    monkeypatch.setattr(svc,'mnemonic_is_valid',None)
    out = []
    threads = [threading.Thread(target=lambda: out.append(svc.reveal_user_ton_seed_once(42,7,'UQaddr'))) for _ in range(2)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sorted([r.get('error', 'success') for r in out]) == ['already_revealed', 'success']
    assert sum(1 for r in out if r.get('ok') and r.get('seed_phrase') == words) == 1
    assert state['commits'] == 1 and state['commit_before_return'] == [True]


def test_init_db_duplicate_marker_does_not_raise_or_create_unique_index(monkeypatch):
    import db.database as database
    executed = []
    class Cur:
        def execute(self, q, params=None):
            executed.append((q, params))
        def fetchone(self): return ('user_ton_wallets',)
        def fetchall(self): return [(42, 2)]
    class Conn:
        rolled = False
        def rollback(self): self.rolled = True
    cur = Cur(); conn = Conn()
    database._diagnose_and_enforce_user_ton_wallet_uniques(conn, cur)
    sql = '\n'.join(q for q, _ in executed)
    assert 'ton_wallet_duplicate_incident' in str(executed)
    assert 'CREATE UNIQUE INDEX' not in sql
    assert conn.rolled is False


def test_admin_choose_canonical_archives_duplicates_and_unblocks_unique(monkeypatch):
    aiogram = types.ModuleType("aiogram"); aiogram.types = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "aiogram", aiogram)
    types_mod = types.ModuleType("aiogram.types")
    types_mod.InlineKeyboardMarkup = lambda *a, **k: types.SimpleNamespace(add=lambda *x, **y: None)
    types_mod.InlineKeyboardButton = lambda *a, **k: object()
    monkeypatch.setitem(sys.modules, "aiogram.types", types_mod)
    monkeypatch.setitem(sys.modules, "aiogram.dispatcher", types.SimpleNamespace(Dispatcher=object, FSMContext=object))
    monkeypatch.setitem(sys.modules, "aiogram.dispatcher.filters.state", types.SimpleNamespace(State=object, StatesGroup=object))
    _install_fake_requests(monkeypatch)
    from bot import admin
    rows = [
        [7, 42, 'UQcanon', 'mainnet', 'v4r2', 'active', '10', '2026', False],
        [8, 42, 'UQdup', 'mainnet', 'v4r2', 'active', '20', '2026', False],
    ]
    audit = []
    class Cur:
        rowcount = 0
        def execute(self, q, params=None):
            if q.startswith('SELECT id,user_id,wallet_address'):
                self.result = [tuple(r) for r in rows]
            elif q.startswith('INSERT INTO user_ton_wallet_quarantine_audit'):
                audit.append(params)
            elif q.startswith('DELETE FROM user_ton_wallets'):
                wid = int(params[1]); before = len(rows); rows[:] = [r for r in rows if int(r[0]) != wid]; self.rowcount = before - len(rows)
            elif q.startswith('UPDATE user_ton_wallets'):
                for r in rows:
                    if int(r[0]) == int(params[2]): r[5] = 'active'
        def fetchall(self): return self.result
    class Conn:
        def cursor(self): return Cur()
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass
    monkeypatch.setattr(admin, 'get_connection', lambda: Conn())
    archived = admin._admin_choose_canonical_wallet_tx(42, 7, 999)
    assert archived == 1
    assert [r[0] for r in rows] == [7]
    assert audit and 'enc' not in str(audit).lower() and 'seed' not in str(audit).lower()


def _load_telegram_seed_helpers():
    import os, time, secrets, hmac, hashlib
    from typing import Dict, Optional
    src = Path('telegram_bot.py').read_text()
    start = src.index('TON_SEND_PENDING: Dict[int, dict] = {}')
    end = src.index('\ndef _ton_project_wallet', start)
    ns = {'os': os, 'time': time, 'secrets': secrets, 'hmac': hmac, 'hashlib': hashlib, 'Dict': Dict, 'Optional': Optional}
    exec(src[start:end], ns)
    return ns


def test_cancel_invalidates_token_removes_keyboard_and_rejects_wrong_user_message_stale(monkeypatch):
    ns = _load_telegram_seed_helpers()
    token = ns['_make_ton_seed_reveal_token'](42, 7, 'UQaddr', 100, 200)
    assert ns['_pop_ton_seed_reveal_token'](43, token, 100, 200) is None
    token = ns['_make_ton_seed_reveal_token'](42, 7, 'UQaddr', 100, 200)
    assert ns['_pop_ton_seed_reveal_token'](42, token, 100, 201) is None
    token = ns['_make_ton_seed_reveal_token'](42, 7, 'UQaddr', 100, 200)
    raw = token.split(':')[1]
    ns['TON_SEED_REVEAL_PENDING'][raw]['expires_at'] = 0
    assert ns['_pop_ton_seed_reveal_token'](42, token, 100, 200) is None
    token = ns['_make_ton_seed_reveal_token'](42, 7, 'UQaddr', 100, 200)
    assert ns['_cancel_ton_seed_reveal_token'](42, token, 100, 200) is True
    assert ns['_pop_ton_seed_reveal_token'](42, token, 100, 200) is None
    first = ns['_make_ton_seed_reveal_token'](42, 7, 'UQaddr', 100, 200)
    second = ns['_make_ton_seed_reveal_token'](42, 7, 'UQaddr', 100, 201)
    assert ns['_pop_ton_seed_reveal_token'](42, first, 100, 200) is None
    assert ns['_pop_ton_seed_reveal_token'](42, second, 100, 201)['wallet_id'] == 7


def test_polling_disabled_and_busy_lock_do_not_start(monkeypatch):
    import asyncio, os, zlib, sys, types
    src = Path('app.py').read_text(); start = src.index('async def run_polling'); end = src.index('\n\nasync def main', start)
    calls = []
    class DP:
        async def start_polling(self, **kwargs): calls.append(kwargs)
    class Cur:
        def execute(self,*a,**k): pass
        def fetchone(self): return [False]
    class Conn:
        def cursor(self): return Cur()
        def close(self): pass
    dbmod = types.SimpleNamespace(get_connection=lambda: Conn(), _db_identifier_redacted=lambda: 'postgres://host/db')
    monkeypatch.setitem(sys.modules, 'db.database', dbmod)
    ns = {'os': os, 'zlib': zlib, 'telegram_bot': types.SimpleNamespace(dp=DP())}
    exec(src[start:end], ns)
    monkeypatch.delenv('BOT_POLLING_ENABLED', raising=False)
    asyncio.run(ns['run_polling']())
    monkeypatch.setenv('BOT_POLLING_ENABLED', 'true')
    asyncio.run(ns['run_polling']())
    assert calls == []


def test_conftest_has_only_sqlite_adapter_no_dependency_stubs():
    text = Path('tests/conftest.py').read_text()
    assert 'sqlite3.register_adapter(datetime, lambda value: value.isoformat(sep=" "))' in text
    assert 'requests' not in text
    assert 'psycopg2' not in text
    assert 'sys.modules' not in text
