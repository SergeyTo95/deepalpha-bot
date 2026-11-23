const previewOrigin = 'https://velia-game-preview-production.up.railway.app';

export function wantsGame(text) {
  const match = String(text).trim().match(/^(?:велия[,\s]+)?(?:(?:создай|сделай|разработай|собери|напиши)\s+(?:[\p{L}\p{N}_-]+\s+){0,5}(?:(?:2d|html5)[- ])?(?:игру|мини[- ]?игру|игрушку)|(?:create|build|make|develop)\s+(?:[\p{L}\p{N}_-]+\s+){0,5}(?:(?:2d|html5)[- ])?(?:game|mini[- ]?game))(?=$|[\s,.!:?])/iu);
  if (!match) return false;
  const [prefix] = match[0].split(/(?:игру|мини[- ]?игру|игрушку|\bgame\b|mini[- ]?game)/iu, 1);
  return !/(?:^|\s)(?:план|концепци\p{L}*|иде[яию]|сценари[йя]|plan|concept|idea)(?=\s|$)/iu.test(prefix);
}

export function gamePreviewLink(raw) {
  try {
    const url = new URL(raw);
    if (url.origin === previewOrigin && url.pathname === '/' && !url.search && !url.username && !url.password
        && url.hash.startsWith('#v1=') && url.hash.length <= 1701) return url.href;
  } catch { /* Invalid results never become navigation targets. */ }
  return '';
}
