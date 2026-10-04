import test from "node:test";
import assert from "node:assert/strict";
import {
  renderMarkdown,
  readCompletion,
  chatPayload,
  loadChats,
  safeSearch,
} from "../core.mjs";

test("untrusted markup, attributes, fenced languages and links cannot inject HTML", () => {
  const html = renderMarkdown(
    '<img src=x onerror=alert(1)>\n\n[bad](javascript:alert)\n\n```"><svg onload=x>\n</code><script>alert(1)</script>\n```',
  );
  assert.ok(
    !html.includes("<img") &&
      !html.includes("<svg") &&
      !html.includes("<script") &&
      !html.includes('href="javascript:'),
  );
  assert.ok(html.includes("&lt;img") && html.includes("&lt;script"));
});
test("common markdown is legible and code stays literal", () => {
  const html = renderMarkdown(
    "## План\n\n**Важно** и `x < y`\n\n- Первый\n- Второй\n\n| A | B |\n| --- | --- |\n| 1 | 2 |",
  );
  assert.ok(
    html.includes("<h2>План</h2>") &&
      html.includes("<strong>Важно</strong>") &&
      html.includes("x &lt; y") &&
      html.includes("<ul>") &&
      html.includes("<table>"),
  );
});
function response(text, chunkSize = 1) {
  const bytes = new TextEncoder().encode(text);
  return new Response(
    new ReadableStream({
      start(controller) {
        for (let i = 0; i < bytes.length; i += chunkSize)
          controller.enqueue(bytes.slice(i, i + chunkSize));
        controller.close();
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  );
}
test("search sources survive streaming and unsafe source links are ignored", async () => {
  const sources = {sources:[{title:"Python",url:"https://www.python.org/"},{title:"bad",url:"javascript:alert(1)"}],
    retrieved_at:"2026-10-03T20:00:00Z", provider:"private", api_key:"private"};
  let metadata;
  const result = await readCompletion(response(
    'data: ' + JSON.stringify({web_search:sources}) + '\n\ndata: {"choices":[{"delta":{"content":"Ответ [1]"}}]}\n\ndata: [DONE]\n\n'),
    () => {}, (value) => {metadata=value;});
  assert.equal(result.text, "Ответ [1]");
  assert.deepEqual(result.search, metadata);
  assert.deepEqual(metadata, safeSearch(sources));
  assert.equal(metadata.sources.length, 1);
  assert.equal(metadata.provider, undefined);
  assert.equal(metadata.api_key, undefined);
});
test("one-byte UTF-8 SSE boundaries retain Russian content and ignore reasoning", async () => {
  let visible = "";
  const result = await readCompletion(
    response(
      'data: {"choices":[{"delta":{"reasoning_content":"PRIVATE","content":"Привет"}}]}\r\n\r\ndata: {"choices":[{"delta":{"content":"!"},"finish_reason":"stop"}]}\r\n\r\ndata: [DONE]\r\n\r\n',
    ),
    (s) => (visible = s),
  );
  assert.equal(visible, "Привет!");
  assert.equal(result.finish, "stop");
});
test("truncated streams preserve received text but report interruption", async () => {
  let visible = "";
  await assert.rejects(
    readCompletion(
      response('data: {"choices":[{"delta":{"content":"Часть"}}]}\n\n'),
      (s) => (visible = s),
    ),
    /прервался/,
  );
  assert.equal(visible, "Часть");
});
test("a buffered burst publishes once and a canonical reset never flashes empty text", async () => {
  const frames = Array.from({length: 1000}, () => 'data: {"choices":[{"delta":{"content":"текст "}}]}\n\n').join("");
  const text = "текст ".repeat(1000), visible = [];
  const final = 'data: ' + JSON.stringify({reset: true, choices: [{delta: {content: text}, finish_reason: "stop"}]}) + '\n\ndata: [DONE]\n\n';
  const result = await readCompletion(response(frames + final, 1000000), (value) => visible.push(value));
  assert.deepEqual(visible, [text]);
  assert.equal(result.text, text);
});
test("a provider error preserves validated partial text from the same chunk", async () => {
  let visible;
  await assert.rejects(readCompletion(response(
    'data: {"choices":[{"delta":{"content":"Часть ответа"}}]}\n\ndata: {"error":{"message":"model_request_failed"}}\n\n', 1000000),
    value => {visible = value;}));
  assert.equal(visible, "Часть ответа");
});
test("server refusal keeps an actionable error and status", async () => {
  await assert.rejects(
    readCompletion(
      new Response(
        JSON.stringify({ error: { message: "flash_context_too_long" } }),
        { status: 400 },
      ),
      () => {},
    ),
    (e) => e.status === 400 && e.message.includes("PRO"),
  );
});
test("payload excludes failed answers and internal UI metadata", () => {
  assert.deepEqual(
    chatPayload(
      {
        messages: [
          { role: "user", content: "Привет", pending: false },
          { role: "assistant", content: "failed", failed: "error" },
          { role: "user", content: "Ещё" },
        ],
      },
      "velia-flash",
    ),
    {
      model: "velia-flash",
      stream: true,
      messages: [
        { role: "user", content: "Привет" },
        { role: "user", content: "Ещё" },
      ],
    },
  );
});
test("corrupt, oversized or unavailable history does not break the chat", () => {
  for (const raw of [
    "bad",
    "{}",
    '[{"id":"a","title":"t","updated":1,"messages":[{"role":"system","content":"bad"}]}]',
  ])
    assert.deepEqual(loadChats({ getItem: () => raw }, "fixture"), []);
  assert.deepEqual(
    loadChats(
      {
        getItem: () => {
          throw new Error("denied");
        },
      },
      "fixture",
    ),
    [],
  );
});
