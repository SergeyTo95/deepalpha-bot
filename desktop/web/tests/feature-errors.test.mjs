import test from 'node:test';
import assert from 'node:assert/strict';
import {featureRequest} from '../features.mjs';
async function withFetch(mock, run) {const previous=globalThis.fetch;globalThis.fetch=mock;try{await run();}finally{globalThis.fetch=previous;}}
test('HTML gateway failure gives a usable message instead of exposing parse errors',async()=>{
 await withFetch(async()=>new Response('<html>internal failure</html>',{status:502}),async()=>{
  await assert.rejects(featureRequest('projects'),e=>e.status===502&&e.message.includes('Попробуй')&&!e.message.includes('Unexpected'));
 });
});
test('expired sessions retain status for account lock and show a sign-in instruction',async()=>{
 await withFetch(async()=>Response.json({ok:false,error:'secret_internal_code'},{status:401}),async()=>{
  await assert.rejects(featureRequest('profile'),e=>e.status===401&&e.message.includes('Войди')&&!e.message.includes('secret_internal_code'));
 });
});
test('network loss and throttling have distinct actionable messages',async()=>{
 await withFetch(async()=>{throw new TypeError('Failed to fetch');},async()=>{
  await assert.rejects(featureRequest('research/missions'),/Проверь интернет/);
 });
 await withFetch(async()=>Response.json({ok:false},{status:429}),async()=>{
  await assert.rejects(featureRequest('projects'),/Подожди/);
 });
});
test('closing a section cancels its request without becoming a network error',async()=>{
 const controller=new AbortController();
 await withFetch(async(_url,{signal})=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true})),async()=>{
  const request=featureRequest('projects',{signal:controller.signal});controller.abort();
  await assert.rejects(request,{name:'AbortError'});
 });
});
test('file uploads explicitly select their chat model and OCR failures remain actionable',async()=>{
 const file=new File(['Invoice 42'],'invoice.txt',{type:'text/plain'});
 for(const [chatModel,mode] of [['velia-flash','flash'],['velia-quantum','quantum']]) {
  await withFetch(async(_url,options)=>{
   assert.equal(options.headers['X-Velia-Chat-Mode'],mode);
   assert.equal(await options.body.get('file').text(),'Invoice 42');
   return Response.json({ok:true,attachment:{id:'file'}});
  },()=>featureRequest('conversations/id/attachments',{method:'POST',file,chatModel}));
 }
 await withFetch(async()=>Response.json({ok:false,error:'image_has_no_readable_text'},{status:422}),async()=>{
  await assert.rejects(featureRequest('conversations/id/attachments',{method:'POST',file,chatModel:'velia-quantum'}),/распознать текст/);
 });
});
