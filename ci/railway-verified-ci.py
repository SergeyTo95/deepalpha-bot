"""Run the reviewed production checks in isolated Railway build stages."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RECEIPTS = Path('/opt/velia-ci')


def source_hash():
    names = json.loads((ROOT / 'ci/railway-source-files.json').read_text())
    if not names or names != sorted(set(names)):
        raise RuntimeError('Source inventory must be nonempty, sorted and unique')
    digest = hashlib.sha256()
    for name in names:
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise RuntimeError('Source path escapes checkout')
        path = ROOT / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f'missing or unsafe source: {name}')
        digest.update(name.encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def commit_sha(value):
    if not re.fullmatch('[0-9a-f]{40}', value or ''):
        raise RuntimeError('Railway source commit is required')
    return value


def clean_environment():
    # Never forward deployment credentials, production DB URLs, provider keys,
    # feature flags, or proxy settings into the test processes.
    return {
        'PATH': '/opt/tests/bin:/usr/lib/postgresql/16/bin:/usr/local/bin:/usr/bin:/bin',
        'HOME': '/tmp/velia-ci-home', 'LANG': 'C.UTF-8',
        'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONPATH': str(ROOT), 'CI': 'true',
    }


def run(command, env, timeout=900):
    subprocess.run(['/bin/bash', '-euo', 'pipefail', '-c', command],
                   cwd=ROOT, env=env, timeout=timeout, check=True)


def write_receipt(group, sha, digest, results):
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    result = dict(group=group, commit=sha, source_sha256=digest,
                  results=results, passed=True)
    (RECEIPTS / f'{group}.json').write_text(json.dumps(result, sort_keys=True))
    print('RAILWAY_CI_PASSED ' + json.dumps(result), flush=True)


def test_group(group, sha):
    import yaml
    manifest = json.loads((ROOT / 'ci/railway-ci-manifest.json').read_text())
    active = set()
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        workflow = yaml.safe_load(path.read_text())
        triggers = workflow.get('on', workflow.get(True, {}))
        push = triggers.get('push') if isinstance(triggers, dict) else None
        if isinstance(push, dict) and 'feature/turbo-short-term-btc' in push.get('branches', []):
            active.add(path.name)
    if active != set(manifest):
        raise RuntimeError('Production workflow inventory changed; review Railway CI mapping')
    for name, spec in manifest.items():
        raw = (ROOT / '.github/workflows' / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != spec['sha256']:
            raise RuntimeError(f'Workflow changed without CI migration review: {name}')
    subprocess.run([sys.executable, str(ROOT / 'ci/render-verified-dockerfile.py'), '--check'],
                   check=True, env=clean_environment())
    digest = source_hash()
    env = clean_environment()
    Path(env['HOME']).mkdir(exist_ok=True)
    if group == 'backend':
        run('python -m pytest -q -p no:cacheprovider tests/test_railway_verified_ci.py', env)
    if os.geteuid() == 0:
        raise RuntimeError('PostgreSQL validation must run as an unprivileged build user')
    data = tempfile.mkdtemp(prefix='velia-ci-pg-')
    run(f'initdb -D {data} -A trust --no-locale > {data}/../velia-initdb.log', env)
    started = False
    results = []
    try:
        run(f'pg_ctl -D {data} -l {data}/server.log -o "-h 127.0.0.1 -p 55432 -k /tmp" -w start', env)
        started = True
        for number, (name, spec) in enumerate(manifest.items()):
            if spec['group'] != group:
                continue
            workflow = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
            job, = workflow['jobs'].values()
            expected = '3.12' if group == 'flash' else '3.11'
            if f'{sys.version_info.major}.{sys.version_info.minor}' != expected:
                raise RuntimeError(f'Expected Python {expected}')
            db_name = f'ci_{number}'
            run(f'createdb -h 127.0.0.1 -p 55432 {db_name}', env)
            job_env = dict(env)
            for key, value in job.get('env', {}).items():
                job_env[key] = str(value)
            dsn = f'postgresql://postgres@127.0.0.1:55432/{db_name}'
            job_env.update(TEST_DATABASE_URL=dsn, VELIA_FLASH_TEST_DATABASE_URL=dsn,
                           DATABASE_URL=dsn)
            count = 0
            for index in spec['steps']:
                step = job['steps'][index]
                step_env = dict(job_env)
                for key, value in step.get('env', {}).items():
                    step_env[key] = str(value).replace('${{ github.workspace }}', str(ROOT))
                command = step['run']
                if '${{' in command:
                    raise RuntimeError('Unresolved GitHub expression in test command')
                xml_path = Path(f'/tmp/velia-ci-{number}-{index}.xml')
                step_env['PYTEST_ADDOPTS'] = f'-p no:cacheprovider --junitxml={xml_path}'
                print(f'RAILWAY_CI_STEP {name}: {step.get("name", index)}', flush=True)
                run(command, step_env)
                if xml_path.exists():
                    suites = ET.parse(xml_path).getroot()
                    for suite in suites.iter('testsuite'):
                        if any(int(suite.get(k, '0')) for k in ('failures', 'errors', 'skipped')):
                            raise RuntimeError('Failed, erroneous, or skipped CI tests')
                        count += int(suite.get('tests', '0'))
            if count == 0:
                raise RuntimeError(f'No tests executed for {name}')
            results.append(dict(workflow=name, tests=count))
    finally:
        if started:
            run(f'pg_ctl -D {data} -m immediate -w stop', env, timeout=60)
    write_receipt(group, sha, digest, results)


def medical_smoke(sha):
    import time
    import urllib.error
    import urllib.request
    env = clean_environment()
    # Preserve only the fixed image paths, never deployment credentials.
    env.update(PATH='/opt/conda/bin:/usr/local/bin:/usr/bin:/bin',
               VELIA_MEDICAL_WORKER_AUTH_TOKEN='ci-secret',
               VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK='false',
               RADAR_UPSTREAM_ROOT='/opt/damo-radar', RADAR_MODEL_ROOT='/models/radar',
               VELIA_MEDICAL_WORK_DIR='/var/lib/velia-medical', PORT='8088')
    proc = subprocess.Popen([sys.executable, 'app.py'], cwd=ROOT / 'medical_worker', env=env)
    try:
        data = None
        for _ in range(30):
            if proc.poll() is not None:
                raise RuntimeError('Medical worker exited before health check')
            try:
                request = urllib.request.Request('http://127.0.0.1:8088/health',
                                                  headers={'Authorization': 'Bearer ci-secret'})
                with urllib.request.urlopen(request, timeout=2) as response:
                    data = json.load(response)
                break
            except urllib.error.URLError:
                time.sleep(1)
        if not (data and data.get('ok') is True and data.get('provider') == 'radar'
                and data.get('ready_for_inference') is False
                and data.get('commercial_use_allowed_by_public_weights') is False
                and data.get('raw_input_retained_after_job') is False):
            raise RuntimeError('Medical fail-closed health check failed')
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    write_receipt('medical-image', sha, source_hash(), [dict(health=data)])


def verify(sha):
    digest = source_hash()
    for group in ('backend', 'flash', 'medical-image'):
        receipt = json.loads((RECEIPTS / f'{group}.json').read_text())
        if (receipt.get('passed') is not True or receipt.get('group') != group
                or receipt.get('commit') != sha or receipt.get('source_sha256') != digest):
            raise RuntimeError(f'Invalid CI receipt for {group}')
    print(f'RAILWAY_CI_VERIFIED commit={sha} source_sha256={digest}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['backend', 'flash', 'medical-image', 'verify'])
    parser.add_argument('--commit', default=os.getenv('RAILWAY_GIT_COMMIT_SHA', ''))
    args = parser.parse_args()
    sha = commit_sha(args.commit)
    if args.command in ('backend', 'flash'):
        test_group(args.command, sha)
    elif args.command == 'medical-image':
        medical_smoke(sha)
    else:
        verify(sha)
