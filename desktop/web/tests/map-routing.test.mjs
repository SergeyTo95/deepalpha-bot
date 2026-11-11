import test from 'node:test';
import assert from 'node:assert/strict';
import {validRoadRoute,requestRoadRoute,roadRouteText} from '../map-routing.mjs';
import {runMapAgent,validMapData,mapMessageContext} from '../map-agent.mjs';

const places=Array.from({length:4},(_,i)=>({ref:'p'+i,title:'Место '+i,address:'Анталия',lon:30.7+i/10,lat:36.9+i/100}));
function road(optimize=false){return {provider:'openrouteservice',mode:'driving',input_coordinates:places.map(p=>[p.lon,p.lat]),order:optimize?[0,2,1,3]:[0,1,2,3],distance_m:12000,duration_s:1800,geometry:places.map(p=>[p.lon,p.lat]),legs:places.slice(1).map(()=>({distance_m:4000,duration_s:600})),traffic:false,optimized:optimize,calculated_at:1794400000,license:'CC-BY-SA-4.0'};}
const stream=plan=>new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(plan)},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});

test('strict provider result binds coordinates, endpoints and estimates without traffic claims',()=>{
  assert.ok(validRoadRoute(road(),places));assert.ok(validRoadRoute(road(true),places));
  for(const change of [{order:[3,2,1,0]},{order:[0,1,1,3]},{traffic:true},{duration_s:NaN},{geometry:[[900,0],[30,0]]}])assert.equal(validRoadRoute({...road(),...change},places),false);
  const mismatch=structuredClone(places);mismatch[0].lon=0;assert.equal(validRoadRoute(road(),mismatch),false);
  assert.match(roadRouteText(road()),/без пробок и времени на остановки/);assert.match(roadRouteText(road()),/12.0 км/);
});

test('route HTTP body contains only coordinates, mode and explicit optimize flag',async()=>{
  const previous=globalThis.fetch;
  try{
    globalThis.fetch=async(url,options)=>{assert.equal(url,'/web-api/v1/map/route');assert.equal(options.credentials,'same-origin');assert.deepEqual(JSON.parse(options.body),{coordinates:places.map(p=>[p.lon,p.lat]),mode:'driving',optimize:false});return Response.json({ok:true,route:road()});};
    assert.equal((await requestRoadRoute(places,'driving',false,new AbortController().signal)).traffic,false);
    globalThis.fetch=async()=>Response.json({ok:false,error:'route_key_required'},{status:503});
    await assert.rejects(requestRoadRoute(places,'driving',false,new AbortController().signal),/бесплатный ключ/);
  }finally{globalThis.fetch=previous;}
});

async function run({optimize=false,text='Построй маршрут между местами',routeLookup=async()=>road(optimize)}){
  const user={role:'user',content:text,requestId:'user-owned'};let calls=0;
  const notebook={account:'owned',epoch:1};
  return runMapAgent({model:'velia-flash',user,notebook,signal:new AbortController().signal,routeLookup,
    history:[{role:'assistant',content:'Выбраны места',mapData:{version:1,places,display:places.map(p=>p.ref),route:null}},user],
    request:async(path,payload)=>{calls++;assert.equal(calls,1);assert.equal(path,'map/plan');assert.equal(payload.web_search,false);return stream({tool:'prepare_route',arguments:{place_ids:places.map(p=>p.ref),mode:'driving',optimize}});}});
}

test('map agent returns verified route directly, preserves fixed endpoints and stores provenance',async()=>{
  const result=await run({optimize:true,text:'Оптимизируй маршрут между местами'});
  assert.deepEqual(result.data.route.ids,['p0','p2','p1','p3']);assert.ok(validMapData(result.data));assert.match(result.text,/без пробок/);
  const context=mapMessageContext({role:'assistant',content:result.text,mapData:result.data});assert.match(context,/"traffic":false/);assert.doesNotMatch(context,/"geometry"/);
  const changed=structuredClone(result.data);changed.route.road.input_coordinates[0][0]=0;assert.equal(validMapData(changed),false);
});

test('missing free key or exhausted provider quota preserves original-order navigator fallback',async()=>{
  const result=await run({optimize:true,text:'Оптимизируй маршрут между местами',routeLookup:async()=>{throw Error('Лимит бесплатного сервиса исчерпан.');}});
  assert.equal(result.data.route.road,undefined);assert.deepEqual(result.data.route.ids,['p0','p1','p2','p3']);assert.match(result.text,/порядок точек сохранён/);assert.match(result.text,/навигатор/);
  assert.ok(validMapData(result.data));
});

test('routing never silently reorders on model initiative and abort does not publish results',async()=>{
  let attempts=0;
  const result=await run({optimize:true,routeLookup:async()=>{attempts++;return road(true);}});
  assert.match(result.text,/нужна просьба оптимизировать/);assert.equal(result.data.route,null);
  assert.equal(attempts,0);
  const read=await run({text:'Покажи места',routeLookup:async()=>{attempts++;return road();}});assert.match(read.text,/явная просьба/);assert.equal(attempts,0);
  await assert.rejects(run({routeLookup:async()=>{throw new DOMException('Stopped','AbortError');}}),{name:'AbortError'});
});
