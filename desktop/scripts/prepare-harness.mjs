import { spawnSync } from 'node:child_process';
import { mkdirSync, existsSync, readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { HARNESS_COMMIT } from '../src/config.mjs';

const root = resolve(import.meta.dirname, '..');
const checkout = join(root, '.runtime', 'harness');
function run(command, args, cwd = root) {
  if (command === 'pnpm') {
    args = [join(root, 'node_modules', 'pnpm', 'bin', 'pnpm.cjs'), ...args];
    command = process.execPath;
  }
  const result = spawnSync(command, args, { cwd, stdio: 'inherit',
    env: { ...process.env, DSH_CLIENT_TITLE: 'VELIA Desktop' },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} failed (${result.status})`);
}
mkdirSync(join(root, '.runtime'), { recursive: true });
if (!existsSync(checkout)) {
  run('git', ['init', checkout]);
  run('git', ['remote', 'add', 'origin', 'https://github.com/deepseek-ai/deepseek-harness.git'], checkout);
  run('git', ['fetch', '--depth', '1', 'origin', HARNESS_COMMIT], checkout);
  run('git', ['checkout', '--detach', HARNESS_COMMIT], checkout);
}
const actual = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: checkout, encoding: 'utf8' });
if (actual.status !== 0 || actual.stdout.trim() !== HARNESS_COMMIT) throw new Error('Harness revision mismatch');

// Change visible product names only. Preserve package names, API ids and licenses.
function brand(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory() && !['node_modules', 'lib', 'tests', '.git'].includes(entry.name)) brand(path);
    else if (entry.isFile() && /\.(ts|tsx|html)$/.test(entry.name)) {
      const original = readFileSync(path, 'utf8');
      const changed = original.replaceAll('DeepSeek Harness', 'VELIA Desktop');
      if (changed !== original) writeFileSync(path, changed);
    }
  }
}
brand(join(checkout, 'packages', 'client'));
writeFileSync(join(checkout, 'VELIA-UPSTREAM.json'), JSON.stringify({ repository: 'deepseek-ai/deepseek-harness', commit: HARNESS_COMMIT }, null, 2) + '\n');
if (process.argv.includes('--build')) {
  run('pnpm', ['install', '--frozen-lockfile'], checkout);
  run('pnpm', ['run', 'build'], checkout);
}
console.log('VELIA Harness source prepared at pinned revision.');
