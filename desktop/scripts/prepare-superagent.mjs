import { spawnSync } from 'node:child_process';
import { cp, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve, dirname, relative, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
import { SUPERAGENT_COMMIT } from '../src/upstreams.mjs';

const root = resolve(import.meta.dirname, '..');
const source = join(root, '.runtime', 'superagent-source');
export const superagentDirectory = join(root, '.runtime', 'superagent');
function run(command, args, cwd = root) {
  const result = spawnSync(command, args, { cwd, stdio: 'inherit' });
  if (result.error || result.status !== 0) throw result.error || new Error(`${command} failed (${result.status})`);
}

/** Copy production dependencies only. Harness supplies its own service peers. */
export async function stageSuperagent(checkout, destination) {
  checkout = resolve(checkout);
  destination = resolve(destination);
  const manifest = JSON.parse(await readFile(join(checkout, 'package.json'), 'utf8'));
  await mkdir(destination, { recursive: true });
  for (const name of ['lib', 'prompts', 'skills', 'locale', 'cordis.patch.yml', 'README.md', 'LICENSE']) {
    await cp(join(checkout, name), join(destination, name), { recursive: true, dereference: true });
  }
  const { scripts, devDependencies, packageManager, ...production } = manifest;
  await writeFile(join(destination, 'package.json'), JSON.stringify(production, null, 2) + '\n');
  const copied = new Set();
  async function dependency(name, anchor) {
    if (copied.has(name)) return;
    copied.add(name);
    const require = createRequire(join(anchor, 'package.json'));
    let directory;
    try { directory = dirname(require.resolve(`${name}/package.json`)); }
    catch {
      directory = dirname(require.resolve(name));
      while (!existsSync(join(directory, 'package.json'))) directory = dirname(directory);
    }
    directory = await realpath(directory);
    const info = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'));
    await mkdir(dirname(join(destination, 'node_modules', name)), { recursive: true });
    await cp(directory, join(destination, 'node_modules', name), {
      recursive: true, dereference: true, filter: path => !relative(directory, path).split(sep).includes('node_modules'),
    });
    for (const child of Object.keys(info.dependencies || {})) await dependency(child, directory);
  }
  for (const name of Object.keys(manifest.dependencies || {})) await dependency(name, checkout);
  await writeFile(join(destination, 'VELIA-UPSTREAM.json'), JSON.stringify({
    repository: 'deepseek-ai/dsh-superagent-prototype', commit: SUPERAGENT_COMMIT,
  }, null, 2) + '\n');
}

export async function prepareSuperagent() {
  await mkdir(join(root, '.runtime'), { recursive: true });
  if (!existsSync(source)) {
    run('git', ['init', source]);
    run('git', ['remote', 'add', 'origin', 'https://github.com/deepseek-ai/dsh-superagent-prototype.git'], source);
    run('git', ['fetch', '--depth', '1', 'origin', SUPERAGENT_COMMIT], source);
    run('git', ['checkout', '--detach', SUPERAGENT_COMMIT], source);
  }
  const head = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: source, encoding: 'utf8' });
  if (head.status !== 0 || head.stdout.trim() !== SUPERAGENT_COMMIT) throw new Error('Superagent revision mismatch');
  const changes = spawnSync('git', ['status', '--porcelain', '--untracked-files=no'], { cwd: source, encoding: 'utf8' });
  if (changes.status !== 0 || changes.stdout.trim()) throw new Error('Superagent source has local modifications');
  const pnpm = join(root, 'node_modules', 'pnpm', 'bin', 'pnpm.cjs');
  run(process.execPath, [pnpm, 'install', '--frozen-lockfile'], source);
  run(process.execPath, [pnpm, 'run', 'typecheck'], source);
  run(process.execPath, [pnpm, 'run', 'build'], source);
  await rm(superagentDirectory, { recursive: true, force: true });
  await stageSuperagent(source, superagentDirectory);
  console.log('VELIA developer extension prepared at pinned revision.');
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await prepareSuperagent();
