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
        [7, 42, 'UQcanon', 'mainnet', 'v4r2', 'pub1', 'encrypted-seed-1', None, False, 'active', '2026', '2026', '10', '2026'],
        [8, 42, 'UQdup', 'mainnet', 'v4r2', 'pub2', 'encrypted-seed-2', None, False, 'active', '2026', '2026', '20', '2026'],
    ]
    audit = []
    class Cur:
        rowcount = 0
        def execute(self, q, params=None):
            if q.startswith('SELECT id,user_id,wallet_address'):
                self.result = [tuple(r) for r in rows]
            elif q.startswith('INSERT INTO user_ton_wallet_quarantine_archive'):
                audit.append(('archive', params)); self.rowcount = 1
            elif q.startswith('INSERT INTO user_ton_wallet_quarantine_audit'):
                audit.append(('audit', params)); self.rowcount = 1
            elif q.startswith('DELETE FROM user_ton_wallets'):
                wid = int(params[1]); before = len(rows); rows[:] = [r for r in rows if int(r[0]) != wid]; self.rowcount = before - len(rows)
            elif q.startswith('UPDATE user_ton_wallets'):
                for r in rows:
                    if int(r[0]) == int(params[2]): r[9] = 'active'
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
    archive_rows = [x for kind, x in audit if kind == 'archive']
    audit_rows = [x for kind, x in audit if kind == 'audit']
    assert archive_rows and archive_rows[0][6] == 'encrypted-seed-2'
    assert audit_rows and 'encrypted-seed-2' not in str(audit_rows).lower()


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


def _install_fake_admin(monkeypatch):
    aiogram = types.ModuleType("aiogram"); aiogram.types = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "aiogram", aiogram)
    types_mod = types.ModuleType("aiogram.types")
    types_mod.InlineKeyboardMarkup = lambda *a, **k: types.SimpleNamespace(buttons=[], add=lambda *x, **y: None)
    types_mod.InlineKeyboardButton = lambda *a, **k: object()
    monkeypatch.setitem(sys.modules, "aiogram.types", types_mod)
    monkeypatch.setitem(sys.modules, "aiogram.dispatcher", types.SimpleNamespace(Dispatcher=object, FSMContext=object))
    monkeypatch.setitem(sys.modules, "aiogram.dispatcher.filters.state", types.SimpleNamespace(State=object, StatesGroup=object))
    _install_fake_requests(monkeypatch)
    from bot import admin
    return admin


def test_quarantine_single_wallet_forbidden_and_archive_insert_failure_rolls_back(monkeypatch):
    admin = _install_fake_admin(monkeypatch)
    rows = [[7,42,'UQone','mainnet','v4r2','pub','encrypted-seed',None,False,'active','c','u','0','chk']]
    class CurSingle:
        rowcount = 0
        def execute(self, q, params=None):
            if q.startswith('SELECT id,user_id,wallet_address'): self.result = [tuple(r) for r in rows]
        def fetchall(self): return self.result
    class ConnSingle:
        rolled = committed = False
        def cursor(self): return CurSingle()
        def rollback(self): self.rolled = True
        def commit(self): self.committed = True
        def close(self): pass
    conn = ConnSingle(); monkeypatch.setattr(admin, 'get_connection', lambda: conn)
    assert admin._admin_quarantine_wallet_tx(42, 7, 999) == 0
    assert rows and conn.rolled and not conn.committed

    rows = [[7,42,'UQa','mainnet','v4r2','pub','encrypted-a',None,False,'active','c','u','0','chk'], [8,42,'UQb','mainnet','v4r2','pub','encrypted-b',None,False,'active','c','u','0','chk']]
    class CurFailArchive:
        rowcount = 0
        def execute(self, q, params=None):
            if q.startswith('SELECT id,user_id,wallet_address'): self.result = [tuple(r) for r in rows]
            elif q.startswith('INSERT INTO user_ton_wallet_quarantine_archive'): self.rowcount = 0
            elif q.startswith('DELETE FROM user_ton_wallets'): rows.clear()
        def fetchall(self): return self.result
    class ConnFail:
        rolled = False
        def cursor(self): return CurFailArchive()
        def rollback(self): self.rolled = True
        def commit(self): raise AssertionError('must not commit')
        def close(self): pass
    conn = ConnFail(); monkeypatch.setattr(admin, 'get_connection', lambda: conn)
    try:
        admin._admin_quarantine_wallet_tx(42, 8, 999)
    except RuntimeError as exc:
        assert str(exc) == 'wallet_archive_insert_failed'
    assert len(rows) == 2 and conn.rolled


