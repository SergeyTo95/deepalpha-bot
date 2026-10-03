import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';

export const HARNESS_COMMIT = '5badb15009ae1756c3afe0ae0cef1faafc290ccc';
export function defaultHome(value = process.env.VELIA_DESKTOP_HOME) {
  if (!value) return join(homedir(), '.velia-desktop');
  if (typeof value !== 'string' || !isAbsolute(value)) {
    throw new Error('VELIA data directory must be an absolute path');
  }
  return value;
}

export function gatewayURL(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) {
    throw new Error('VELIA gateway must use HTTPS without credentials, query or fragment');
  }
  return url.href.replace(/\/$/, '');
}

export function profilePatch(baseURL, credentialPath, localGateway) {
  let providerURL = gatewayURL(baseURL);
  if (localGateway) {
    const local = new URL(localGateway);
    if (local.protocol !== 'http:' || local.hostname !== '127.0.0.1' || !local.port
        || local.username || local.password || local.search || local.hash || local.pathname !== '/v1') {
      throw new Error('Unexpected local VELIA gateway');
    }
    providerURL = local.href;
  }
  return [
    ...(credentialPath ? [{ id: 'credentials', config: { path: credentialPath } }] : []),
    { id: 'llm-pi-ai', config: { providers: { velia: {
      displayName: 'VELIA', apiKeyEnv: 'VELIA_ACCESS_TOKEN',
      api: 'openai-completions', baseURL: providerURL,
      retryPolicy: { mode: 'normal', maxRetries: 0 },
      models: [
        { id: 'velia-pro', name: 'VELIA PRO', contextWindow: 32768, maxTokens: 4096, input: ['text'] },
        { id: 'velia-flash', name: 'VELIA FLASH', contextWindow: 8192, maxTokens: 512,
          input: ['text'], reasoningEfforts: false,
          compat: { maxTokensField: 'max_tokens', supportsDeveloperRole: false,
            supportsReasoningEffort: false, supportsStrictMode: false } },
      ],
    } } } },
    { id: 'agent-default-model', config: { provider: 'velia', model: 'velia-pro' } },
    { id: 'system-prompt', config: {
      personaPrefix: 'Ты Велия (VELIA), ИИ-помощница. Говори о себе в женском роде. По умолчанию отвечай по-русски. Выполняй задачи с помощью доступных инструментов.',
      personaSuffix: 'Your working directory is {{cwd}}.',
    } },
    { id: 'desktop-product-telemetry', disabled: true },
    { id: 'product-analytics', disabled: true },
  ];
}

/** Accept only a tokenized launch URL from our loopback child. */
export function launchURL(line, port) {
  const match = line.match(/dsh web:\s*(http:\/\/[^\s]+)/);
  if (!match) return null;
  const url = new URL(match[1]);
  if (url.hostname !== '127.0.0.1' || !/^\d+$/.test(url.port) || Number(url.port) < 1
      || (port !== undefined && url.port !== String(port)) || url.username || url.password) {
    throw new Error('Unexpected Harness launch URL');
  }
  return url.href;
}
