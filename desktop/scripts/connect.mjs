import { createInterface } from 'node:readline/promises';
import { randomUUID } from 'node:crypto';
import { mkdir } from 'node:fs/promises';
import { defaultHome, gatewayURL } from '../src/config.mjs';
import { saveSession } from '../src/session.mjs';

const origin = new URL(gatewayURL(process.env.VELIA_GATEWAY_URL || 'https://deepalpha-ai.com/desktop-api/v1')).origin;
console.log(`Откройте ${origin}/mobile-connect под своим аккаунтом Велии и получите код подключения.`);
const prompt = createInterface({ input: process.stdin, output: process.stdout });
try {
  const code = await prompt.question('Код подключения: ');
  const device_id = randomUUID();
  const response = await fetch(origin + '/mobile-api/v1/auth/exchange', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pairing_code: code.trim(), device_id, device_name: 'VELIA Desktop' }),
    signal: AbortSignal.timeout(20000),
  });
  const result = await response.json();
  if (!response.ok || !result.ok || !result.access_token || !result.refresh_token) throw new Error('Код не принят. Получите новый код подключения.');
  const home = defaultHome();
  await mkdir(home, { recursive: true });
  await saveSession(home, { ...result, origin, device_id, expiresAt: Date.now() + Number(result.access_expires_in || 900) * 1000 });
  console.log('VELIA Desktop подключена. Токены не выводятся в консоль.');
} finally { prompt.close(); }
