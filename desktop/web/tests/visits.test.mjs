import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {Notebook} from '../notebook.mjs';
import {localDate,visitDate,validDate,validateVisit,visitsForDate,visitConflicts,deviceTimeZone} from '../visits.mjs';
import {runMapAgent,wantsMap,validMapData,mapReply,parseMapPlan} from '../map-agent.mjs';
import {quickVisitReply,createVisitActions} from '../visit-agent.mjs';
const now=Date.parse('2026-11-07T21:30:00Z');
const point={title:'Клиент Иван',address:'Анталия, улица 1',lat:36.9,lon:30.7};
const output=messages=>JSON.parse(messages.at(-1).content.split('): ')[1]);
const record=patch=>({id:'visit-one',kind:'visit',title:'Иван',content:'',tags:[],address:'Улица 1',lat:36.9,lon:30.7,date:'2026-11-08',time:'10:00',timezone:'Europe/Istanbul',task:'Доставка заказа',status:'planned',result:'',...patch});
function fixture(){
  let revision=0,encrypted=null,failWrite=false;const writes=[];
  const request=async(path,options={})=>{
    if(options.method==='PUT'){if(failWrite||options.data.revision!==revision)throw Object.assign(Error('Записи изменились'),{status:409});revision++;encrypted=options.data.record;writes.push(encrypted);return {revision};}
    return {account:'a'.repeat(24),owner:'7',encryption_key:'1'.repeat(64),revision,record:encrypted};
  };
  const book=new Notebook(request,webcrypto);
  const run=async({user,plans,history=[user],model='velia-pro',fastVisitReplies=false})=>runMapAgent({user,history,model,fastVisitReplies,notebook:book,now,signal:new AbortController().signal,lookup:async()=>[point],
    request:async(path,payload)=>{assert.equal(path,'map/plan');assert.equal(payload.model,model);assert.ok(payload.messages[0].content.includes(localDate(now,deviceTimeZone())));const next=plans.shift();assert.ok(next);const plan=typeof next==='function'?next(payload.messages):next;return new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(plan)},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});}});
  return {book,run,writes,reopen:()=>new Notebook(request,webcrypto),fail:()=>failWrite=true};
}
test('relative dates follow the requested timezone through year changes and DST',()=>{
  const instant=Date.parse('2026-12-31T12:30:00Z');
  assert.equal(localDate(instant,'Pacific/Kiritimati'),'2027-01-01');assert.equal(visitDate('tomorrow',{now:instant,zone:'Pacific/Kiritimati'}),'2027-01-02');
  assert.equal(visitDate('tomorrow',{now:instant,zone:'America/Los_Angeles'}),'2027-01-01');
  assert.equal(visitDate('tomorrow',{now:Date.parse('2026-03-08T06:30:00Z'),zone:'America/New_York'}),'2026-03-09');
  assert.equal(visitDate('tomorrow',{now:Date.parse('2026-11-01T05:30:00Z'),zone:'America/New_York'}),'2026-11-02');
});
test('invalid dates, times, statuses and timezones are rejected before storage',()=>{
  for(const value of ['2026-02-29','2026-04-31','2026-1-1','1899-12-31','2200-01-01'])assert.equal(validDate(value),false);
  assert.ok(validDate('2028-02-29'));
  for(const patch of [{time:'24:00'},{time:'9:00'},{timezone:'Not/A_Zone'},{date:'2026-02-30'},{status:'paid'},{task:''}])assert.throws(()=>validateVisit(record(patch)));
  assert.equal(validateVisit(record({time:''})).time,'');
});
test('plans sort known appointment times, retain untimed visits, and flag equal planned slots only',()=>{
  const entries=[record({id:'late',time:'14:00'}),record({id:'untimed',time:''}),record({id:'early',time:'09:00'}),record({id:'done',time:'08:00',status:'done'}),record({id:'cancelled',status:'cancelled'})];
  assert.deepEqual(visitsForDate(entries,'2026-11-08','planned').map(p=>p.id),['early','late','untimed']);
  assert.equal(visitConflicts(record({time:'09:00'}),entries).length,1);assert.equal(visitConflicts(record({time:''}),entries).length,0);assert.equal(visitConflicts(record({time:'09:00',timezone:'UTC'}),entries).length,0);
});
test('visit task, result and timezone reopen from encrypted account storage',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));await f.book.save(record({result:'Оплачено наличными'}));
  assert.ok(!JSON.stringify(f.writes).includes('Оплачено наличными'));const next=f.reopen();await next.unlock('a'.repeat(24));const values=await next.load();assert.equal(values[0].result,'Оплачено наличными');assert.equal(values[0].timezone,'Europe/Istanbul');
});
for(const model of ['velia-flash','velia-quantum','velia-pro'])test(model+' schedules, lists and completes an actual referenced visit without inventing an outcome',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));const user={role:'user',content:'Запланируй визит на завтра в 10:00: доставить заказ',requestId:crypto.randomUUID()};
  const first=await f.run({model,user,plans:[{tool:'search_places',arguments:{query:'Antalya'}},messages=>({tool:'schedule_visit',arguments:{place_id:output(messages)[0].ref,date:'tomorrow',time:'10:00',task:'Доставить заказ'}}),{answer:'Запланировала визит.'}]});
  assert.ok(validMapData(first.data));assert.equal(first.data.visitPlan.rows.length,1);assert.equal(first.data.visitPlan.rows[0].date,visitDate('tomorrow',{now}));assert.equal(first.data.visitPlan.rows[0].result,'');
  const list={role:'user',content:'Собери мои выезды на завтра',requestId:crypto.randomUUID()};
  assert.ok(wantsMap(list.content));const second=await f.run({model,user:list,history:[{role:'assistant',content:first.text,mapData:first.data},list],plans:[{tool:'list_visits',arguments:{date:'tomorrow'}},{answer:'Вот план выездов.'}]});
  const finish={role:'user',content:'Отметь первый визит выполненным. Результат: заказ принят',requestId:crypto.randomUUID()};assert.ok(wantsMap(finish.content,[{role:'assistant',content:second.text,mapData:second.data}]));
  const third=await f.run({model,user:finish,history:[{role:'assistant',content:second.text,mapData:second.data},finish],plans:[{tool:'update_visit',arguments:{visit_id:second.data.visitPlan.rows[0].ref,status:'done',result:'Заказ принят'}},{answer:'Отметила визит выполненным.'}]});
  assert.equal(third.data.visitPlan.rows[0].status,'done');assert.equal((await f.book.load())[0].result,'Заказ принят');assert.equal((await f.book.load())[0].address,point.address);
});
test('invented visit identifiers and unsolicited scheduling cannot mutate the notebook',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));await f.book.save(record());const before=f.writes.length;
  const user={role:'user',content:'Покажи мои визиты',requestId:crypto.randomUUID()};
  await f.run({user,plans:[{tool:'search_places',arguments:{query:'Antalya'}},messages=>({tool:'schedule_visit',arguments:{place_id:output(messages)[0].ref,date:'tomorrow',time:'10:00',task:'Непрошеная задача'}}),messages=>{assert.equal(output(messages).action_completed,false);return {answer:'Вот доступные места.'};}]});
  const edit={role:'user',content:'Отметь визит выполненным',requestId:crypto.randomUUID()};
  await f.run({user:edit,plans:[{tool:'update_visit',arguments:{visit_id:'another-account-or-invented',status:'done'}},messages=>{assert.equal(output(messages).action_completed,false);return {answer:'Выбери визит из плана.'};}]});
  assert.equal(f.writes.length,before);assert.equal((await f.book.load())[0].status,'planned');
});
test('a stale notebook cannot report a successfully saved visit',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));f.fail();const user={role:'user',content:'Запланируй визит завтра в 10:00',requestId:crypto.randomUUID()};
  const result=await f.run({user,plans:[{tool:'search_places',arguments:{query:'Antalya'}},messages=>({tool:'schedule_visit',arguments:{place_id:output(messages)[0].ref,date:'tomorrow',time:'10:00',task:'Доставка'}}),messages=>{assert.equal(output(messages).action_completed,false);return {answer:'Обнови план и повтори.'};}]});
  assert.equal(f.writes.length,0);assert.equal(result.data.visitPlan,null);
});
test('duplicate scheduling leaves one visit, and rescheduling retains its result and location',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));const user={role:'user',content:'Запланируй визит завтра в 10:00',requestId:crypto.randomUUID()};
  const first=await f.run({user,plans:[{tool:'search_places',arguments:{query:'Antalya'}},messages=>({tool:'schedule_visit',arguments:{place_id:output(messages)[0].ref,date:'tomorrow',time:'10:00',task:'Доставка'}}),{answer:'Запланировала.'}]});
  const next={...user,requestId:crypto.randomUUID()};
  await f.run({user:next,history:[{role:'assistant',content:first.text,mapData:first.data},next],plans:[{tool:'schedule_visit',arguments:{place_id:first.data.visitPlan.rows[0].placeRef,date:'tomorrow',time:'10:00',task:'Доставка'}},messages=>{assert.equal(output(messages).already_scheduled,true);return {answer:'Этот визит уже в плане.'};}]});
  assert.equal((await f.book.load()).length,1);
  const update={role:'user',content:'Перенеси первый визит на 2026-11-12 в 14:00',requestId:crypto.randomUUID()};
  await f.run({user:update,history:[{role:'assistant',content:first.text,mapData:first.data},update],plans:[{tool:'update_visit',arguments:{visit_id:first.data.visitPlan.rows[0].ref,date:'2026-11-12',time:'14:00'}},{answer:'Перенесла.'}]});
  const value=(await f.book.load())[0];assert.equal(value.date,'2026-11-12');assert.equal(value.time,'14:00');assert.equal(value.lat,point.lat);assert.equal(value.task,'Доставка');
});
test('map confirmation omits relative UI placement without changing unrelated text',()=>{
  assert.equal(mapReply('Карта с аэропортом Анталии показана выше. Это аэропорт.'),'Карта с аэропортом Анталии показана. Это аэропорт.');
  assert.equal(mapReply('The map is displayed below.'),'The map is displayed.');assert.equal(mapReply('Выше температура, ниже давление.'),'Выше температура, ниже давление.');
});

