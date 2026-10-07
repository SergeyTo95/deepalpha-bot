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
    { id: 'llm-deepseek', disabled: true },
    { id: 'llm-deepseek-account', disabled: true },
    // Use the local first-message title instead of a competing auxiliary call.
    { id: 'session-title-llm', disabled: true },
  ];
}


/**
 * Hosted Browser Agent profile for VELIA Agent Core.
 *
 * Flash is deliberately the only model. The hosted surface gets browser tools
 * but no shell/filesystem preset, no provider fallback, and no plugin UI that
 * could re-enable server-side tools.
 */
export function browserAgentPatch(baseURL, credentialPath, localGateway, executablePath = '/usr/bin/chromium') {
  if (typeof executablePath !== 'string' || !executablePath.startsWith('/')) {
    throw new Error('VELIA Agent Core browser executable must use an absolute path');
  }
  const patch = profilePatch(baseURL, credentialPath, localGateway);
  const provider = patch.find(item => item.id === 'llm-pi-ai').config.providers.velia;
  provider.models = [provider.models.find(model => model.id === 'velia-flash')];
  patch.find(item => item.id === 'agent-default-model').config.model = 'velia-flash';
  patch.find(item => item.id === 'system-prompt').config = {
    personaPrefix: 'Ты Велия (VELIA), браузерный ИИ-агент. Говори о себе в женском роде. По умолчанию отвечай по-русски. Для действий в интернете используй только доступные браузерные инструменты. Не утверждай, что действие выполнено, пока инструмент не подтвердил результат.',
    personaSuffix: 'This is a hosted browser-agent session. There is no implicit access to the host filesystem or shell.',
  };
  patch.push(
    { id: 'agent-preset-registry', config: { default: 'velia-browser' } },
    { id: 'preset-standard', disabled: true },
    { id: 'preset-ptc', disabled: true },
    { id: 'preset-minimal', disabled: true },
    { id: 'preset-cordis', disabled: true },
    { id: 'ui-agent-preset', disabled: true },
    { id: 'ui-plugin-manager', disabled: true },
    { id: 'ui-settings-plugin-inventory', disabled: true },
    { id: 'ui-sidebar-terminal', disabled: true },
    { id: 'ui-sidebar-files', disabled: true },
    { id: 'ui-workspace', disabled: true },
    { id: 'terminal-controller', disabled: true },
    { id: 'workspace-files', disabled: true },
    { id: 'workspace-controller', disabled: true },
    { id: 'directory-picker', disabled: true },
    { insert: [
      { id: 'velia-browser-use', name: '@deepseek-ai/dsh-browser-use' },
      { id: 'velia-browser-provider', name: '@deepseek-ai/dsh-experimental-browser-use-playwright-mcp',
        config: { mode: 'launch', headless: true, executablePath, toolCallTimeoutMs: 45000 } },
      { id: 'preset-velia-browser', name: '@deepseek-ai/dsh-agent-preset', config: {
        id: 'velia-browser',
        order: 0,
        plugins: [
          { id: 'persona', name: '@deepseek-ai/dsh-persona', config: {
            prefix: 'Ты Велия (VELIA), браузерный ИИ-агент. Используй браузерные инструменты для навигации, чтения страниц, кликов и заполнения форм. Не выдумывай состояние страницы и не сообщай об успехе действия без результата инструмента.',
            suffix: 'The session has browser capabilities only; local shell and host filesystem access are not available.',
          } },
          { id: 'time-context', name: '@deepseek-ai/dsh-time-context' },
        ],
      } },
    ] },
  );
  return patch;
}

/** Accept only a tokenized launch URL from our loopback child. */
export function launchURL(line, port) {
  const match = line.match(/dsh web:\s*(http:\/\/[^\s]+)/);
  if (!match) return null;
  const url = new URL(match[1]);
  if (url.hostname !== '127.0.0.1' || !/^\d+$/.test(url.port) || Number(url.port) < 1
      || (port !== undefined && url.port !== String(port)) || url.username || url.password) {
    throw new Error('Unexpected VELIA Agent Core launch URL');
  }
  return url.href;
}