def test_delete_rowcount_zero_rolls_back(monkeypatch):
    admin = _install_fake_admin(monkeypatch)
    rows = [[7,42,'UQa','mainnet','v4r2','pub','encrypted-a',None,False,'active','c','u','0','chk'], [8,42,'UQb','mainnet','v4r2','pub','encrypted-b',None,False,'active','c','u','0','chk']]
    class Cur:
        rowcount = 0
        def execute(self, q, params=None):
            if q.startswith('SELECT id,user_id,wallet_address'): self.result = [tuple(r) for r in rows]
            elif q.startswith('INSERT INTO user_ton_wallet_quarantine_archive'): self.rowcount = 1
            elif q.startswith('INSERT INTO user_ton_wallet_quarantine_audit'): self.rowcount = 1
            elif q.startswith('DELETE FROM user_ton_wallets'): self.rowcount = 0
        def fetchall(self): return self.result
    class Conn:
        rolled = False
        def cursor(self): return Cur()
        def rollback(self): self.rolled = True
        def commit(self): raise AssertionError('must not commit')
        def close(self): pass
    conn = Conn(); monkeypatch.setattr(admin, 'get_connection', lambda: conn)
    try:
        admin._admin_quarantine_wallet_tx(42, 8, 999)
    except RuntimeError as exc:
        assert str(exc) == 'wallet_delete_failed'
    assert len(rows) == 2 and conn.rolled


def test_duplicate_wallet_address_blocks_all_user_flows(monkeypatch):
    _install_fake_requests(monkeypatch)
    from services import ton_wallet_service as svc
    class Cur:
        def __init__(self): self.result = []
        def execute(self, q, params=None):
            if 'FROM user_ton_wallets' in q and 'WHERE user_id=%s' in q:
                uid = int(params[0]); self.result = [(uid,'UQshared','mainnet','v4r2','0',None,False,None,'active',None,'c',uid)]
            elif 'WHERE wallet_address=%s' in q:
                self.result = [(7,42,'UQshared','active'), (8,43,'UQshared','active')]
        def fetchall(self): return self.result
    class Conn:
        def cursor(self): return Cur()
        def close(self): pass
    monkeypatch.setattr(svc, 'get_connection', lambda: Conn())
    monkeypatch.setenv('TON_WALLET_ENABLED', 'true'); monkeypatch.setenv('TON_SEED_EXPORT_ENABLED', 'true')
    monkeypatch.setattr(svc, '_get_fernet', lambda: object())
    monkeypatch.setattr(svc, '_wallet_ready', lambda: True)
    monkeypatch.setattr(svc, 'get_setting', lambda key, default='': default)
    assert svc.get_user_ton_wallet(42)['error'] == 'wallet_conflict'
    assert svc.get_or_create_user_ton_wallet(42)['error'] == 'wallet_conflict'
    assert svc.send_ton_from_user_wallet(42, 'bad', 1)['error'] in {'wallet_conflict', 'invalid_address'}


def test_admin_wallet_address_view_and_cross_user_remediation(monkeypatch):
    admin = _install_fake_admin(monkeypatch)
    rows = [(7, 42, 'UQshared', 'active', '1', '2026', False), (8, 43, 'UQshared', 'inactive', '2', '2026', True)]
    text = admin._admin_gram_wallets_address_incident_text('UQshared', rows)
    assert 'wallet_id=7 user_id=42' in text and 'wallet_id=8 user_id=43' in text
    prod = [
        [7,42,'UQshared','mainnet','v4r2','pub1','encrypted-a',None,False,'inactive','c','u','1','chk'],
        [8,43,'UQshared','mainnet','v4r2','pub2','encrypted-b',None,True,'active','c','u','2','chk'],
    ]
    archive = [] ; executed = []
    class Cur:
        rowcount = 0
        def execute(self, q, params=None):
            executed.append(q)
            if q.startswith('SELECT wallet_address'):
                self.one = ('UQshared',)
            elif q.startswith('SELECT id,user_id,wallet_address'):
                self.result = [tuple(r) for r in prod]
            elif q.startswith('INSERT INTO user_ton_wallet_quarantine_archive'):
                archive.append(('archive', params)); self.rowcount = 1
            elif q.startswith('INSERT INTO user_ton_wallet_quarantine_audit'):
                archive.append(('audit', params)); self.rowcount = 1
            elif q.startswith('DELETE FROM user_ton_wallets'):
                wid = int(params[0]); before=len(prod); prod[:] = [r for r in prod if int(r[0]) != wid]; self.rowcount = before-len(prod)
            elif q.startswith("UPDATE user_ton_wallets SET status='active'"):
                for r in prod:
                    if int(r[0]) == int(params[1]): r[9] = 'active'
            elif 'GROUP BY' in q:
                self.result = []
        def fetchone(self): return self.one
        def fetchall(self): return self.result
    class Conn:
        def cursor(self): return Cur()
        def rollback(self): raise AssertionError('must not rollback')
        def commit(self): pass
        def close(self): pass
    monkeypatch.setattr(admin, 'get_connection', lambda: Conn())
    assert admin._admin_choose_wallet_address_owner_tx(7, 999) == 1
    assert prod == [[7,42,'UQshared','mainnet','v4r2','pub1','encrypted-a',None,False,'active','c','u','1','chk']]
    assert [x for kind, x in archive if kind == 'archive'][0][6] == 'encrypted-b'
    assert any('CREATE UNIQUE INDEX IF NOT EXISTS user_ton_wallets_user_id_unique' in q for q in executed)
    assert any('CREATE UNIQUE INDEX IF NOT EXISTS user_ton_wallets_wallet_address_unique' in q for q in executed)
    assert any('DELETE FROM settings WHERE key' in q for q in executed)


