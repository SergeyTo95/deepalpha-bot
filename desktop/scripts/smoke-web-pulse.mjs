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
let pulseItems=[],pulseNotices=[],pulsePrefs={timezone:'UTC',quiet:true,quiet_start:'22:00',quiet_end:'08:00',telegram:false,preview:false,digest:false,digest_time:'09:00'};
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
  if(path==='features/pulse'){
    if(options.method==='POST'){const due_at=Math.floor(Date.now()/1000)+86400,item={...data,item_id:data.id,state:'active',revision:1,due_at,action_at:due_at,notify_at:due_at,created_at:Math.floor(Date.now()/1000)};pulseItems.push(item);return Response.json({ok:true,account,item});}
    return Response.json({ok:true,account,items:pulseItems,notifications:pulseNotices,settings:pulsePrefs,worker_enabled:true,worker_last_tick:Math.floor(Date.now()/1000),telegram_available:false,telegram_username:'VelyonCoreAI'});
  }
  if(path==='features/pulse/settings'){pulsePrefs={...pulsePrefs,...data};delete pulsePrefs.account;return Response.json({ok:true,account,settings:pulsePrefs});}
  if(path.startsWith('features/pulse/')){
    const id=path.split('/').at(-1),item=pulseItems.find(p=>p.item_id===id);assert.ok(item);assert.equal(data.account,account);
    if(options.method==='DELETE'){pulseItems=pulseItems.filter(p=>p.item_id!==id);return Response.json({ok:true,account});}
    assert.equal(data.revision,item.revision);item.revision++;
    if(data.action==='complete'){item.state='done';pulseNotices.push({id:crypto.randomUUID(),title:item.title,item_id:id,kind:'base',status:'done',result:data.result,delivery:'off'});}
    if(data.action==='cancel')item.state='cancelled';
    if(data.action==='snooze'){item.due_at+=900;item.action_at=item.due_at;}
    return Response.json({ok:true,account,item});
  }
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
  await wait(()=>document.querySelector('[data-section="pulse"]'));
  const selected=process.env.VELIA_TEST_CHAT_MODEL||'velia-pro';document.querySelector('[data-model="'+selected+'"]').click();
  await send('Напомни завтра в 10 позвонить клиенту','Сохранила напоминание');assert.equal(pulseItems.length,1);assert.equal(pulseItems[0].title,'позвонить клиенту');assert.equal(mapPlans,0);
  await send('Напомни проверить счёт','На какую дату и время напомнить');assert.equal(pulseItems.length,1);
  await send('завтра в 11','Сохранила напоминание');assert.equal(pulseItems.length,2);assert.equal(pulseItems[1].title,'проверить счёт');
  await send('Покажи мои напоминания','Нашла напоминаний: 2');
  await send('Отметь первое напоминание выполненным. Результат: дозвонился','Отметила выполнение');assert.equal(pulseItems[0].state,'done');assert.equal(pulseItems[1].state,'active');
  document.querySelector('[data-section="pulse"]').click();await wait(()=>document.querySelector('.pulse-card'));
  const card=document.querySelector('.pulse-card');assert.ok(card.textContent.includes('проверить счёт'));
  const prior=pulseItems[1].revision;buttons(card,'Через 15 минут')[0].click();await wait(()=>pulseItems[1].revision>prior);
  buttons(document,'Добавить напоминание')[0].click();const form=document.querySelector('.pulse-editor');form.querySelector('textarea').value='Проверить неделю';form.querySelector('[aria-label="Повторение"]').value='weekly';form.querySelector('[aria-label="Повторение"]').dispatchEvent(new window.Event('change'));form.querySelector('input[type="time"]').value='09:30';form.dispatchEvent(new window.Event('submit',{cancelable:true}));await wait(()=>pulseItems.length===3);assert.equal(pulseItems[2].schedule.kind,'weekly');
  const prefs=document.querySelector('details form');assert.ok(prefs);const toggles=[...prefs.querySelectorAll('input[type="checkbox"]')];assert.ok(toggles.some(input=>input.disabled));assert.equal(pulsePrefs.preview,false);
  const completedFilter=document.querySelector('[aria-label="Показать напоминания"]');completedFilter.value='done';completedFilter.dispatchEvent(new window.Event('change'));await wait(()=>document.querySelector('.pulse-card')?.textContent.includes('позвонить клиенту'));assert.ok(document.querySelector('.pulse-card').textContent.includes('дозвонился'));
  buttons(document,'К диалогу')[0].click();await send('Открой Пульс','Открыла Пульс');await wait(()=>!document.querySelector('#feature-view').hidden);
  assert.equal(mapPlans,0);assert.ok(!calls.some(call=>call.path==='chat/completions'));
  buttons(document,'К диалогу')[0].click();await send('Помоги расставить приоритеты','Обычный ответ: четыре');const ordinary=calls.find(call=>call.path==='chat/completions');assert.ok(JSON.stringify(ordinary.data.messages).includes('позвонить клиенту'));assert.ok(!JSON.stringify(ordinary.data.messages).includes('Проверить неделю'));
  console.log(JSON.stringify({ok:true,synthetic:true,model:selected,checks:['chat creation without a model call','missing date/time clarified in same chat','completion targets referenced item','completion is separate from delivery','snooze uses actual revision','weekly creation form','Telegram off and private preview by default','completed result history','open Pulse from chat','ordinary follow-up uses displayed reminders only']}));
}catch(error){console.error(error);console.error(JSON.stringify({lastMessage:document.querySelector('#messages')?.lastElementChild?.textContent,view:document.querySelector('#feature-view')?.textContent?.slice(-1500)}));process.exitCode=1;}
finally{dom.window.close();setTimeout(()=>process.exit(process.exitCode||0),10);}
