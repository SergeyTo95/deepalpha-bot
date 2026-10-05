import { createServer } from 'node:http';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { once } from 'node:events';
import { gatewayURL } from './config.mjs';

/** Keep account tokens in the main process; Harness receives only a loopback key. */
export async function startProxy(gateway, getSession, fetcher = fetch, options = {}) {
  const endpoint = gatewayURL(gateway);
  const key = randomBytes(32).toString('hex');
  const authorization = Buffer.from('Bearer ' + key);
  const server = createServer(async (request, response) => {
    const provided = Buffer.from(request.headers.authorization || '');
    const reply = (status, code) => {
      if (!response.destroyed && !response.headersSent) {
        response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
        response.end(JSON.stringify({ error: { message: code, type: 'velia_desktop_error' } }));
      }
    };
    if (provided.length !== authorization.length || !timingSafeEqual(provided, authorization)) {
      reply(401, 'unauthorized'); return;
    }
    if (!((request.method === 'GET' && request.url === '/v1/models')
        || (request.method === 'POST' && request.url === '/v1/chat/completions'))) {
      reply(404, 'unknown_endpoint'); return;
    }
    const cancellation = new AbortController();
    const cancelled = () => cancellation.abort();
    request.once('aborted', cancelled); response.once('close', cancelled);
    try {
      const chunks = []; let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 1024 * 1024) { reply(413, 'request_too_large'); return; }
        chunks.push(chunk);
      }
      const session = await getSession();
      if (new URL(session.origin).origin !== new URL(endpoint).origin) {
        reply(401, 'account_origin_mismatch'); return;
      }
      let body = Buffer.concat(chunks);
      let flash = false;
      try {
        let payload = JSON.parse(body.toString());
        flash = payload.model === 'velia-flash';
        if (flash && Array.isArray(payload.tools) && Array.isArray(options.allowedToolNames)) {
          const allowed = new Set(options.allowedToolNames);
          payload = { ...payload, tools: payload.tools.filter(tool => allowed.has(tool?.function?.name)) };
          if (!payload.tools.length) throw new Error('VELIA Agent Core tool allowlist matched no tools');
        }
        // pi-ai reserves 4096 tokens above its estimate. The shipped tool set
        // makes that heuristic reduce Flash's 512-token budget to one token.
        // The gateway renders/tokenizes the real prompt and reserves the full
        // answer before inference, so restore the declared budget for this case.
        if (flash && payload.max_tokens === 1 && Array.isArray(payload.tools) && payload.tools.length) {
          payload = { ...payload, max_tokens: 512 };
        }
        body = Buffer.from(JSON.stringify(payload));
      } catch (error) {
        if (error?.message === 'VELIA Agent Core tool allowlist matched no tools') {
          reply(400, 'tool_allowlist_empty'); return;
        }
        /* Gateway validates malformed JSON. */
      }
      const upstream = await fetcher(endpoint + request.url.slice(3), {
        method: request.method, redirect: 'error',
        headers: { Authorization: 'Bearer ' + session.access_token, 'Content-Type': 'application/json' },
        ...(request.method === 'POST' ? { body } : {}),
        signal: AbortSignal.any([cancellation.signal, AbortSignal.timeout(flash ? 360000 : 180000)]),
      });
      response.writeHead(upstream.status, { 'Content-Type': upstream.headers.get('Content-Type') || 'application/json',
        'Cache-Control': 'no-store' });
      if (upstream.body) for await (const chunk of upstream.body) {
        if (!response.write(chunk)) await once(response, 'drain', { signal: cancellation.signal });
      }
      response.end();
    } catch {
      if (response.headersSent) response.destroy();
      else reply(502, 'velia_connection_failed');
    } finally {
      request.removeListener('aborted', cancelled); response.removeListener('close', cancelled);
    }
  });
  server.maxHeadersCount = 30;
  server.requestTimeout = 20000;
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  return { url: `http://127.0.0.1:${server.address().port}/v1`, key,
    async close() { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); },
  };
}