for(const model of ['velia-flash','velia-quantum','velia-pro'])test(model+' confirms a simple successful write without a second model request',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));const place={...point,ref:'known-point'};
  const user={role:'user',content:'Запланируй визит к этому месту на завтра в 10:00',requestId:crypto.randomUUID()};
  const plans=[{tool:'schedule_visit',arguments:{place_id:place.ref,date:'tomorrow',time:'10:00',task:'Доставить заказ'}}];
  const result=await f.run({model,user,fastVisitReplies:true,history:[{role:'assistant',content:'Выбрано место.',mapData:{version:1,places:[place],display:[place.ref],route:null}},user],plans});
  assert.equal(plans.length,0);assert.ok(result.text.includes('Запланировала'));assert.equal((await f.book.load()).length,1);assert.equal((await f.book.find('Доставить заказ')).length,1);
});
test('composite requests and other languages retain the full planner loop',()=>{
  assert.ok(quickVisitReply('list_visits',{total:2,date:'2026-11-08'},'Собери мои выезды на завтра','ru').includes('2'));
  assert.equal(quickVisitReply('schedule_visit',{saved:true,plan:{date:'2026-11-08'}},'Запланируй два визита','ru'),null);
  assert.equal(quickVisitReply('list_visits',{total:2,date:'2026-11-08'},'Покажи выезды на завтра и проложи маршрут','ru'),null);
  assert.equal(quickVisitReply('list_visits',{total:2,date:'2026-11-08'},'Mes visites de demain','fr'),null);
});

