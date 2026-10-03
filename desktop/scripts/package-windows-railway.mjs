import { spawnSync } from 'node:child_process';
import { readFile, readdir, writeFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { resolve, join } from 'node:path';
import { HARNESS_COMMIT } from '../src/config.mjs';

const root = resolve(import.meta.dirname, '..');
const require = createRequire(import.meta.url);
function run(args, extraEnv = {}) {
  const result = spawnSync(process.execPath, args, { cwd: root, stdio: 'inherit',
    env: { ...process.env, CSC_IDENTITY_AUTO_DISCOVERY: 'false', ...extraEnv } });
  if (result.error || result.status !== 0) throw result.error || new Error(`Windows builder failed: ${args[0]}`);
}
run(['scripts/prepare-windows-runtime.mjs']);
run(['scripts/smoke-windows-deps.mjs']);
run(['scripts/smoke-harness.mjs']);
run(['scripts/smoke-web.mjs']);
const { getWineToolset } = require('app-builder-lib/out/toolsets/wine.js');
process.env.USE_SYSTEM_WINE = 'false';
const wineToolset = await getWineToolset('1.0.1');
const nativeWine = join(wineToolset.execPath, '..', '..', 'lib', 'wine', 'x86_64-unix', 'ntdll.so');
const libraries = spawnSync('ldd', [nativeWine], { encoding: 'utf8' });
if (libraries.error || libraries.status !== 0 || /not found/.test(libraries.stdout + libraries.stderr)) {
  throw libraries.error || new Error(`Missing Wine toolset dependencies: ${libraries.stdout}${libraries.stderr}`);
}
console.log('VELIA_NSIS_WINE_DEPENDENCIES_READY', libraries.stdout.trim());
// Use the checksum-pinned Wine 11 toolset for the 32-bit NSIS helper. The system
// Wine remains the independent executor for the Windows runtime qualifications.
// BCJ is supported by the NSIS extraction plugin; modern 7-Zip's BCJ2 is not.
run(['node_modules/electron-builder/out/cli/cli.js', '--win', '--x64', '--publish', 'never',
  '--config.toolsets.wine=1.0.1'], { USE_SYSTEM_WINE: 'false', ELECTRON_BUILDER_7Z_FILTER: 'BCJ' });
run(['scripts/smoke-windows-installer.mjs'], { USE_SYSTEM_WINE: 'false' });
const pkg = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'));
const files = [];
for (const name of await readdir(join(root, 'dist'))) {
  if (!name.endsWith('.exe') || !name.startsWith('VELIA-Desktop-')) continue;
  const path = join(root, 'dist', name), bytes = await readFile(path);
  if (bytes.toString('ascii', 0, 2) !== 'MZ') throw new Error('Installer is not a Windows PE executable');
  files.push({ name, bytes: (await stat(path)).size, sha256: createHash('sha256').update(bytes).digest('hex') });
}
if (files.length !== 1) throw new Error('Expected exactly one Windows preview installer');
const manifest = { product: 'VELIA Desktop Preview', version: pkg.version, platform: 'win32', arch: 'x64',
  sourceCommit: process.env.RAILWAY_GIT_COMMIT_SHA || null, harnessCommit: HARNESS_COMMIT,
  builtAt: new Date().toISOString(), qualification: { windowsNodeUnderWine: true,
    windowsNativeModules: true, readToolRoundTrip: true, authenticatedWeb: true, packagedResources: true,
    installerUnderWine: true, installedResources: true,
    physicalWindowsGUI: false, liveModel: false, signed: false }, files };
await writeFile(join(root, 'dist', 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log('VELIA_WINDOWS_INSTALLER_BUILT', JSON.stringify(manifest));
