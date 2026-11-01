import { isAbsolute, join } from 'node:path';
import { profilePatch, gatewayURL } from '../../desktop/src/config.mjs';

export { SUPERAGENT_COMMIT } from '../../desktop/src/upstreams.mjs';
export const PROFILE = 'velia-developer';
export const quote = value => `'${String(value).replaceAll("'", "'\\''")}'`;

/** Configuration is operator-owned; repository messages cannot change it. */
export function configuration(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected a configuration object');
  const allowed = new Set(['home', 'repository', 'checkout', 'githubCLI', 'allowedUsers', 'gateway', 'model', 'baseBranch']);
  if (Object.keys(value).some(key => !allowed.has(key))) throw new Error('Unknown developer configuration key');
  for (const key of ['home', 'checkout', 'githubCLI']) {
    if (typeof value[key] !== 'string' || !isAbsolute(value[key]) || /[\r\n\0]/.test(value[key])) {
      throw new Error(`${key} must be an absolute path`);
    }
  }
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(value.repository || '')) throw new Error('Expected repository owner/name');
  if (!Array.isArray(value.allowedUsers) || !value.allowedUsers.length
      || value.allowedUsers.some(login => typeof login !== 'string' || !/^[A-Za-z0-9-]+$/.test(login))) {
    throw new Error('allowedUsers must name GitHub users');
  }
  const model = value.model || 'velia-flash';
  if (!['velia-flash', 'velia-pro'].includes(model)) throw new Error('Select an existing VELIA model');
  const baseBranch = value.baseBranch || 'main';
  if (!/^[A-Za-z0-9][A-Za-z0-9_./-]*$/.test(baseBranch) || baseBranch.includes('..')
      || baseBranch.includes('//') || baseBranch.endsWith('/') || baseBranch.endsWith('.lock')) throw new Error('Invalid base branch');
  return { ...value, allowedUsers: [...new Set(value.allowedUsers)], gateway: gatewayURL(value.gateway), model, baseBranch };
}

/** One separate profile/process; the hosted browser profile never loads this bundle. */
export function developerPatch(config, credentialPath, localGateway) {
  const patch = profilePatch(config.gateway, credentialPath, localGateway);
  const provider = patch.find(item => item.id === 'llm-pi-ai').config.providers.velia;
  provider.models = provider.models.filter(model => model.id === config.model);
  patch.find(item => item.id === 'agent-default-model').config.model = config.model;
  patch.push({ id: 'dsh-superagent', config: { stateDir: join(config.home, 'state') } },
    { id: 'dsh-superagent-feishu', disabled: true });
  return patch;
}

export function initialSettings(config, prepareScript) {
  return {
    policy: { rules: [], sources: { github: {
      allowed: config.allowedUsers.map(id => ({ id })), bots: [],
    } } },
    eval: { mode: 'off', model: { provider: 'velia', model: config.model } },
    executor: { maxWorkers: 1, model: { provider: 'velia', model: config.model },
      directory: join(config.home, 'work'),
      prepare: [quote(process.execPath), quote(prepareScript), quote(config.checkout),
        quote(config.repository), quote(config.baseBranch)].join(' '),
      blockedCommands: [],
    },
    source: { enabled: true, config: { repository: config.repository, cli: config.githubCLI,
      observeIssues: false, observePulls: false, observeComments: false, observeCi: false } },
  };
}
