import { randomUUID } from 'node:crypto';
import { gatewayURL } from './config.mjs';
import { sessionPayload } from './session.mjs';

export async function pairDevice(gateway, code, fetcher = fetch) {
  const normalized = typeof code === 'string' ? code.toUpperCase().replace(/[\s._-]/g, '') : '';
  if (!/^[ABCDEFGHJKLMNPQRSTUVWXYZ2-9]{16}$/.test(normalized)) throw new Error('Введите код подключения Велии из 16 символов.');
  const origin = new URL(gatewayURL(gateway)).origin;
  const device_id = randomUUID();
  const response = await fetcher(origin + '/mobile-api/v1/auth/exchange', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pairing_code: normalized, device_id, device_name: 'VELIA Desktop' }),
    redirect: 'error',
    signal: AbortSignal.timeout(20000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error('Код не принят. Получите новый код подключения.');
  return sessionPayload(result, { origin, device_id });
}
