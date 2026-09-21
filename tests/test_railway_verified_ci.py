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
