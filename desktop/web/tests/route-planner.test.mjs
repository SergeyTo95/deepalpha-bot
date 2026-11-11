import test from 'node:test';
import assert from 'node:assert/strict';
import {directRouteStops,runDirectRoute} from '../route-planner.mjs';
import {runMapAgent,validMapData} from '../map-agent.mjs';

const input='Построй автомобильный маршрут от ворот Адриана до аэропорта Анталии. Покажи расстояние и время в пути.';
const gate={title:"Hadrian's Gate",address:'Muratpaşa, Antalya, Turkey',lat:36.8852646,lon:30.7085481,country_code:'TR',category:''};
const wrong={...gate,title:'Hadrian Gate Hotel',lat:36.88479,lon:30.70784,category:'hotel'};
const airport={title:'Antalya Airport',address:'Aksu, Antalya, Turkey',lat:36.89994,lon:30.79819,country_code:'TR',category:'airport'};
const searchPlan={stops:[{query:"Hadrian's Gate Antalya",alternative:'Hadrian Kapısı Antalya',country_code:'TR'},{query:'Antalya Airport',alternative:'Antalya Havalimanı',country_code:'TR'}]};
const stream=(value,finish='stop')=>new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(value)},finish_reason:finish}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});
const road=points=>({provider:'openrouteservice',mode:'driving',input_coordinates:points.map(p=>[p.lon,p.lat]),order:[0,1],distance_m:15000,duration_s:1500,geometry:points.map(p=>[p.lon,p.lat]),legs:[{distance_m:15000,duration_s:1500}],traffic:false,optimized:false,calculated_at:1794400000,license:'CC-BY-SA-4.0'});

test('direct route parsing isolates named endpoints without swallowing writes, waypoints or ordinal followups',()=>{
  assert.deepEqual(directRouteStops(input),{stops:['ворот Адриана','аэропорта Анталии'],mode:'driving'});
  assert.equal(directRouteStops('Walking directions from Museum A to Airport B.').mode,'walking');
  for(const text of ['Сохрани место и построй маршрут от АА до ББ','Построй маршрут от АА до ББ через ВВ','Построй маршрут от первого до второго','Назначь встречу с 10 до 11','Построй маршрут между местами'])assert.equal(directRouteStops(text),null,text);
});

for(const model of ['velia-flash','velia-quantum','velia-pro'])test('reported route request reaches the routing service after two bounded model calls: '+model,async()=>{
  let calls=0,routes=0;const lookups=[];const user={role:'user',content:input,requestId:'request-route'};
  const result=await runMapAgent({model,user,history:[user],notebook:{account:'owned',epoch:1},signal:new AbortController().signal,
    request:async(path,payload)=>{assert.equal(path,'map/plan');assert.equal(payload.model,model);assert.equal(payload.web_search,false);assert.ok(!payload.messages[0].content.includes('list_visits'));return stream(++calls===1?searchPlan:{ids:['s0c1','s1c0']});},
    lookup:async(query,signal,filters)=>{lookups.push({query,filters});return /Airport/u.test(query)?[airport]:[wrong,gate];},
    routeLookup:async(points,mode,optimize)=>{routes++;assert.equal(mode,'driving');assert.equal(optimize,false);assert.equal(points[0].title,gate.title);assert.equal(points[1].title,airport.title);return road(points);}});
  assert.equal(calls,2);assert.equal(routes,1);assert.equal(lookups.length,2);
  assert.equal(lookups[0].filters.category,'');assert.equal(lookups[1].filters.category,'airport');
  assert.ok(validMapData(result.data));assert.match(result.text,/15.0 км/);assert.match(result.text,/без пробок/);
});

async function fixture({answers=[searchPlan,{ids:['s0c0','s1c0']}],lookup=async q=>/Airport|Havaliman/u.test(q)?[airport]:[gate],routeLookup=async points=>road(points),requestText=input}={}){
  let calls=0,searches=0,routes=0;const state={version:1,places:[],display:[],route:null};
  const result=await runDirectRoute({text:requestText,model:'velia-flash',state,signal:new AbortController().signal,guard:()=>{},publish:()=>{},categoryFor:text=>/аэропорт/iu.test(text)?'airport':'',
    request:async()=>{const value=answers[calls++];assert.notEqual(value,undefined,'No unbounded planner loops');return stream(value);},
    lookup:async(...args)=>{searches++;return lookup(...args);},routeLookup:async(...args)=>{routes++;return routeLookup(...args);},
    addPlace:p=>{const place={...p,ref:'p'+state.places.length};state.places.push(place);return place;}});
  return {result,calls,searches,routes};
}

