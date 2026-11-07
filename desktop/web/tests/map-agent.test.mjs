import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {Notebook} from '../notebook.mjs';
import {wantsMap,runMapAgent,routeUrl,validMapData,mapMessageContext} from '../map-agent.mjs';
import {chatPayload} from '../core.mjs';
const places=[{title:'Клиент А',address:'Анталия, адрес 1',lat:36.9,lon:30.7},{title:'Клиент Б',address:'Анталия, адрес 2',lat:36.8,lon:30.6}];
const output=messages=>JSON.parse(messages.at(-1).content.split('): ')[1]);
function fixture(){
  let revision=0,record=null;const writes=[];
  const book=new Notebook(async(path,options={})=>{
    if(options.method==='PUT'){assert.equal(options.data.revision,revision);revision++;record=options.data.record;writes.push(record);return {revision};}
    return {account:'a'.repeat(24),owner:'7',encryption_key:'1'.repeat(64),revision,record};
  },webcrypto);
  const run=async({plans,user,history=[user],model='velia-pro',lookup=async()=>places,...extra})=>{
    const calls=[];
    const value=await runMapAgent({model,user,history,notebook:book,signal:new AbortController().signal,lookup,progress:()=>{},...extra,
      request:async(path,payload)=>{assert.equal(path,'map/plan');assert.equal(payload.model,model);assert.equal(payload.web_search,false);calls.push(payload);const next=plans.shift();assert.ok(next,'no unexpected model calls');const plan=typeof next==='function'?next(payload.messages):next;return new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(plan)},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});}});
    assert.equal(plans.length,0);return {...value,calls};
  };
  return {book,run,writes};
}
test('geographic tasks and concrete follow-ups route automatically; unrelated chat leaves maps',()=>{
  assert.ok(wantsMap('Мне нужно съездить по адресам клиентов'));
  assert.ok(wantsMap('Как добраться до аэропорта Анталии?'));
  assert.ok(wantsMap('Найди кафе в Анталии на карте'));
  assert.ok(wantsMap('Сохрани второе место',[{role:'assistant',content:'Найдено',mapData:{}}]));
  assert.equal(wantsMap('Теперь объясни теорему Пифагора',[{role:'assistant',content:'Найдено',mapData:{}}]),false);
  assert.equal(wantsMap('Что такое географическая карта?'),false);
  assert.equal(wantsMap('Составь список покупок'),false);
  assert.ok(wantsMap('Да, сохрани',[{role:'assistant',content:'Выбрано место',mapData:{}}]));
  assert.equal(wantsMap('Покажи все новости',[{role:'assistant',content:'Найдено',mapData:{}}]),false);
});
for(const model of ['velia-flash','velia-quantum','velia-pro'])test(model+' finds real places, shows them, saves a referred point and edits its comment',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));const user={role:'user',content:'Найди адреса клиентов на карте',requestId:crypto.randomUUID()};
  const first=await f.run({model,user,plans:[{tool:'search_places',arguments:{query:'Анталия'}},messages=>({tool:'show_places',arguments:{place_ids:output(messages).map(p=>p.ref)}}),{answer:'Нашла два места.'}]});
  assert.ok(validMapData(first.data));assert.equal(first.data.display.length,2);assert.equal(f.writes.length,0);
  const secondUser={role:'user',content:'Сохрани второе место с комментарием: доставка после 12',requestId:crypto.randomUUID()};
  const second=await f.run({model,user:secondUser,history:[user,{role:'assistant',content:first.text,mapData:first.data},secondUser],plans:[{tool:'save_place',arguments:{place_id:first.data.places[1].ref,comment:'Доставка после 12'}},{answer:'Сохранила второе место.'}]});
  let entries=await f.book.load();assert.equal(entries.length,1);assert.equal(entries[0].lat,36.8);assert.equal(entries[0].content,'Доставка после 12');assert.ok(!JSON.stringify(f.writes).includes('Доставка после 12'));
  const edit={role:'user',content:'Измени комментарий у этого места: доставка после 15',requestId:crypto.randomUUID()};
  await f.run({model,user:edit,history:[{role:'assistant',content:second.text,mapData:second.data},edit],plans:[{tool:'update_place',arguments:{place_id:first.data.places[1].ref,comment:'Доставка после 15'}},{answer:'Обновила комментарий.'}]});
  entries=await f.book.load();assert.equal(entries[0].content,'Доставка после 15');assert.equal(entries[0].title,'Клиент Б');
});
test('invented identifiers and unsolicited writes are rejected even when the planner asks',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));const user={role:'user',content:'Покажи место на карте',requestId:crypto.randomUUID()};
  await f.run({user,plans:[{tool:'search_places',arguments:{query:'Анталия'}},messages=>({tool:'save_place',arguments:{place_id:output(messages)[0].ref}}),messages=>{assert.equal(output(messages).action_completed,false);return {tool:'show_places',arguments:{place_ids:['invented']}};},messages=>{assert.equal(output(messages).action_completed,false);return {answer:'Уточни нужное место.'};}]});
  assert.equal((await f.book.load()).length,0);assert.equal(f.writes.length,0);
});
test('several requested saves work in one turn and do not expose all notebook records',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));await f.book.save({kind:'note',title:'Личное',content:'Не отправлять модели',tags:[]});
  const user={role:'user',content:'Найди и сохрани оба адреса',requestId:crypto.randomUUID()};let refs;
  const result=await f.run({user,plans:[{tool:'search_places',arguments:{query:'Анталия'}},messages=>{refs=output(messages).map(p=>p.ref);return {tool:'save_place',arguments:{place_id:refs[0]}};},()=>({tool:'save_place',arguments:{place_id:refs[1]}}),{tool:'list_places',arguments:{}},messages=>{assert.equal(output(messages).length,2);return {answer:'Сохранила оба адреса.'};}]});
  assert.equal((await f.book.load()).filter(p=>p.kind==='place').length,2);assert.ok(!JSON.stringify(result.calls).includes('Не отправлять модели'));
});
test('account change during lookup aborts before publishing or saving',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));const user={role:'user',content:'Найди и сохрани адрес',requestId:crypto.randomUUID()};let published=0;
  await assert.rejects(f.run({user,plans:[{tool:'search_places',arguments:{query:'Анталия'}}],lookup:async()=>{f.book.lock();return places;},onData:()=>published++}),{name:'AbortError'});
  assert.equal(published,0);assert.equal(f.writes.length,0);
});
test('route links retain the exact requested order, mode and mobile waypoint limit',()=>{
  const points=[...places,{title:'Третий',lat:37,lon:31}];const link=new URL(routeUrl(points,'walking'));
  assert.equal(link.origin,'https://www.google.com');assert.equal(link.searchParams.get('origin'),'36.9,30.7');assert.equal(link.searchParams.get('destination'),'37,31');assert.equal(link.searchParams.get('waypoints'),'36.8,30.6');assert.equal(link.searchParams.get('travelmode'),'walking');
  assert.throws(()=>routeUrl(Array(6).fill(points[0])));assert.throws(()=>routeUrl(points,'fake'));
});

