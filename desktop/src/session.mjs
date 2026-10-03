import { readFile, writeFile, rename } from 'node:fs/promises';
import { join } from 'node:path';
import { gatewayURL } from './config.mjs';

export async function saveSession(home, session) {
  const path = join(home, 'session.json');
  await writeFile(path + '.tmp', JSON.stringify(session), { mode: 0o600 });
  await rename(path + '.tmp', path);
}

export async function refreshSession(home, fetcher = fetch) {
  let session;
  try { session = JSON.parse(await readFile(join(home, 'session.json'), 'utf8')); }
  catch { throw new Error('Подключите устройство: выполните npm run connect.'); }
  if (session.expiresAt > Date.now() + 60000) return session;
  const origin = new URL(gatewayURL(session.origin)).origin;
  const response = await fetcher(origin + '/mobile-api/v1/auth/refresh', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: session.refresh_token, device_id: session.device_id }),
    signal: AbortSignal.timeout(20000),
  });
  const result = await response.json();
  if (!response.ok || !result.ok || !result.access_token || !result.refresh_token) throw new Error('Не удалось обновить сессию Велии.');
  const updated = { ...session, ...result, expiresAt: Date.now() + Number(result.access_expires_in || 900) * 1000 };
  await saveSession(home, updated);
  return updated;
}
