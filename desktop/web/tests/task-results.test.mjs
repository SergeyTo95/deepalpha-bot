import test from 'node:test';
import assert from 'node:assert/strict';
import {mergeMissionMessages,missionResultText} from '../task-results.mjs';
import {wantsBackgroundTask,setupMissionClient} from '../missions.mjs';
import {setupPulse} from '../pulse.mjs';
const mid='11111111-1111-4111-a111-111111111111',cid='22222222-2222-4222-a222-222222222222',nid='33333333-3333-4333-a333-333333333333';
const fixture=()=>({id:mid,title:'План Velia',goal:'Подготовь подробный план Velia',model:'velia-quantum',status:'completed',revision:5,created_at:100,source:{chat_id:cid,title:'Исходный диалог',remote:false},steps:[{id:'s1',status:'completed',tool:'text.draft',result:{kind:'draft',data:{text:'Проверенный текст'}}},{id:'s2',status:'completed',tool:'document.export',result:{kind:'file',data:{title:'План',format:'pdf',bytes:1024,sha256:'fixture'}}}]});
const notice=(status='completed')=>({id:nid,mission_id:mid,task_status:status,at:150,status:'unread',kind:'task',title:'Задача выполнена: План Velia',delivery:'accepted',resource:{kind:'mission',id:mid,chat_id:cid}});

test('long analysis and explicit background goals are detected without sending simple questions to a queue',()=>{
  for(const s of ['Подготовь подробный анализ рынка роботов','Разработай стратегию развития проекта Velia','Составь бизнес-план магазина','Сделай обзор в фоне','Исследуй подробно рынок доставки дронами в Турции'])assert.equal(wantsBackgroundTask(s),true,s);
  for(const s of ['Привет','Что такое денежный резерв?','Посчитай расходы за сегодня','Напомни завтра в 10','Создай калькулятор бюджета','Подготовь деплой GitHub','Повторяй каждый день: составь бизнес-план'])assert.equal(wantsBackgroundTask(s),false,s);
});

test('completed work restores to its originating chat once, including after device-cache loss',()=>{
  const m=fixture(),chats=[{id:'other',title:'Другой диалог',messages:[],updated:200}];
  assert.equal(mergeMissionMessages(chats,m,[notice()]),true);
  assert.equal(chats.find(c=>c.id==='other').messages.length,0);
  const original=chats.find(c=>c.id===cid);assert.equal(original.messages.length,3);
  assert.match(original.messages.at(-1).content,/Проверенный текст/);assert.match(original.messages.at(-1).content,/Файл готов/);
  assert.equal(original.messages.at(-1).taskEvent,nid);assert.equal(mergeMissionMessages(chats,m,[notice()]),false);
  const recovered=[];mergeMissionMessages(recovered,m,[{...notice(),status:'read'}]);assert.equal(recovered[0].messages.at(-1).taskEvent,nid,'reading a notification must not discard its chat result');
});

test('refreshing native history reattaches mission overlays without copying approval tokens',()=>{
  const m=fixture();m.source.remote=true;m.steps.push({id:'s3',status:'completed',tool:'review',approval:{token:'NEVER_CACHE_THIS'}});
  const chats=[{id:cid,title:'Чат',remote:true,messages:[{role:'user',content:'Старая переписка',sentAt:1}],updated:1}];mergeMissionMessages(chats,m,[notice()]);
  assert.equal(chats[0].messages[0].content,'Старая переписка');assert.doesNotMatch(JSON.stringify(chats),/NEVER_CACHE_THIS/);
  chats[0].messages=chats[0].messages.slice(0,1);mergeMissionMessages(chats,m,[notice()]);assert.equal(chats[0].messages.length,4);
});

test('an intentionally deleted chat is never resurrected by a late completion',()=>{
  const m=fixture();m.source.detached=true;const chats=[];assert.equal(mergeMissionMessages(chats,m,[notice()]),false);assert.deepEqual(chats,[]);
});

test('waiting, failed and uncertain states never become completion messages',()=>{
  for(const status of ['awaiting_approval','waiting_input','failed','uncertain','interrupted']){
    const m=fixture();m.status=status;m.question='Какой период?';const text=missionResultText(m,notice(status));assert.doesNotMatch(text,/Задача выполнена/);
    const chats=[];mergeMissionMessages(chats,m,[notice(status)]);assert.doesNotMatch(chats[0].messages.at(-1).content,/Задача выполнена/);
  }
  const m=fixture();m.steps[1].status='failed';const chats=[];mergeMissionMessages(chats,m,[notice()]);assert.equal(chats[0].messages.some(m=>m.taskEvent),false);
});

