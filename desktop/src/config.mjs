import { homedir } from 'node:os';
import { join } from 'node:path';

export const HARNESS_COMMIT = '5badb15009ae1756c3afe0ae0cef1faafc290ccc';
export const defaultHome = () => join(homedir(), '.velia-desktop');

export function gatewayURL(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) {
    throw new Error('VELIA gateway must use HTTPS without credentials, query or fragment');
  }
  return url.href.replace(/\/$/, '');
}

export function profilePatch(baseURL, credentialPath) {
  return [
    ...(credentialPath ? [{ id: 'credentials', config: { path: credentialPath } }] : []),
    { id: 'llm-pi-ai', config: { providers: { velia: {
      displayName: 'VELIA', apiKeyEnv: 'VELIA_ACCESS_TOKEN',
      api: 'openai-completions', baseURL: gatewayURL(baseURL),
      retryPolicy: { mode: 'normal', maxRetries: 0 },
      models: [
        { id: 'velia-pro', name: 'VELIA PRO', contextWindow: 32768, maxTokens: 4096, input: ['text'] },
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