test('simple day reads and explicit completion use actual storage without a model request',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));await f.book.save(record({date:visitDate('tomorrow',{now})}));
  const list={role:'user',content:'Собери мои выезды на завтра',requestId:crypto.randomUUID()};
  const first=await f.run({user:list,plans:[],fastVisitReplies:true});
  assert.equal(first.data.visitPlan.rows[0].status,'planned');
  const finish={role:'user',content:'Отметь первый визит выполненным. Результат: заказ передан',requestId:crypto.randomUUID()};
  const second=await f.run({user:finish,plans:[],fastVisitReplies:true,history:[{role:'assistant',content:first.text,mapData:first.data},finish]});
  assert.equal(second.data.visitPlan.rows[0].status,'done');assert.equal((await f.book.load())[0].result,'заказ передан');
});
test('quick completion does not choose a sole visit when the user names another place or multiple visits',()=>{
  const state={visitPlan:{rows:[{...record(),ref:'v1',visitId:'own-record',task:'Доставка заказа'}]}};
  for(const content of ['Отметь визит к другому клиенту выполненным','Отметь первый визит к Ивану выполненным','Отметь оба визита выполненными','Отметь первый визит выполненным и перенеси второй на завтра']){
    const actions=createVisitActions({state,user:{content},notebook:{},guard(){},findPlace(){},addPlace(){},persist(){},now});
    assert.equal(actions.quickCommand(),null,content);
  }
  assert.equal(quickVisitReply('list_visits',{total:1,date:'2026-11-08'},'Отметь визит выполненным','ru'),null);
});