test('new result polling hydrates source chats even when the notice was already read on another device',async t=>{
  const m=fixture(),calls=[];const api=setupMissionClient({profile:()=>({account:'owner'}),request:async path=>{
    calls.push(path);return {ok:true,account:'owner',...(path==='/inbox'?{notifications:[{...notice(),status:'read'}]}:path==='/'+mid?{mission:m}:{missions:[{...m,steps:[]}]})};
  }});t.after(()=>api.reset());await api.poll();assert.equal(api.get(mid).steps[0].result.data.text,'Проверенный текст');assert.ok(calls.includes('/'+mid));
});

test('a changed cookie account is rejected before a result can populate chat history',async t=>{
  let locked=0;const api=setupMissionClient({profile:()=>({account:'old'}),onUnauthorized:()=>{locked++;api.reset();},request:async()=>({ok:true,account:'new',missions:[fixture()]})});t.after(()=>api.reset());
  await assert.rejects(api.poll(),{name:'AbortError'});assert.equal(locked,1);assert.deepEqual(api.list(),[]);
});

test('every task mutation carries the originating browser-account fingerprint',async t=>{
  let sent;const api=setupMissionClient({profile:()=>({account:'owner'}),request:async(path,options)=>{sent=options.data;return {ok:true,account:'owner',mission:fixture()};}});t.after(()=>api.reset());
  await api.submit({goal:'План'},new AbortController().signal);assert.equal(sent.account,'owner');assert.equal(sent.goal,'План');
});

test('Pulse merges durable task notices, hides bridge reminders and reads both records',async t=>{
  const previous=globalThis.document;globalThis.document={addEventListener(){},hidden:false};let tasks=[notice()],read=0,calls=[];
  const api=setupPulse({profile:()=>({account:'owner'}),taskInbox:()=>tasks,readTask:async id=>{assert.equal(id,nid);read++;tasks=tasks.map(n=>({...n,status:'read'}));},request:async(path,options)=>{
    calls.push({path,options});return {ok:true,account:'owner',items:[{item_id:nid,title:'Системное уведомление'},{item_id:'reminder',title:'Личное напоминание'}],notifications:[{id:'external',item_id:nid,title:'Копия',status:'unread'}]};
  }});t.after(()=>{api.reset();globalThis.document=previous;});
  await api.sync();assert.equal(api.state().notifications.length,1);assert.equal(api.state().notifications[0].resource.id,mid);assert.equal(api.state().items.length,1);
  await api.read(nid);assert.equal(read,1);assert.ok(calls.some(c=>c.path==='pulse/notifications/external/read'));assert.equal(api.state().notifications[0].status,'read');
});

test('ordinary follow-up receives actual task state without approval tokens or internal leases',async()=>{
  const {missionMessageContext}=await import('../task-results.mjs');const m=fixture();m.status='awaiting_approval';m.lease='INTERNAL_LEASE';m.steps[1].approval={token:'APPROVAL_SECRET'};
  const text=missionMessageContext({missionId:mid,content:'Приняла задачу'},m);assert.match(text,/awaiting_approval/);assert.doesNotMatch(text,/APPROVAL_SECRET|INTERNAL_LEASE/);assert.match(text,/обычный ответ в чате не разрешает/);
});

test('typed task markers from native history do not duplicate a delivered result',async()=>{
  const {missionHistoryMetadata}=await import('../task-results.mjs');const m=fixture(),n=notice();
  const chats=[{id:cid,title:'Исходный диалог',updated:1,messages:[
    {role:'user',content:m.goal,...missionHistoryMetadata({role:'user',mission_id:mid,mission_origin:true})},
    {role:'assistant',content:'Приняла задачу',...missionHistoryMetadata({role:'assistant',mission_id:mid,mission_ack:true})},
    {role:'assistant',content:'Готово',...missionHistoryMetadata({role:'assistant',mission_id:mid,mission_event_id:nid})}
  ]}];
  assert.equal(mergeMissionMessages(chats,m,[n]),false);assert.equal(chats[0].messages.length,3);
  assert.deepEqual(missionHistoryMetadata({mission_id:'bad'}),{});
  assert.equal(wantsBackgroundTask('Создай PDF с планом'),true);
});
