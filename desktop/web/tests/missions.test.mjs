import test from 'node:test';
import assert from 'node:assert/strict';
import {setupMissionClient,wantsMission,prepareMission,missionRequest} from '../missions.mjs';
import {mountMissionCard,mountMissionList} from '../mission-ui.mjs';
const id='11111111-1111-4111-a111-111111111111';
const mission=()=>({id,title:'Velia <script>',model:'velia-flash',revision:1,status:'awaiting_approval',steps:[{id:'s1',title:'Календарь',tool:'calendar.create',status:'awaiting_approval',approval:{token:'fixture-nonce',expires_at:Date.now()/1000+600,preview:{action:'calendar.create',title:'Встреча <img>',start:'2026-11-20T15:00:00+03:00',end:'2026-11-20T15:30:00+03:00',time_zone:'Europe/Istanbul'}}}]});
const stream=value=>new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(value)},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {resolve,reject,promise};};
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.attributes={};}
  append(...xs){this.children.push(...xs);}replaceChildren(...xs){this.children=[];this.append(...xs);}setAttribute(k,v){this.attributes[k]=v;}
  get childNodes(){return this.children;}set innerHTML(_){throw Error('Mission UI cannot render raw HTML');}
}
const nodes=e=>[e,...e.children.flatMap(nodes)];
const find=(e,text)=>nodes(e).find(n=>n.tag==='button'&&n.textContent===text);
async function dom(run){const previous=globalThis.document;globalThis.document={createElement:t=>new Element(t)};try{await run();}finally{globalThis.document=previous;}}

test('explicit missions and multi-action requests route without hijacking finance or miniapps',()=>{
  for(const text of ['Выполни миссию: подготовь PDF','Миссия: план проекта','Собери данные, подготовь отчёт, создай презентацию'])assert.ok(wantsMission(text),text);
  for(const text of ['Мои расходы сегодня','Создай калькулятор прибыли','Создай репозиторий отчёта на GitHub'])assert.equal(wantsMission(text),false,text);
});

test('planning calls only selected model with search off and never approves or executes',async()=>{
  for(const model of ['velia-flash','velia-quantum','velia-pro']){
    const calls=[],plan={title:'План',steps:[{id:'s1',title:'Текст',tool:'text.draft',arguments:{instruction:'План Velia'}}]};
    const result=await prepareMission({text:'Выполни миссию: план',model,catalog:{tools:[{name:'text.draft'}]},timeZone:'Europe/Istanbul',signal:new AbortController().signal,request:async(...args)=>{calls.push(args);return stream(plan);}});
    assert.equal(result.plan.title,'План');assert.equal(calls.length,1);assert.equal(calls[0][0],'missions/plan');assert.equal(calls[0][1].model,model);assert.equal(calls[0][1].web_search,false);
    assert.equal(Object.hasOwn(result,'approved'),false);
  }
});

test('planner questions retain goal and unknown or executable tools are rejected',async()=>{
  const options={text:'Выполни миссию: письмо',model:'velia-flash',catalog:{tools:[{name:'text.draft'}]},signal:new AbortController().signal,timeZone:'UTC'};
  const q=await prepareMission({...options,request:async()=>stream({question:'Какой email?'})});assert.equal(q.pending.goal,options.text);
  for(const tool of ['shell.exec','gmail.send','payment.send'])await assert.rejects(prepareMission({...options,request:async()=>stream({title:'План',steps:[{id:'s1',title:'Шаг',tool,arguments:{}}]})}),/проверить план/);
});

test('approval uses the exact displayed revision and nonce, and refresh is not approval',async t=>{
  const calls=[],m=mission(),client=setupMissionClient({profile:()=>({account:'a'}),request:async(path,options)=>{calls.push({path,options});return {ok:true,mission:m};}});t.after(()=>client.reset());
  await client.refresh(id);await client.action(m,'approve');assert.deepEqual(calls.at(-1).options.data,{revision:1,token:'fixture-nonce'});
  await client.action(m,'refresh-approval');assert.deepEqual(calls.at(-1).options.data,{revision:1});
});

