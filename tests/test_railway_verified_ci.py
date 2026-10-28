"""Deployment receipts must fail closed on stale or incomplete validation."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    'railway_verified_ci', Path(__file__).resolve().parents[1] / 'ci/railway-verified-ci.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    (root / 'ci').mkdir(parents=True)
    (root / 'app.py').write_text('print("tested source")\n')
    (root / 'ci/railway-source-files.json').write_text(json.dumps(['app.py']))
    monkeypatch.setattr(ci, 'ROOT', root)
    monkeypatch.setattr(ci, 'RECEIPTS', tmp_path / 'receipts')
    return root


def receipts(sha='a' * 40):
    digest = ci.source_hash()
    for group in ('backend', 'flash', 'medical-image'):
        ci.write_receipt(group, sha, digest, [{'tests': 1}])
    return sha


def test_all_receipts_match_checkout(checkout):
    ci.verify(receipts())


def test_source_change_rejects_old_receipts(checkout):
    sha = receipts()
    (checkout / 'app.py').write_text('print("untested code")')
    with pytest.raises(RuntimeError, match='Invalid CI receipt'):
        ci.verify(sha)


def test_another_commit_cannot_reuse_receipts(checkout):
    receipts()
    with pytest.raises(RuntimeError, match='Invalid CI receipt'):
        ci.verify('b' * 40)


@pytest.mark.parametrize('group', ['backend', 'flash', 'medical-image'])
def test_missing_stage_blocks_deploy(checkout, group):
    sha = receipts()
    (ci.RECEIPTS / f'{group}.json').unlink()
    with pytest.raises(FileNotFoundError):
        ci.verify(sha)


def test_failed_stage_blocks_deploy(checkout):
    sha = receipts()
    path = ci.RECEIPTS / 'flash.json'
    data = json.loads(path.read_text())
    data['passed'] = False
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match='Invalid CI receipt'):
        ci.verify(sha)


def test_tests_do_not_inherit_production_credentials(monkeypatch):
    for key in ('DATABASE_URL', 'BOT_TOKEN', 'VELIA_FLASH_API_KEY', 'HTTP_PROXY'):
        monkeypatch.setenv(key, 'production-credential')
    environment = ci.clean_environment()
    assert not set(environment).intersection({'DATABASE_URL', 'BOT_TOKEN', 'VELIA_FLASH_API_KEY', 'HTTP_PROXY'})


@pytest.mark.parametrize('value', ['', 'main', 'a' * 39, 'g' * 40])
def test_source_commit_is_required(value):
    with pytest.raises(RuntimeError, match='source commit'):
        ci.commit_sha(value)


def test_source_cannot_escape_checkout(checkout):
    (checkout / 'ci/railway-source-files.json').write_text(json.dumps(['../secret']))
    with pytest.raises(RuntimeError, match='escapes checkout'):
        ci.source_hash()


def test_medical_loopback_health_ignores_outbound_proxy_and_waits_for_startup(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from types import SimpleNamespace
    import threading
    requests = []
    class Health(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            requests.append(self.headers.get('Authorization'))
            if len(requests) == 1:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok":true,"provider":"radar"}')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    try:
        result = ci.wait_for_medical_health(SimpleNamespace(poll=lambda: None),
            f'http://127.0.0.1:{server.server_port}/health', timeout=3)
        assert result == {'ok':True,'provider':'radar'}
        assert requests == ['Bearer ci-secret', 'Bearer ci-secret']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_medical_health_detects_worker_exit_and_enforces_deadline():
    from types import SimpleNamespace
    with pytest.raises(RuntimeError, match='exited'):
        ci.wait_for_medical_health(SimpleNamespace(poll=lambda: 1), 'http://127.0.0.1:1/health')
    with pytest.raises(RuntimeError, match='startup timed out'):
        ci.wait_for_medical_health(SimpleNamespace(poll=lambda: None), 'http://127.0.0.1:1/health', timeout=0.03)


@pytest.mark.parametrize('changed', [{'ok':False}, {'provider':'other'}, {'ready_for_inference':True},
    {'commercial_use_allowed_by_public_weights':True}, {'raw_input_retained_after_job':True}])
def test_medical_startup_resilience_never_weakens_fail_closed_contract(monkeypatch, changed):
    from types import SimpleNamespace
    stopped, receipts = [], []
    process = SimpleNamespace(terminate=lambda: stopped.append(True), wait=lambda **_kwargs: 0)
    monkeypatch.setattr(ci.subprocess, 'Popen', lambda *_a, **_k: process)
    health = {'ok':True, 'provider':'radar', 'ready_for_inference':False,
        'commercial_use_allowed_by_public_weights':False, 'raw_input_retained_after_job':False, **changed}
    monkeypatch.setattr(ci, 'wait_for_medical_health', lambda *_a, **_k: health)
    monkeypatch.setattr(ci, 'write_receipt', lambda *a: receipts.append(a))
    with pytest.raises(RuntimeError, match='fail-closed'):
        ci.medical_smoke('a'*40)
    assert stopped == [True] and not receipts
