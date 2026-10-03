import { spawnSync } from 'node:child_process';
import { createReadStream } from 'node:fs';
import { readFile, writeFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve, join } from 'node:path';
import { HARNESS_COMMIT } from '../src/config.mjs';

const root = resolve(import.meta.dirname, '..');
function run(args) {
  const result = spawnSync(process.execPath, args, { cwd: root, stdio: 'inherit',
    env: { ...process.env, CSC_IDENTITY_AUTO_DISCOVERY: 'false' } });
  if (result.error || result.status !== 0) throw result.error || new Error(`Windows builder failed: ${args[0]}`);
}
run(['scripts/prepare-windows-runtime.mjs']);
run(['scripts/smoke-windows-deps.mjs']);
run(['scripts/smoke-harness.mjs']);
run(['scripts/smoke-web.mjs']);
// Railway qualifies the Windows x64 runtime under Wine, but cannot execute the
// 32-bit NSIS uninstaller helper. Produce an explicitly portable archive.
run(['node_modules/electron-builder/out/cli/cli.js', '--win', '--x64', '--dir', '--publish', 'never']);
run(['scripts/smoke-windows-portable.mjs']);
const pkg = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'));
const name = `VELIA-Desktop-${pkg.version}-win-x64.zip`;
const path = join(root, 'dist', name), hash = createHash('sha256');
for await (const chunk of createReadStream(path)) hash.update(chunk);
const files = [{ name, bytes: (await stat(path)).size, sha256: hash.digest('hex') }];
const manifest = { product: 'VELIA Desktop Preview', version: pkg.version, platform: 'win32', arch: 'x64',
  artifactKind: 'portable-zip', sourceCommit: process.env.RAILWAY_GIT_COMMIT_SHA || null, harnessCommit: HARNESS_COMMIT,
  builtAt: new Date().toISOString(), qualification: { windowsNodeUnderWine: true,
    windowsNativeModules: true, readToolRoundTrip: true, authenticatedWeb: true, packagedResources: true,
    portableArchive: true, extractedResources: true, installer: false,
    physicalWindowsGUI: false, liveModel: false, signed: false }, files };
await writeFile(join(root, 'dist', 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log('VELIA_WINDOWS_PORTABLE_BUILT', JSON.stringify(manifest));