test('late responses from a previous account never populate a new account',async t=>{
  let account='a';const old=deferred();const client=setupMissionClient({profile:()=>({account}),request:()=>account==='a'?old.promise:Promise.resolve({ok:true,missions:[]})});t.after(()=>client.reset());
  const pending=client.poll();client.reset();account='b';await client.poll();old.resolve({ok:true,missions:[mission()]});await assert.rejects(pending,{name:'AbortError'});
  assert.deepEqual(client.list(),[]);assert.equal(client.get(id),undefined);
});

test('poll failure preserves last status, shows stale notice, and clears on successful refresh',async t=>{
  let fail=false;const m=mission();const client=setupMissionClient({profile:()=>({account:'a'}),request:async()=>{if(fail)throw Error('Нет соединения');return {ok:true,missions:[m]};}});t.after(()=>client.reset());
  await client.poll();fail=true;await assert.rejects(client.poll(),/Нет соединения/);assert.equal(client.list()[0].id,id);assert.match(client.status(),/соединения/);
  fail=false;await client.poll();assert.equal(client.status(),'');
});

test('a broken view listener cannot interrupt logout or account cleanup',async t=>{
  const client=setupMissionClient({profile:()=>({account:'a'}),request:async()=>({ok:true,missions:[mission()]})});t.after(()=>client.reset());
  await client.poll();client.subscribe(()=>{throw Error('Detached UI');});assert.doesNotThrow(()=>client.reset());assert.deepEqual(client.list(),[]);
});

test('mission card displays plain text approval and requires explicit click',async()=>dom(async()=>{
  const m=mission(),calls=[],listeners=new Set(),controller=new AbortController(),parent=new Element('div');let watched=0;
  const client={get:()=>m,list:()=>[m],watch:()=>{watched++;return ()=>watched--;},subscribe:fn=>{listeners.add(fn);return ()=>listeners.delete(fn);},action:async(...args)=>calls.push(args),refresh:async()=>m};
  mountMissionCard(parent,{client,id,signal:controller.signal});assert.equal(calls.length,0);assert.ok(nodes(parent).some(n=>n.textContent==='Velia <script>'));
  const approve=find(parent,'Подтвердить и создать событие');assert.ok(approve);await approve.onclick();assert.equal(calls.length,1);assert.equal(calls[0][0].revision,1);assert.equal(calls[0][1],'approve');
  controller.abort();assert.equal(listeners.size,0);assert.equal(watched,0);
}));

test('expired approval only refreshes nonce and does not execute',async()=>dom(async()=>{
  const m=mission();m.steps[0].approval.expires_at=1;const calls=[],controller=new AbortController(),parent=new Element('div');
  const client={get:()=>m,watch:()=>()=>{},subscribe:()=>()=>{},action:async(_m,name)=>calls.push(name),refresh:async()=>m};
  mountMissionCard(parent,{client,id,signal:controller.signal});await find(parent,'Обновить подтверждение').onclick();assert.deepEqual(calls,['refresh-approval']);controller.abort();
}));

test('mission list cleanup detaches both list and detail subscribers',async()=>dom(async()=>{
  const m=mission(),listeners=new Set(),controller=new AbortController(),parent=new Element('div');let watched=0;
  const client={get:()=>m,list:()=>[m],watch:()=>{watched++;return ()=>watched--;},subscribe:fn=>{listeners.add(fn);return ()=>listeners.delete(fn);},poll:async()=>{},refresh:async()=>m};
  await mountMissionList(parent,{client,signal:controller.signal,toast:()=>{},prepare:()=>{},selectedId:id});assert.equal(listeners.size,2);controller.abort();assert.equal(listeners.size,0);assert.equal(watched,0);
}));

test('non-JSON errors are friendly and do not expose upstream HTML',async()=>{
  const previous=globalThis.fetch;globalThis.fetch=async()=>new Response('<!DOCTYPE secret>',{status:502});try{await assert.rejects(missionRequest(),e=>!e.message.includes('DOCTYPE')&&e.message.includes('соединение'));}finally{globalThis.fetch=previous;}
});