def test_address_conflict_blocks_balance_reveal_send_before_secret_or_network(monkeypatch):
    _install_fake_requests(monkeypatch)
    from services import ton_wallet_service as svc
    class Cur:
        def __init__(self): self.result = []
        def execute(self, q, params=None):
            if 'WHERE user_id=%s' in q:
                self.result = [(42,'UQshared','mainnet','v4r2','0',None,False,None,'active',None,'c',7)]
            elif 'WHERE wallet_address=%s' in q and 'id,user_id,wallet_address,status' in q:
                self.result = [(7,42,'UQshared','active'), (8,43,'UQshared','active')]
            elif 'SELECT id,user_id FROM user_ton_wallets WHERE wallet_address' in q:
                self.result = [(7,42),(8,43)]
            elif 'SELECT id,user_id,wallet_address,status,seed_encrypted,seed_reveal_used' in q:
                self.result = [(7,42,'UQshared','active','encrypted',False)]
        def fetchall(self): return self.result
    class Conn:
        def cursor(self): return Cur()
        def rollback(self): pass
        def close(self): pass
    monkeypatch.setattr(svc, 'get_connection', lambda: Conn())
    monkeypatch.setattr(svc, 'get_setting', lambda key, default='': default)
    monkeypatch.setenv('TON_WALLET_ENABLED','true'); monkeypatch.setenv('TON_SEED_EXPORT_ENABLED','true')
    monkeypatch.setattr(svc, '_get_fernet', lambda: object()); monkeypatch.setattr(svc, '_wallet_ready', lambda: True)
    monkeypatch.setattr(svc, 'validate_ton_address', lambda x: True); monkeypatch.setattr(svc, 'normalize_ton_address', lambda x: x)
    monkeypatch.setattr(svc, 'get_ton_balance', lambda *a, **k: (_ for _ in ()).throw(AssertionError('network called')))
    monkeypatch.setattr(svc, 'decrypt_secret', lambda *a, **k: (_ for _ in ()).throw(AssertionError('decrypt called')))
    assert svc.get_user_ton_balance(42, refresh=True)['error'] == 'wallet_conflict'
    assert svc.reveal_user_ton_seed_once(42, 7, 'UQshared')['error'] == 'wallet_conflict'
    assert svc.send_ton_from_user_wallet(42, 'UQdest', 1)['error'] == 'wallet_conflict'


def test_restore_archived_wallet_dry_run_conflict_and_success(monkeypatch):
    from scripts import restore_archived_ton_wallet as restore
    archive_row = (5, 8, 42, 'UQrestore', 'mainnet', 'v4r2', 'public-key-secret', 'encrypted-restore', None, False, 'active', 'c', 'u', '9', 'chk', 'archived')
    state = {'prod': [], 'archive_updated': False, 'audit': [], 'rolled': 0, 'committed': 0}
    class Cur:
        rowcount = 0
        def execute(self, q, params=None):
            if q.startswith('SELECT id,original_wallet_id'):
                self.one = archive_row
            elif q.startswith('SELECT id,user_id FROM user_ton_wallets'):
                self.result = list(state['prod'])
            elif q.startswith('INSERT INTO user_ton_wallets'):
                state['prod'].append(params); self.rowcount = 1
            elif q.startswith('INSERT INTO user_ton_wallet_quarantine_audit'):
                state['audit'].append(params); self.rowcount = 1
            elif q.startswith('UPDATE user_ton_wallet_quarantine_archive'):
                state['archive_updated'] = True; self.rowcount = 1
        def fetchone(self): return self.one
        def fetchall(self): return self.result
    class Conn:
        def cursor(self): return Cur()
        def rollback(self): state['rolled'] += 1
        def commit(self): state['committed'] += 1
        def close(self): pass
    monkeypatch.setattr(restore, 'get_connection', lambda: Conn())
    assert restore.restore_archive_record(5, 'tester', dry_run=True)['dry_run'] is True
    assert state['prod'] == [] and state['committed'] == 0
    state['prod'] = [(7,42)]
    assert restore.restore_archive_record(5, 'tester', dry_run=False)['error'] == 'production_conflict'
    assert state['committed'] == 0
    state['prod'] = []
    out = restore.restore_archive_record(5, 'tester', dry_run=False)
    assert out['ok'] is True and state['committed'] == 1 and state['archive_updated'] is True
    assert state['prod'][0][5] == 'encrypted-restore'
    assert 'encrypted-restore' not in str(state['audit']).lower()
