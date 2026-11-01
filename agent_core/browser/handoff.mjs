export const USER_ACTION_KINDS = Object.freeze([
  'credentials',
  'otp',
  'passkey',
  'captcha',
  'device_approval',
]);

const marker = /(?:^|\n)VELIA_USER_ACTION_REQUIRED:(credentials|otp|passkey|captcha|device_approval)\s*(?:\n|$)/i;

export function extractUserAction(text) {
  const value = String(text ?? '');
  const match = value.match(marker);
  const kind = match ? match[1].toLowerCase() : null;
  const cleaned = value.replace(marker, '\n').replace(/\n{3,}/g, '\n\n').trim();
  return { text: cleaned, userActionRequired: kind };
}
