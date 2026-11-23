import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';

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
createServer((request, response) => {
  const path = request.url.split('?', 1)[0];
  const headers = {
    'Content-Security-Policy': policy,
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
    'Cache-Control': 'no-cache',
  };
  if (!['GET', 'HEAD'].includes(request.method)) {
    response.writeHead(405, { ...headers, Allow: 'GET, HEAD' }).end();
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
