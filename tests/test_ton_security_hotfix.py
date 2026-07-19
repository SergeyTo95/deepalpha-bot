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
