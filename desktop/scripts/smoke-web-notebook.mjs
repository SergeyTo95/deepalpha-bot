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
const materials=new Map();globalThis.confirm=()=>true;
const calls=[];let snapshot={revision:0,record:null},mapClick,mapRemoved=0,mapPlans=0,mapRefs,visitRef;
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
  if(path.startsWith('features/notebook/materials/')){const id=path.split('/').at(-1);if(options.method==='PUT'){materials.set(id,data.record);return Response.json({ok:true,owner:'7',saved:true});}if(options.method==='DELETE'){materials.delete(id);return Response.json({ok:true,owner:'7',deleted:true});}return Response.json({ok:true,owner:'7',record:materials.get(id)});}
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
    if(mapPlans===1){mapRefs=JSON.parse(data.messages.at(-1).content.split('): ')[1]).map(p=>p.ref);plan={tool:'show_places',arguments:{place_ids:mapRefs}};}
    if(mapPlans===2)plan={answer:'Нашла места на карте.'};
    if(mapPlans===3)plan={tool:'save_place',arguments:{place_id:mapRefs[1],comment:'Встреча после 15'}};
    if(mapPlans===4)plan={answer:'Сохранила второе место.'};
    if(mapPlans===5)plan={tool:'prepare_route',arguments:{place_ids:mapRefs,mode:'walking'}};
    if(mapPlans===6)plan={answer:'Подготовила ссылку на пеший маршрут.'};
    if(mapPlans===7)plan={tool:'schedule_visit',arguments:{place_id:mapRefs[1],date:'tomorrow',time:'10:00',task:'Передать заказ'}};
    if(mapPlans===8)plan={tool:'add_place_note',arguments:{place_id:mapRefs[1],text:'Оплата по пятницам',type:'agreement'}};
    if(mapPlans===9)plan={answer:'Договорённость записана.'};
    if(mapPlans===10)plan={tool:'attach_place_materials',arguments:{place_id:mapRefs[1],caption:'Акт передачи'}};
    if(mapPlans===11)plan={answer:'Файл сохранён у места.'};
    if(mapPlans===12)plan={tool:'get_place_history',arguments:{place_id:mapRefs[1]}};
    if(mapPlans===13)plan={answer:'Оплата по пятницам, заказ передан.'};
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
  await send('Сколько будет два плюс два?','Обычный ответ: четыре');assert.equal(mapPlans,7);assert.ok(document.querySelector('#messages').textContent.includes('Исходный ответ до карты'));const normal=calls.find(call=>call.path==='chat/completions');assert.ok(normal.data.messages.some(m=>m.content.includes('Исходный ответ до карты')));assert.ok(normal.data.messages.some(m=>m.content.includes('Аэропорт Анталии')));
  await send('Запланируй визит к этому месту на завтра в 10:00: передать заказ','Запланировала визит');
  await send('Собери мои выезды на завтра','Выездов на ');
  assert.ok(document.querySelector('.chat-visit-list').textContent.includes('Передать заказ'));
  await send('Отметь первый визит выполненным. Результат: заказ передан','Отметила визит выполненным');assert.equal(mapPlans,8);
  await send('Составь сообщение клиенту об итогах','Обычный ответ: четыре');
  assert.ok(calls.filter(call=>call.path==='chat/completions').at(-1).data.messages.some(m=>m.content.toLocaleLowerCase().includes('заказ передан')));
  const lastPlan=[...document.querySelectorAll('.chat-map-result')].findLast(card=>buttons(card,'Открыть план').length);buttons(lastPlan,'Открыть план')[0].click();await wait(()=>document.querySelector('.visit-card'));
  assert.ok(document.querySelector('.visit-card').textContent.includes('Выполнен'));assert.ok(document.querySelector('.visit-card').textContent.toLocaleLowerCase().includes('заказ передан'));
  buttons(document.querySelector('.visit-card'),'Изменить')[0].click();const visitEditor=document.querySelector('.visit-editor');visitEditor.querySelector('input[type="time"]').value='11:30';visitEditor.dispatchEvent(new window.Event('submit',{cancelable:true}));await wait(()=>document.querySelector('.visit-card')?.textContent.includes('11:30'));
  buttons(document,'Места')[0].click();await wait(()=>document.querySelector('.map-toolbar'));
  document.querySelector('[aria-label="Найти на карте"]').value='Анталия';document.querySelector('.map-toolbar').dispatchEvent(new window.Event('submit',{cancelable:true}));await wait(()=>document.querySelector('.map-results')?.textContent.includes('Анталия'));
  buttons(document.querySelector('.map-toolbar'),'Выбрать точку')[0].click();mapClick({latlng:{lat:36.9,lng:30.7}});
  await wait(()=>document.querySelector('.notebook-editor'));
  const editor=document.querySelector('.notebook-editor');editor.querySelector('input').value='Клиент Анталия';editor.dispatchEvent(new window.Event('submit',{cancelable:true}));
  await wait(()=>document.querySelector('.feature-body')?.textContent.includes('Клиент Анталия'));
  const route=[...document.querySelectorAll('a')].find(link=>link.textContent==='Маршрут в навигаторе');assert.equal(new URL(route.href).origin,'https://www.google.com');
  document.querySelector('[data-section="notes"]').click();await wait(()=>document.querySelectorAll('.notebook-card').length===8);assert.ok(mapRemoved>0);
  assert.ok(!calls.some(call=>call.path!=='map/plan'&&call.path!=='chat/completions'&&/\/plan$|agent\/browser/.test(call.path)));assert.equal(calls.filter(call=>call.path==='chat/completions').length,2);
  assert.equal(calls.filter(call=>call.path==='map/search').length,2);

  buttons(document,'К диалогу')[0].click();
  await send('Запиши договорённость по этому месту: оплата по пятницам','Договорённость записана');
  const input=document.querySelector('#attachment-input');Object.defineProperty(input,'files',{value:[new File(['original private act'],'act.txt',{type:'text/plain'})],configurable:true});input.dispatchEvent(new window.Event('change'));await wait(()=>document.querySelector('.file-chip'));
  await send('Прикрепи эти файлы к этому месту','Файл сохранён у места');assert.equal(materials.size,1);assert.ok(!JSON.stringify([...materials]).includes('original private act'));
  await send('Покажи историю этого места','Показала историю места');assert.equal(mapPlans,12);
  const historyCard=[...document.querySelectorAll('.chat-map-result')].at(-1);buttons(historyCard,'История и материалы')[0].click();await wait(()=>document.querySelector('.place-card'));
  assert.ok(document.querySelector('.place-card').textContent.toLocaleLowerCase().includes('заказ передан'));assert.ok(document.querySelector('.place-card').textContent.includes('Оплата по пятницам'));
  const entry=document.querySelector('[aria-label="Запись по месту"]');entry.value='Вход со двора';entry.closest('form').dispatchEvent(new window.Event('submit',{cancelable:true}));await wait(()=>document.querySelector('.place-timeline').textContent.includes('Вход со двора'));
  const material=[...document.querySelectorAll('.place-history-item')].find(card=>card.textContent.includes('act.txt'));buttons(material,'Открыть')[0].click();await wait(()=>document.querySelector('.file-view-body').textContent.includes('original private act'));
  buttons(document.querySelector('.file-dialog'),'Закрыть')[0].click();buttons(material,'Удалить')[0].click();await wait(()=>materials.size===0&&!document.querySelector('.place-timeline').textContent.includes('act.txt'));
  console.log(JSON.stringify({ok:true,synthetic:true,checks:['notes saved from Flash, Quantum and PRO without model requests','ciphertext sent to storage','list and favorite in notebook','search own notes','map tools run through selected model from chat','follow-up saves second geocoder result with comment','requested walking route uses actual coordinates','server chat context retained in browser continuation','unrelated question returns to normal chat with actual map context','selected coordinate saved as a place','external navigation','visit scheduled and listed from chat','referenced visit completed with result','visit plan opens inline and from My places','manual visit time edit persists','map cleanup on section change','place agreements and history in main chat','original file encrypted, reopened and deleted from place card','visit results and place notes share one card']}));
}catch(error){console.error(error);console.error(JSON.stringify({mapPlans,lastMessage:document.querySelector('#messages')?.lastElementChild?.textContent}));process.exitCode=1;}
finally{dom.window.close();setTimeout(()=>process.exit(process.exitCode||0),10);}
