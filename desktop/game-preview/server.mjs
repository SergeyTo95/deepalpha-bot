import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { decodeGame, gameSources, gameZip } from './exports.mjs';

const assets = {
  '/': ['index.html', 'text/html; charset=utf-8'],
  '/game.mjs': ['game.mjs', 'text/javascript; charset=utf-8'],
  '/style.css': ['style.css', 'text/css; charset=utf-8'],
  '/phaser.min.js': ['phaser.min.js', 'text/javascript; charset=utf-8'],
};
const files = new Map(await Promise.all(Object.entries(assets).map(async ([path, [file, type]]) => [
  path, { type, body: await readFile(new URL('./dist/' + file, import.meta.url)) },
])));
const policy = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
const branded = 'https://games.velyoncore.ai';
const legacy = 'velia-game-preview-production.up.railway.app';
let brandedReady = false;
async function checkBrandedDomain() {
  try {
    const response = await fetch(branded + '/health', { redirect: 'error', signal: AbortSignal.timeout(2200) });
    brandedReady = response.ok && response.headers.get('X-Velia-Game-Preview') === '1';
    await response.body?.cancel();
  } catch {
    brandedReady = false;
  }
}
await checkBrandedDomain();
setInterval(checkBrandedDomain, 60000).unref();

createServer((request, response) => {
  const path = request.url.split('?', 1)[0];
  const headers = {
    'Content-Security-Policy': policy,
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
    'Cache-Control': 'no-cache',
    'X-Velia-Game-Preview': '1',
  };
  if (!['GET', 'HEAD'].includes(request.method)) {
    response.writeHead(405, { ...headers, Allow: 'GET, HEAD' }).end();
    return;
  }
  if (request.url.length > 6000) {
    response.writeHead(414, headers).end();
    return;
  }
  if (brandedReady && request.headers.host === legacy && ['/', '/download'].includes(path)) {
    response.writeHead(307, { ...headers, Location: branded + request.url }).end();
    return;
  }
  if (path === '/download' || path === '/source.json') {
    let spec;
    try {
      const query = new URL(request.url, 'http://localhost').searchParams;
      spec = query.has('spec') ? decodeGame(query.get('spec')) : null;
    } catch {
      response.writeHead(400, headers).end();
      return;
    }
    const archive = path === '/download';
    const body = archive ? gameZip(spec, files) : Buffer.from(JSON.stringify({ files: gameSources(spec, files) }));
    response.writeHead(200, {
      ...headers,
      'Cache-Control': 'private, no-store',
      'Content-Type': archive ? 'application/zip' : 'application/json; charset=utf-8',
      'Content-Length': body.length,
      ...(archive && { 'Content-Disposition': 'attachment; filename="velia-game.zip"' }),
    }).end(request.method === 'HEAD' ? undefined : body);
    return;
  }
  const asset = path === '/health' ? { type: 'text/plain', body: Buffer.from('ok') } : files.get(path);
  if (!asset) {
    response.writeHead(404, headers).end();
    return;
  }
  response.writeHead(200, { ...headers, 'Content-Type': asset.type, 'Content-Length': asset.body.length });
  response.end(request.method === 'HEAD' ? undefined : asset.body);
}).listen(Number(process.env.PORT || 8080), '0.0.0.0');
