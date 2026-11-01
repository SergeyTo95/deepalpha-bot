import { execFileSync } from 'node:child_process';
import { existsSync, realpathSync } from 'node:fs';
import { dirname, resolve, relative, isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';

/** Run only against the configured GitHub checkout and a fresh Ticket directory. */
export function prepareWorktree(checkout, repository, baseBranch, env = process.env) {
  const directory = env.DSH_SUPERAGENT_DIR, name = env.DSH_SUPERAGENT_NAME;
  if (!isAbsolute(checkout) || !isAbsolute(directory || '') || !/^[a-zA-Z0-9_-]+$/.test(name || '')
      || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository || '')) throw new Error('Invalid Ticket workspace');
  const root = realpathSync(checkout);
  const destination = resolve(realpathSync(dirname(directory)), directory.split('/').at(-1));
  if (existsSync(destination) || destination === root || !relative(root, destination).startsWith('..')) {
    throw new Error('Ticket directory must be new and outside the source checkout');
  }
  const git = args => execFileSync('git', ['-C', root, ...args], { encoding: 'utf8', timeout: 120000, env });
  git(['check-ref-format', '--branch', baseBranch]);
  const remote = git(['remote', 'get-url', 'origin']).trim();
  if (remote !== `https://github.com/${repository}.git` && remote !== `https://github.com/${repository}`) {
    throw new Error('Source checkout does not match the configured repository');
  }
  git(['fetch', '--no-tags', 'origin', baseBranch]);
  git(['worktree', 'add', '-b', `velia/${name}`, destination, 'FETCH_HEAD']);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  prepareWorktree(...process.argv.slice(2));
}
