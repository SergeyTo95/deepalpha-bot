import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { resolve, join } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const pkg = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'));
const archive = join(root, 'dist', `VELIA-Desktop-${pkg.version}-win-x64.zip`);
const staging = await mkdtemp(join(root, 'dist', '.portable-'));
const app = join(staging, 'app');
function run(command, args, extraEnv = {}) {
  const child = spawnSync(command, args, { cwd: root, stdio: 'inherit', timeout: 180_000,
    env: { ...process.env, ...extraEnv } });
  if (child.error || child.status !== 0) throw child.error || new Error(`Portable qualification failed: ${command} (${child.status})`);
}
try {
  run('python3', ['-c', `
from hashlib import sha256
from pathlib import Path
import re, sys, zipfile
source, archive, extracted = map(Path, sys.argv[1:])
entries = sorted(source.rglob('*'))
if any(p.is_symlink() for p in entries):
    raise ValueError('Portable Windows archive must contain materialized files')
names = set()
for path in entries:
    relative = path.relative_to(source)
    for part in relative.parts:
        if re.search(r'[<>:"|?*]', part) or part.endswith((' ', '.')) or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:[.].*)?', part):
            raise ValueError('Invalid Windows archive path: ' + str(relative))
    folded = str(relative).casefold()
    if folded in names:
        raise ValueError('Case-insensitive archive path collision: ' + str(relative))
    names.add(folded)
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as output:
    for path in entries:
        output.write(path, path.relative_to(source).as_posix())
with zipfile.ZipFile(archive) as packed:
    if packed.testzip() is not None:
        raise ValueError('Portable archive CRC verification failed')
    packed.extractall(extracted)
files = 0
for expected in entries:
    actual = extracted / expected.relative_to(source)
    if expected.is_dir():
        assert actual.is_dir(), 'Missing extracted directory'
    else:
        assert sha256(expected.read_bytes()).digest() == sha256(actual.read_bytes()).digest(), 'Changed extracted file'
        files += 1
print('VELIA_PORTABLE_FILES_QUALIFIED', files, flush=True)
`, join(root, 'dist', 'win-unpacked'), archive, app]);
  for (const script of ['smoke-windows-deps.mjs', 'smoke-harness.mjs', 'smoke-web.mjs']) {
    run(process.execPath, [join(root, 'scripts', script)], { VELIA_QUALIFICATION_RESOURCES: join(app, 'resources') });
  }
  console.log('VELIA_WINDOWS_PORTABLE_QUALIFIED', JSON.stringify({
    extractedRuntimeUnderWine: true, extractedReadTool: true, extractedWeb: true,
  }));
} finally {
  await rm(staging, { recursive: true, force: true });
}
