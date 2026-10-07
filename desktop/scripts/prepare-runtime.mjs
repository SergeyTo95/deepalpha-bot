import { spawnSync } from 'node:child_process';
import { mkdir, mkdtemp, rm, copyFile, chmod, writeFile, rename } from 'node:fs/promises';
import { resolve, join, dirname } from 'node:path';
import { HARNESS_COMMIT } from '../src/config.mjs';
import { runtimeClosure } from './runtime-closure.mjs';
import { qualificationRuntime, runtimeProbe } from './qualification-runtime.mjs';

const root = resolve(import.meta.dirname, '..');
const checkout = join(root, '.runtime', 'harness');
const published = join(root, '.runtime', 'package');
const destination = await mkdtemp(join(root, '.runtime', '.package-'));
const deployed = join(destination, 'harness');
const platform = process.env.VELIA_RUNTIME_PLATFORM || process.platform;
const windowsNode = process.env.VELIA_WINDOWS_NODE;
if (platform !== process.platform && !(platform === 'win32' && process.platform === 'linux' && process.arch === 'x64' && windowsNode)) {
  throw new Error('Unsupported cross-platform runtime preparation');
}
const closure = runtimeClosure(checkout, platform);
try {
  const result = spawnSync(process.execPath, [join(root, 'node_modules', 'pnpm', 'bin', 'pnpm.cjs'),
    '--filter', '@deepseek-ai/dsh', '--prod', '--config.node-linker=hoisted',
    '--config.allow-unused-patches=true', 'deploy', '--legacy',
    ...(platform !== process.platform ? ['--ignore-scripts'] : []), deployed],
  { cwd: checkout, stdio: 'inherit' });
  if (result.error || result.status !== 0) throw result.error || new Error('Harness runtime deployment failed');
} finally { closure.restore(); }
await mkdir(join(destination, 'node'), { recursive: true });
const node = join(destination, 'node', platform === 'win32' ? 'node.exe' : 'node');
await copyFile(platform !== process.platform ? windowsNode : process.execPath, node);
if (platform !== 'win32') await chmod(node, 0o755);
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
  node: process.version, platform, arch: process.arch, workspacePeers: closure.added,
  dependencyLayout: 'hoisted', localOverrides: 'file',
  nodeArchiveSHA256: process.env.VELIA_WINDOWS_NODE_SHA256 || null }, null, 2) + '\n');
await runtimeProbe(qualificationRuntime(destination));
await rm(published, { recursive: true, force: true });
await rename(destination, published);
console.log('Bundled VELIA runtime is ready:', platform, process.arch);
