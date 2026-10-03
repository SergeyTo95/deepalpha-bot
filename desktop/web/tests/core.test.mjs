import test from 'node:test';
import assert from 'node:assert/strict';
import {renderMarkdown,readCompletion,chatPayload,loadChats} from '../core.mjs';

test('untrusted markup, attributes, fenced languages and links cannot inject HTML',()=>{
  const html=renderMarkdown('<img src=x onerror=alert(1)>\n\n[bad](javascript:alert)\n\n```"><svg onload=x>\n</code><script>alert(1)</script>\n```');
  assert.ok(!html.includes('<img')&&!html.includes('<svg')&&!html.includes('<script')&&!html.includes('href="javascript:'));
  assert.ok(html.includes('&lt;img')&&html.includes('&lt;script'));
});
test('common markdown is legible and code stays literal',()=>{
  const html=renderMarkdown('## План\n\n**Важно** и `x < y`\n\n- Первый\n- Второй\n\n| A | B |\n| --- | --- |\n| 1 | 2 |');
  assert.ok(html.includes('<h2>План</h2>')&&html.includes('<strong>Важно</strong>')&&html.includes('x &lt; y')&&html.includes('<ul>')&&html.includes('<table>'));
});
function response(text,chunkSize=1){const bytes=new TextEncoder().encode(text);return new Response(new ReadableStream({start(controller){for(let i=0;i<bytes.length;i+=chunkSize)controller.enqueue(bytes.slice(i,i+chunkSize));controller.close();}}),{headers:{'content-type':'text/event-stream'}});}
test('one-byte UTF-8 SSE boundaries retain Russian content and ignore reasoning',async()=>{
  let visible='';const result=await readCompletion(response('data: {"choices":[{"delta":{"reasoning_content":"PRIVATE","content":"Привет"}}]}\r\n\r\ndata: {"choices":[{"delta":{"content":"!"},"finish_reason":"stop"}]}\r\n\r\ndata: [DONE]\r\n\r\n'),s=>visible=s);
  assert.equal(visible,'Привет!');assert.equal(result.finish,'stop');
});
test('truncated streams preserve received text but report interruption',async()=>{
  let visible='';await assert.rejects(readCompletion(response('data: {"choices":[{"delta":{"content":"Часть"}}]}\n\n'),s=>visible=s),/прервался/);assert.equal(visible,'Часть');
});
test('server refusal keeps an actionable error and status',async()=>{
  await assert.rejects(readCompletion(new Response(JSON.stringify({error:{message:'flash_context_too_long'}}),{status:400}),()=>{}),e=>e.status===400&&e.message.includes('PRO'));
});
test('payload excludes failed answers and internal UI metadata',()=>{
  assert.deepEqual(chatPayload({messages:[{role:'user',content:'Привет',pending:false},{role:'assistant',content:'failed',failed:'error'},{role:'user',content:'Ещё'}]},'velia-flash'),{model:'velia-flash',stream:true,messages:[{role:'user',content:'Привет'},{role:'user',content:'Ещё'}]});
});
test('corrupt, oversized or unavailable history does not break the chat',()=>{
  for(const raw of ['bad','{}','[{"id":"a","title":"t","updated":1,"messages":[{"role":"system","content":"bad"}]}]'])assert.deepEqual(loadChats({getItem:()=>raw},'fixture'),[]);
  assert.deepEqual(loadChats({getItem:()=>{throw new Error('denied');}},'fixture'),[]);
});
