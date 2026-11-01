import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';

if (process.platform !== 'linux' || process.arch !== 'x64') throw new Error('Railway Windows builder requires Linux x64');
const root = resolve(import.meta.dirname, '..');
const version = process.version;
const directory = join(root, '.runtime', 'windows-node');
await mkdir(directory, { recursive: true });
const archive = `node-${version}-win-x64.zip`;
async function download(name) {
  const response = await fetch(`https://nodejs.org/dist/${version}/${name}`,
    { redirect: 'error', signal: AbortSignal.timeout(180000) });
  if (!response.ok) throw new Error(`Windows Node download failed (${response.status})`);
  const bytes = Buffer.from(await response.arrayBuffer());
  if (bytes.length > 100 * 1024 * 1024) throw new Error('Windows Node archive exceeds its size limit');
  return bytes;
}
const checksums = (await download('SHASUMS256.txt')).toString();
const expected = checksums.split('\n').map(line => line.trim().split(/\s+/)).find(fields => fields[1] === archive)?.[0];
if (!expected || !/^[a-f0-9]{64}$/.test(expected)) throw new Error('Official Windows Node checksum missing');
const bytes = await download(archive);
if (createHash('sha256').update(bytes).digest('hex') !== expected) throw new Error('Windows Node archive checksum mismatch');
await writeFile(join(directory, archive), bytes);
const unpack = spawnSync('unzip', ['-q', '-o', join(directory, archive), '-d', directory], { stdio: 'inherit' });
if (unpack.error || unpack.status !== 0) throw unpack.error || new Error('Windows Node extraction failed');
const node = join(directory, `node-${version}-win-x64`, 'node.exe');
const executable = await readFile(node);
if (executable.toString('ascii', 0, 2) !== 'MZ') throw new Error('Windows Node archive contains no PE executable');
const prepare = spawnSync(process.execPath, [join(root, 'scripts', 'prepare-runtime.mjs')],
  { cwd: root, stdio: 'inherit', env: { ...process.env, VELIA_RUNTIME_PLATFORM: 'win32',
    VELIA_WINDOWS_NODE: node, VELIA_WINDOWS_NODE_SHA256: expected } });
if (prepare.error || prepare.status !== 0) throw prepare.error || new Error('Windows runtime preparation failed');
