import { globSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

/** Include workspace service peers in the CLI carrier's production dependency set. */
export function runtimeClosure(checkout, targetPlatform = process.platform) {
  const packages = new Map();
  for (const file of globSync(['vendor/*/package.json', 'packages/*/*/package.json', 'apps/*/package.json',
    'native/system/package.json', 'native/system/packages/*/package.json'], { cwd: checkout })) {
    const manifest = JSON.parse(readFileSync(join(checkout, file), 'utf8'));
    packages.set(manifest.name, manifest);
  }
  const path = join(checkout, 'apps', 'cli', 'package.json');
  const original = readFileSync(path, 'utf8');
  const workspacePath = join(checkout, 'pnpm-workspace.yaml');
  const workspaceOriginal = readFileSync(workspacePath, 'utf8');
  const cli = JSON.parse(original);
  const visited = new Set(), required = new Set();
  function visit(name) {
    if (visited.has(name) || !packages.has(name)) return;
    visited.add(name);
    const manifest = packages.get(name);
    for (const dependency of Object.keys(manifest.dependencies || {})) visit(dependency);
    for (const peer of Object.keys(manifest.peerDependencies || {})) {
      if (packages.has(peer)) { required.add(peer); visit(peer); }
    }
  }
  visit(cli.name);
  if (process.env.VELIA_AGENT_CORE_BROWSER_RUNTIME === 'true') {
    for (const name of [
      '@deepseek-ai/dsh-browser-use',
      '@deepseek-ai/dsh-experimental-browser-use-runtime',
      '@deepseek-ai/dsh-experimental-browser-use-playwright-mcp',
    ]) {
      if (!packages.has(name)) throw new Error('VELIA Agent Core browser package missing: ' + name);
      required.add(name);
      visit(name);
    }
  }
  required.delete(cli.name);
  const added = [...required].filter(name => !(name in cli.dependencies)).sort();
  for (const name of added) cli.dependencies[name] = 'workspace:*';
  // link: overrides retain links to the build checkout after relocation.
  let workspace = workspaceOriginal.replaceAll('link:vendor/', 'file:vendor/');
  if (targetPlatform === 'win32' && process.platform === 'linux') {
    workspace += '\nsupportedArchitectures:\n  os: [current, win32]\n  cpu: [x64]\n';
  }
  writeFileSync(workspacePath, workspace);
  try { writeFileSync(path, JSON.stringify(cli, null, 2) + '\n'); }
  catch (error) { writeFileSync(workspacePath, workspaceOriginal); throw error; }
  return { added, restore: () => {
    writeFileSync(path, original); writeFileSync(workspacePath, workspaceOriginal);
  } };
}