test('planner formatting variations cannot turn unexecuted text into a success confirmation',()=>{
  assert.deepEqual(parseMapPlan('Сейчас проверю.\n```json\n{"tool":"list_visits","arguments":{"date":"tomorrow"}}\n```'),{tool:'list_visits',arguments:{date:'tomorrow'}});
  assert.deepEqual(parseMapPlan('{"tool":"schedule_visit","arguments":{"task":"text with } and { braces"},"answer":"Уже сохранила"}'),{tool:'schedule_visit',arguments:{task:'text with } and { braces'}});
  assert.throws(()=>parseMapPlan('{"tool":"list_visits","arguments":{}}\n{"tool":"schedule_visit","arguments":{}}'));
  assert.throws(()=>parseMapPlan('Я запланировала визит.'));
  assert.deepEqual(parseMapPlan('{"list_visits":{"date":"tomorrow","status":"planned","visits":[{"title":"не доверять этим выдуманным данным"}],"total":1}}'),{tool:'list_visits',arguments:{date:'tomorrow',status:'planned'}});
  assert.throws(()=>parseMapPlan('{"delete_user":{"user_id":7}}'));
});
test('a long daily plan keeps totals and fits the gateway message limit',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));
  for(let i=0;i<25;i++)await f.book.save(record({id:'many-'+i,title:String(i).padStart(2,'0')+'"'.repeat(198),address:'\\'.repeat(1000),task:'\\'.repeat(2000),result:'"'.repeat(4000),lon:30.7+i/1000,date:visitDate('tomorrow',{now})}));
  const user={role:'user',content:'Собери мои выезды на завтра',requestId:crypto.randomUUID()};
  const result=await f.run({user,plans:[{tool:'list_visits',arguments:{date:'tomorrow'}},messages=>{assert.ok(messages.at(-1).content.length<32000);const value=output(messages);assert.equal(value.total,25);assert.equal(value.shown,20);return {answer:'Показала первые 20 выездов.'};}]});
  assert.equal(result.data.visitPlan.total,25);assert.equal(result.data.visitPlan.rows.length,20);assert.equal(result.data.display.length,12);assert.ok(validMapData(result.data));
});
