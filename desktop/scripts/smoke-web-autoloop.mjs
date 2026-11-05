/** Whole-app recurring-task acceptance. All account endpoints are local fixtures. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const {JSDOM}=await import(process.env.VELIA_JSDOM_MODULE||'jsdom');
const dom=new JSDOM(await fs.readFile(new URL('../web/index.html',import.meta.url),'utf8'),{url:'https://velia.example',pretendToBeVisual:true});
const {window}=dom;
Object.assign(globalThis,{window,document:window.document,localStorage:window.localStorage,sessionStorage:window.sessionStorage,
  location:window.location,matchMedia:()=>({matches:true}),requestAnimationFrame:window.requestAnimationFrame.bind(window),
  cancelAnimationFrame:window.cancelAnimationFrame.bind(window),BroadcastChannel:undefined});
Object.defineProperty(globalThis,'navigator',{value:window.navigator,configurable:true});
window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
window.HTMLDialogElement.prototype.close=function(){this.open=false;};
const cid='00000000-0000-4000-8000-000000000001',rid='00000000-0000-4000-8000-000000000002';
const calls=[],rules=[],conversations=[];
const profile={ok:true,account:'a'.repeat(24),history_cache_key:'b'.repeat(64),name:'Owner',language:'ru',credits:10,
  free_api_tiers:true,background_tasks:true,models:['velia-flash','velia-quantum','velia-pro']};
globalThis.fetch=async(url,options={})=>{
  const path=String(url).replace('/web-api/v1/',''),data=options.body?JSON.parse(options.body):null;calls.push({path,data});
  if(path==='session')return Response.json(profile);
  if(path==='background-tasks')return Response.json({ok:true,jobs:[]});
  if(path==='autoloop/rules'&&data){const rule={...data,id:rid,model:data.model.replace('velia-','')};rules.push(rule);return Response.json({ok:true,rule},{status:201});}
  if(path==='autoloop/rules')return Response.json({ok:true,rules});
  if(path===`autoloop/rules/${rid}/disable`){rules[0].enabled=false;return Response.json({ok:true,rule:rules[0]});}
  if(path==='conversations'&&data){const conversation={id:cid,title:data.title};conversations.push(conversation);return Response.json({ok:true,conversation});}
  if(path==='conversations')return Response.json({ok:true,conversations});
  if(path.endsWith('/messages'))return Response.json({ok:true,messages:[{id:'ack',role:'assistant',content:'Поручение сохранено.',status:'completed',chat_mode:'quantum'}]});
  if(path==='voice/voices')return Response.json({voices:[]});
  return Response.json({ok:false,error:'fixture_unavailable'},{status:404});
};
try{
  await import('../web/app.mjs');await new Promise(r=>setTimeout(r,80));
  assert.equal(document.querySelector('#autoloop-create').hidden,false);
  document.querySelector('[data-model="velia-quantum"]').click();
  document.querySelector('#autoloop-create').click();
  assert.match(document.querySelector('#prompt').value,/Повторяй каждые 60 минут/);
  document.querySelector('#prompt').value+='Проверь статус проекта';
  document.querySelector('#composer').dispatchEvent(new window.Event('submit',{cancelable:true}));
  await new Promise(r=>setTimeout(r,120));
  assert.equal(rules.length,1);assert.equal(rules[0].model,'quantum');assert.equal(rules[0].interval_minutes,60);
  assert.equal(document.querySelector('#autoloop-rules').hidden,false);
  assert.match(document.querySelector('#autoloop-rules').textContent,/Проверь статус проекта/);
  assert.ok(!calls.some(c=>c.path.endsWith('/tasks')||c.path.endsWith('/messages/stream')));
  const pause=[...document.querySelectorAll('#autoloop-rules button')].find(b=>b.textContent==='Остановить повторы');
  pause.click();await new Promise(r=>setTimeout(r,50));
  assert.equal(rules[0].enabled,false);assert.match(document.querySelector('#autoloop-rules').textContent,/на паузе/);
  console.log(JSON.stringify({ok:true,synthetic:true,checks:['recurring action visible','draft prepared','selected Quantum preserved',
    'schedule submitted once','no foreground inference','rule rendered','pause persisted']}));
}catch(error){console.error(error);process.exitCode=1;}
finally{dom.window.close();setTimeout(()=>process.exit(process.exitCode||0),10);}