test('empty searches use a different-language fallback once and missing places do not claim a finished route',async()=>{
  const done=await fixture({lookup:async q=>/Airport/u.test(q)?[airport]:/Kapısı/u.test(q)?[gate]:[]});assert.equal(done.searches,3);assert.equal(done.routes,1);
  const missing=await fixture({answers:[searchPlan],lookup:async()=>[]});assert.equal(missing.searches,4);assert.equal(missing.calls,1);assert.equal(missing.routes,0);
  assert.match(missing.result.text,/Не удалось найти точное место/);assert.doesNotMatch(missing.result.text,/предел действий|сохранены/);assert.equal(missing.result.data.route,null);
});

test('ambiguous candidates retry at most once and never guess or call routing',async()=>{
  const value=await fixture({answers:[searchPlan,{ids:[null,'s1c0']},{ids:[null,'s1c0']}]});
  assert.equal(value.calls,3);assert.equal(value.searches,3);assert.equal(value.routes,0);assert.match(value.result.text,/Уточни точный адрес/);
});

test('fabricated IDs, changed account and truncated output stop before provider routing',async()=>{
  await assert.rejects(fixture({answers:[searchPlan,{ids:['invented','s1c0']}]}),/нет в результатах/);
  let calls=0;
  const user={role:'user',content:input,requestId:'owned'},notebook={account:'owned',epoch:1};
  await assert.rejects(runMapAgent({model:'velia-flash',user,history:[user],notebook,signal:new AbortController().signal,
    lookup:async()=>{throw Error('must not search after account change');},request:async()=>{calls++;notebook.epoch++;return stream(searchPlan);}}),{name:'AbortError'});
  assert.equal(calls,1);
  await assert.rejects(runMapAgent({model:'velia-flash',user,history:[user],notebook:{account:'owned',epoch:1},signal:new AbortController().signal,
    lookup:async()=>{throw Error('must not search after truncated extraction');},request:async()=>stream(searchPlan,'length')}),/Выбор точек прервался/);
});

test('one JSON object surrounded by model prose is recoverable but several objects are rejected',async()=>{
  let calls=0;const user={role:'user',content:input,requestId:'owned'};
  const result=await runMapAgent({model:'velia-flash',user,history:[user],notebook:{account:'owned',epoch:1},signal:new AbortController().signal,
    lookup:async q=>/Airport/u.test(q)?[airport]:[gate],routeLookup:async points=>road(points),
    request:async()=>{const text='Результат:\n'+JSON.stringify(++calls===1?searchPlan:{ids:['s0c0','s1c0']});return new Response('data: '+JSON.stringify({choices:[{delta:{content:text},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});}});
  assert.equal(calls,2);assert.ok(result.data.route.road);
  await assert.rejects(runMapAgent({model:'velia-flash',user,history:[user],notebook:{account:'owned',epoch:1},signal:new AbortController().signal,
    lookup:async()=>{throw Error('must not search');},request:async()=>new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(searchPlan)+'\n'+JSON.stringify(searchPlan)},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}})}),/Не удалось разобрать/);
});

test('route-provider error returns actual selected points and navigator handoff with no invented metrics',async()=>{
  const value=await fixture({routeLookup:async()=>{throw Error('Бесплатный сервис недоступен.');}});
  assert.equal(value.routes,1);assert.equal(value.result.data.route.road,undefined);assert.equal(value.result.data.route.ids.length,2);assert.match(value.result.text,/навигатор/);assert.doesNotMatch(value.result.text,/км|\d+ мин/);
});

test('named route preserves pins but never publishes raw HTML or JSON parsing errors',async()=>{
  const value=await fixture({routeLookup:async()=>{throw SyntaxError('Unexpected token < <!DOCTYPE private detail');}});
  assert.match(value.result.text,/Расчёт дорог сейчас недоступен/);
  assert.doesNotMatch(JSON.stringify(value.result),/Unexpected|DOCTYPE|private detail/);
  assert.equal(value.result.data.route.road,undefined);assert.equal(value.result.data.route.ids.length,2);
});

test('repeating the same generic place search stops before another lookup or ten model calls',async()=>{
  let calls=0,lookups=0;const user={role:'user',content:'Найди кафе в Анталии',requestId:'owned'};
  const result=await runMapAgent({model:'velia-flash',user,history:[user],notebook:{account:'owned',epoch:1},signal:new AbortController().signal,
    lookup:async()=>{lookups++;return [];},request:async()=>{calls++;return stream({tool:'search_places',arguments:{query:'cafes Antalya',category:'cafe',country_code:'TR'}});}});
  assert.equal(calls,2);assert.equal(lookups,1);assert.match(result.text,/Поиск повторился/);assert.doesNotMatch(result.text,/сохранены/);
});