test('normal follow-up gets only displayed map locations, never the full notebook or internal references',()=>{
  const mapData={version:1,places:places.map((p,i)=>({...p,ref:'internal-ref-'+i})),display:['internal-ref-1'],route:null};
  const chat={messages:[{role:'assistant',content:'Выбрала место',mapData},{role:'user',content:'Составь письмо клиенту по этому адресу'}]};
  const payload=chatPayload(chat,'velia-pro',{messageContext:mapMessageContext});
  assert.ok(payload.messages[0].content.includes('Клиент Б'));assert.ok(!payload.messages[0].content.includes('Клиент А'));
  assert.ok(!JSON.stringify(payload).includes('internal-ref'));assert.equal(payload.messages[1].content,chat.messages[1].content);
});

test('failed or irrelevant searches do not automatically put candidates on the map',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));const user={role:'user',content:'Найди аэропорт Анталии и покажи на карте',requestId:crypto.randomUUID()};
  const wrong=await f.run({user,lookup:async(query,signal,filters)=>{assert.equal(filters.category,'airport');return [{title:'Анталья Донер',address:'Беларусь',lat:53.6,lon:23.8,country_code:'BY',category:'restaurant'}];},plans:[{tool:'search_places',arguments:{query:'Анталья'}},{answer:'Повторю поиск по точному названию.'}]});
  assert.equal(wrong.data.places.length,0);assert.equal(wrong.data.display.length,0);
});

test('country/type-constrained retry finds an airport and displays only selected matching results',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));const user={role:'user',content:'Найди аэропорт Анталии и покажи на карте',requestId:crypto.randomUUID()};let calls=0;
  const result=await f.run({user,lookup:async(query,signal,filters)=>{calls++;assert.equal(filters.category,'airport');assert.equal(filters.country_code,'TR');return query==='Antalya Airport'?[{title:'Antalya Airport',address:'Antalya, Türkiye',lat:36.8999425,lon:30.7981914,category:'airport',country_code:'TR'}]:[];},plans:[{tool:'search_places',arguments:{query:'аэропорт Анталии',country_code:'TR'}},messages=>{assert.deepEqual(output(messages),[]);return {tool:'search_places',arguments:{query:'Antalya Airport',country_code:'TR'}};},messages=>({tool:'show_places',arguments:{place_ids:[output(messages)[0].ref]}}),{answer:'Нашла аэропорт Анталии.'}]});
  assert.equal(calls,2);assert.equal(result.data.display.length,1);assert.equal(result.data.places[0].category,'airport');assert.ok(!result.data.places.some(p=>p.country_code!=='TR'));
});
