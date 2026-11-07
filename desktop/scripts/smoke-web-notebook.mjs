/** Whole-app flow with synthetic notebook storage, map SDK and geocoder. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const {JSDOM}=await import(process.env.VELIA_JSDOM_MODULE||'jsdom');
const dom=new JSDOM(await fs.readFile(new URL('../web/index.html',import.meta.url),'utf8'),{url:'https://velia.example/',pretendToBeVisual:true});
const {window}=dom;
Object.assign(globalThis,{window,document:window.document,localStorage:window.localStorage,sessionStorage:window.sessionStorage,location:window.location,matchMedia:()=>({matches:true}),requestAnimationFrame:window.requestAnimationFrame.bind(window),cancelAnimationFrame:window.cancelAnimationFrame.bind(window),BroadcastChannel:undefined,Event:window.Event});
Object.defineProperty(globalThis,'navigator',{value:window.navigator,configurable:true});
window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
window.HTMLDialogElement.prototype.close=function(){this.open=false;};
const account='a'.repeat(24),profile={ok:true,account,history_cache_key:'b'.repeat(64),language:'ru',credits:10,free_api_tiers:true,background_tasks:true,browser_agent:true,models:['velia-flash','velia-quantum','velia-pro']};
const calls=[];let snapshot={revision:0,record:null},mapClick,mapRemoved=0,mapPlans=0,mapRefs;
window.L={
  map:()=>({setView(){return this;},invalidateSize(){},fitBounds(){return this;},attributionControl:{setPrefix(){}},on(name,action){if(name==='click')mapClick=action;},remove(){mapRemoved++;}}),
  tileLayer:()=>({addTo(){return this;}}),layerGroup:()=>({addTo(){return this;},clearLayers(){}}),divIcon:value=>value,
  marker:()=>({addTo(){return this;},bindPopup(){return this;},openPopup(){return this;}}),
};
globalThis.fetch=async(url,options={})=>{
  const path=String(url).replace('/web-api/v1/',''),data=options.body?JSON.parse(options.body):null;calls.push({path,data});
  if(path==='session')return Response.json(profile);
  if(path==='conversations')return Response.json({ok:true,conversations:[{id:'server-chat',title:'Исходный диалог',updated_at:new Date().toISOString()}]});
  if(path==='conversations/server-chat/messages')return Response.json({ok:true,messages:[{id:'m1',role:'user',content:'Контекст до карты',status:'completed',created_at:new Date().toISOString()},{id:'m2',role:'assistant',content:'Исходный ответ до карты',status:'completed',created_at:new Date().toISOString()}]});
  if(path==='features/chat-topics?conversation_id=server-chat')return Response.json({ok:true,entries:[]});
  if(path==='background-tasks')return Response.json({ok:true,jobs:[]});
  if(path==='features/notebook'){
    if(options.method==='PUT'){
      assert.equal(data.account,account);assert.equal(data.owner,'7');assert.equal(data.revision,snapshot.revision);
      snapshot={revision:snapshot.revision+1,record:data.record};return Response.json({ok:true,revision:snapshot.revision,account,owner:'7'});
    }
    return Response.json({ok:true,...snapshot,account,owner:'7',encryption_key:'1'.repeat(64)});
  }
  if(path==='map/plan'){
    assert.equal(data.model,process.env.VELIA_TEST_CHAT_MODEL||'velia-pro');
    let plan;
    if(mapPlans===0)plan={tool:'search_places',arguments:{query:'Анталия'}};
    if(mapPlans===1){mapRefs=JSON.parse(data.messages.at(-1).content.split('): ')[1]).map(p=>p.ref);plan={answer:'Нашла места на карте.'};}
    if(mapPlans===2)plan={tool:'save_place',arguments:{place_id:mapRefs[1],comment:'Встреча после 15'}};
    if(mapPlans===3)plan={answer:'Сохранила второе место.'};
    if(mapPlans===4)plan={tool:'prepare_route',arguments:{place_ids:mapRefs,mode:'walking'}};
    if(mapPlans===5)plan={answer:'Подготовила ссылку на пеший маршрут.'};
    assert.ok(plan);mapPlans++;
    return new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(plan)},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});
  }
  if(path==='chat/completions'){assert.equal(data.model,process.env.VELIA_TEST_CHAT_MODEL||'velia-pro');return new Response('data: '+JSON.stringify({choices:[{delta:{content:'Обычный ответ: четыре.'},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});}
  if(path==='map/search')return Response.json({ok:true,places:[{title:'Анталия',address:'Türkiye',lat:36.8969,lon:30.7133},{title:'Аэропорт Анталии',address:'Türkiye',lat:36.9,lon:30.8}]});
  return Response.json({ok:false,error:'fixture_unavailable'},{status:404});
};
const wait=async(check)=>{for(let attempt=0;attempt<150;attempt++){if(check())return;await new Promise(resolve=>setTimeout(resolve,20));}throw Error('Fixture timeout');};
const buttons=(parent,text)=>[...parent.querySelectorAll('button')].filter(button=>button.textContent===text);
const send=async(text,answer)=>{document.querySelector('#prompt').value=text;document.querySelector('#composer').dispatchEvent(new window.Event('submit',{cancelable:true}));await wait(()=>!document.querySelector('#prompt').disabled&&document.querySelector('#messages').textContent.includes(answer));};
try{
  await import('../web/app.mjs');
  for(const [index,model] of ['velia-flash','velia-quantum','velia-pro'].entries()){
    document.querySelector('[data-model="'+model+'"]').click();
    await send('Сохрани заметку: Идея доставки '+index,'Сохранила заметку');
  }
  assert.equal(snapshot.revision,3);
  assert.ok(!JSON.stringify(snapshot).includes('Идея доставки'));
  await send('Создай список покупок: хлеб, молоко','Сохранила список');
  await send('Найди мою идею про доставки','Нашла записей: 3');
  const star=document.querySelector('.message.assistant:last-child [aria-label="Сохранить ответ в избранное"]');star.click();await wait(()=>star.textContent==='★');
  document.querySelector('[data-section="notes"]').click();await wait(()=>document.querySelectorAll('.notebook-card').length===5);
  const revision=snapshot.revision,checkbox=document.querySelector('.notebook-check input');checkbox.click();await wait(()=>snapshot.revision===revision+1&&document.querySelector('.notebook-check input')?.checked);
  const kind=document.querySelector('[aria-label="Тип записи"]');kind.value='note';kind.dispatchEvent(new window.Event('change'));
  const search=document.querySelector('[aria-label="Поиск заметок"]');search.value='Идея доставки';search.dispatchEvent(new window.Event('input'));await wait(()=>document.querySelectorAll('.notebook-card').length===3);
  buttons(document,'К диалогу')[0].click();
  await wait(()=>[...document.querySelectorAll('.history-open')].some(b=>b.textContent==='Исходный диалог'));[...document.querySelectorAll('.history-open')].find(b=>b.textContent==='Исходный диалог').click();await wait(()=>document.querySelector('#messages').textContent.includes('Исходный ответ до карты'));
  document.querySelector('[data-model="'+(process.env.VELIA_TEST_CHAT_MODEL||'velia-pro')+'"]').click();
  await send('Найди на карте Анталию','Нашла места на карте');await wait(()=>document.querySelector('.chat-map-result'));
  await send('Сохрани второе место с комментарием: встреча после 15','Сохранила второе место');
  await send('Проложи маршрут между ними пешком','Подготовила ссылку');
  const nav=[...document.querySelectorAll('.chat-map-result a')].find(a=>a.textContent==='Открыть маршрут в навигаторе');assert.equal(new URL(nav.href).searchParams.get('travelmode'),'walking');
  assert.ok(new URL(nav.href).searchParams.get('destination').includes('30.8'));
  await send('Сколько будет два плюс два?','Обычный ответ: четыре');assert.equal(mapPlans,6);assert.ok(document.querySelector('#messages').textContent.includes('Исходный ответ до карты'));const normal=calls.find(call=>call.path==='chat/completions');assert.ok(normal.data.messages.some(m=>m.content.includes('Исходный ответ до карты')));assert.ok(normal.data.messages.some(m=>m.content.includes('Аэропорт Анталии')));
  buttons(document.querySelector('.chat-map-result'),'Мои места')[0].click();await wait(()=>document.querySelector('.map-toolbar'));
  document.querySelector('[aria-label="Найти на карте"]').value='Анталия';document.querySelector('.map-toolbar').dispatchEvent(new window.Event('submit',{cancelable:true}));await wait(()=>document.querySelector('.map-results')?.textContent.includes('Анталия'));
  buttons(document.querySelector('.map-toolbar'),'Выбрать точку')[0].click();mapClick({latlng:{lat:36.9,lng:30.7}});
  await wait(()=>document.querySelector('.notebook-editor'));
  const editor=document.querySelector('.notebook-editor');editor.querySelector('input').value='Клиент Анталия';editor.dispatchEvent(new window.Event('submit',{cancelable:true}));
  await wait(()=>document.querySelector('.feature-body')?.textContent.includes('Клиент Анталия'));
  const route=[...document.querySelectorAll('a')].find(link=>link.textContent==='Маршрут в навигаторе');assert.equal(new URL(route.href).origin,'https://www.google.com');
  document.querySelector('[data-section="notes"]').click();await wait(()=>document.querySelectorAll('.notebook-card').length===7);assert.ok(mapRemoved>0);
  assert.ok(!calls.some(call=>call.path!=='map/plan'&&call.path!=='chat/completions'&&/\/plan$|agent\/browser/.test(call.path)));assert.equal(calls.filter(call=>call.path==='chat/completions').length,1);
  assert.equal(calls.filter(call=>call.path==='map/search').length,2);
  console.log(JSON.stringify({ok:true,synthetic:true,checks:['notes saved from Flash, Quantum and PRO without model requests','ciphertext sent to storage','list and favorite in notebook','search own notes','map tools run through selected model from chat','follow-up saves second geocoder result with comment','requested walking route uses actual coordinates','server chat context retained in browser continuation','unrelated question returns to normal chat with actual map context','selected coordinate saved as a place','external navigation','map cleanup on section change']}));
}catch(error){console.error(error);process.exitCode=1;}
finally{dom.window.close();setTimeout(()=>process.exit(process.exitCode||0),10);}
