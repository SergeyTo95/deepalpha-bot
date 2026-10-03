import { spawnSync } from 'node:child_process';
import { mkdir, mkdtemp, rm, copyFile, chmod, writeFile, rename } from 'node:fs/promises';
import { resolve, join, dirname } from 'node:path';
import { HARNESS_COMMIT } from '../src/config.mjs';
import { runtimeClosure } from './runtime-closure.mjs';

const root = resolve(import.meta.dirname, '..');
const checkout = join(root, '.runtime', 'harness');
const published = join(root, '.runtime', 'package');
const destination = await mkdtemp(join(root, '.runtime', '.package-'));
const deployed = join(destination, 'harness');
// Build and deploy on the target OS/architecture: native addons are not portable.
const closure = runtimeClosure(checkout);
try {
  const result = spawnSync(process.execPath, [join(root, 'node_modules', 'pnpm', 'bin', 'pnpm.cjs'),
    '--filter', '@deepseek-ai/dsh', '--prod', '--config.node-linker=hoisted',
    '--config.allow-unused-patches=true', 'deploy', '--legacy', deployed],
  { cwd: checkout, stdio: 'inherit' });
  if (result.error || result.status !== 0) throw result.error || new Error('Harness runtime deployment failed');
} finally { closure.restore(); }
await mkdir(join(destination, 'node'), { recursive: true });
const node = join(destination, 'node', process.platform === 'win32' ? 'node.exe' : 'node');
await copyFile(process.execPath, node);
if (process.platform !== 'win32') await chmod(node, 0o755);
await mkdir(join(destination, 'notices'), { recursive: true });
for (const name of ['LICENSE', 'THIRD_PARTY_NOTICES.md', 'VELIA-UPSTREAM.json']) {
  await copyFile(join(checkout, name), join(destination, 'notices', name));
}
const nodeLicense = join(dirname(process.execPath), process.platform === 'win32' ? 'LICENSE' : '../LICENSE');
try { await copyFile(nodeLicense, join(destination, 'notices', 'NODE-LICENSE.txt')); }
catch (error) {
  if (error.code !== 'ENOENT') throw error;
  const response = await fetch(`https://raw.githubusercontent.com/nodejs/node/${process.version}/LICENSE`,
    { redirect: 'error', signal: AbortSignal.timeout(20000) });
  if (!response.ok) throw new Error('Node.js license download failed');
  await writeFile(join(destination, 'notices', 'NODE-LICENSE.txt'), await response.text());
}
await writeFile(join(destination, 'notices', 'BUILD.json'), JSON.stringify({ harness: HARNESS_COMMIT,
  node: process.version, platform: process.platform, arch: process.arch, workspacePeers: closure.added,
  dependencyLayout: 'hoisted', localOverrides: 'file' }, null, 2) + '\n');
const probe = spawnSync(node, [join(deployed, 'lib', 'bin.js'), '--version'], { encoding: 'utf8' });
if (probe.error || probe.status !== 0) throw probe.error || new Error('Bundled runtime cannot start');
await rm(published, { recursive: true, force: true });
await rename(destination, published);
console.log('Bundled VELIA runtime is ready:', process.platform, process.arch);
