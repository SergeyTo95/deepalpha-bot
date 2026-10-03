import { gatewayURL } from './config.mjs';

/** Validate wire credentials before encrypting them or using their lifetime. */
export function sessionPayload(result, previous) {
  if (!result?.ok || typeof result.access_token !== 'string' || !result.access_token.startsWith('va_')
      || typeof result.refresh_token !== 'string' || !result.refresh_token.startsWith('vr_')
      || !Number.isInteger(result.access_expires_in) || result.access_expires_in < 1
      || result.access_expires_in > 86400) throw new Error('Некорректная сессия Велии. Получите новый код подключения.');
  return { ...result, origin: previous.origin, device_id: previous.device_id,
    expiresAt: Date.now() + result.access_expires_in * 1000 };
}

export async function refreshSession(storage, fetcher = fetch) {
  const session = await storage.read();
  if (session.expiresAt > Date.now() + 60000) return session;
  const origin = new URL(gatewayURL(session.origin)).origin;
  const response = await fetcher(origin + '/mobile-api/v1/auth/refresh', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: session.refresh_token, device_id: session.device_id }),
    redirect: 'error',
    signal: AbortSignal.timeout(20000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error('Не удалось обновить сессию Велии.');
  const updated = sessionPayload(result, session);
  await storage.write(updated);
  return updated;
}

/** Serialize token rotation so a reused refresh token cannot revoke the device. */
export function sessionManager(storage, fetcher = fetch) {
  let pending;
  return () => {
    if (!pending) pending = refreshSession(storage, fetcher).finally(() => { pending = undefined; });
    return pending;
  };
}
