import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {pushSupport,setupWebPush,applicationKey} from '../web-push.mjs';

const A='a'.repeat(24),B='b'.repeat(24),device='11111111-1111-4111-8111-111111111111',binding='22222222-2222-4222-8222-222222222222',notification='33333333-3333-4333-8333-333333333333',delivery='44444444-4444-4444-8444-444444444444';
const active={state:'on',owner:A,device_id:device,binding_id:binding,preview:false};
const payload={owner:A,device_id:device,binding_id:binding,notification_id:notification,delivery_id:delivery,preview:true,body:'Частное напоминание'};

test('capability detection explains iOS Home Screen requirement and other devices',()=>{
  const env={isSecureContext:true,navigator:{serviceWorker:{},userAgent:'Android'},PushManager:class{},Notification:class{}};
  assert.equal(pushSupport(env),'supported');assert.equal(pushSupport({...env,isSecureContext:false}),'unsupported');
  assert.equal(pushSupport({...env,navigator:{...env.navigator,userAgent:'iPhone'}}),'install');
  assert.equal(pushSupport({...env,navigator:{...env.navigator,userAgent:'iPhone',standalone:true}}),'supported');
  assert.equal(pushSupport({...env,navigator:{...env.navigator,platform:'MacIntel',maxTouchPoints:5}}),'install');
  assert.deepEqual([...applicationKey('AQID')],[1,2,3]);
});

function browser(){
  let account=A,saved,sub=null,subscriptionGate;const calls=[],listeners=[];
  const subscription={options:{},toJSON:()=>({endpoint:'https://fcm.googleapis.com/test',keys:{}}),unsubscribe:async()=>{calls.push('unsubscribe');sub=null;return true;}};
  const registration={active:{postMessage(data,ports){
    calls.push('worker:'+data.type);
    if(data.type==='CLEAR'&&(!data.owner||saved?.owner===data.owner)&&(!data.binding_id||saved?.binding_id===data.binding_id)){saved={state:'off',owner:saved?.owner};if(sub)subscription.unsubscribe();}
    if(data.type==='BIND')saved={state:'on',...data};
    if(data.type==='PREVIEW'&&saved?.binding_id===data.binding_id)saved={...saved,preview:data.preview};
    ports[0].postMessage({ok:true,value:saved});ports[0].close();
  }},pushManager:{getSubscription:async()=>sub,subscribe(options){calls.push('subscribe');assert.equal(options.userVisibleOnly,true);sub=subscription;return subscriptionGate||Promise.resolve(sub);}}};
  const replacements={isSecureContext:true,navigator:{serviceWorker:{register:async()=>registration}},PushManager:class{},Notification:{permission:'granted'}};
  const originals=Object.fromEntries(Object.keys(replacements).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  for(const [key,value] of Object.entries(replacements))Object.defineProperty(globalThis,key,{value,configurable:true});
  const pulse={call:async(path,options)=>{calls.push(path);if(path==='/push/config')return {public_key:'AQID',available:true};if(path==='/push/devices')return {device_id:device,binding_id:options.data.binding_id};return {};},state:()=>({settings:{preview:false}}),subscribe:fn=>listeners.push(fn)};
  const push=setupWebPush({profile:()=>account?{account}:null,pulse});
  return {push,calls,get saved(){return saved;},set saved(value){saved=value;},get sub(){return sub;},subscription,set sub(value){sub=value;},set account(value){account=value;},gate(value){subscriptionGate=value;},restore(){for(const [key,value] of Object.entries(originals))if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}};
}

test('initializing never prompts; enable subscribes directly in the user click; disabling clears before network',async()=>{
  const env=browser();try{
    await env.push.sync();assert.ok(env.push.state().ready);assert.ok(!env.calls.includes('subscribe'));
    const enabling=env.push.toggle();assert.equal(env.calls.at(-1),'subscribe');await enabling;
    assert.ok(env.push.state().active);assert.equal(env.saved.owner,A);
    await env.push.toggle();assert.ok(!env.push.state().active);
    assert.ok(env.calls.indexOf('worker:CLEAR')<env.calls.indexOf('unsubscribe'));
    assert.ok(env.calls.indexOf('unsubscribe')<env.calls.indexOf('/push/devices/'+device));
  }finally{env.restore();}
});

test('same-account reload restores consent; account switch clears and never auto-subscribes',async()=>{
  const env=browser();try{
    env.saved=active;env.sub=env.subscription;await env.push.sync();assert.ok(env.push.state().active);
    env.account=B;await env.push.sync();assert.ok(!env.push.state().active);assert.equal(env.saved.state,'off');
    assert.ok(env.calls.includes('unsubscribe'));assert.ok(!env.calls.includes('subscribe'));
  }finally{env.restore();}
});

test('logout during a native permission prompt cannot bind a late subscription',async()=>{
  const env=browser();try{
    await env.push.sync();let resolve;env.gate(new Promise(done=>{resolve=done;}));
    const enable=env.push.toggle(),disable=env.push.disable();env.account=B;
    resolve(env.subscription);await Promise.all([enable,disable]);
    assert.ok(!env.push.state().active);assert.ok(!env.calls.includes('/push/devices'));assert.equal(env.sub,null);
  }finally{env.restore();}
});

test('guest cleanup completes before another account can recover a binding',async()=>{
  const env=browser();try{
    env.saved=active;env.sub=env.subscription;const cleanup=env.push.disable();env.account=B;
    await Promise.all([cleanup,env.push.sync()]);assert.ok(env.push.state().ready);assert.ok(!env.push.state().active);assert.equal(env.saved.state,'off');
  }finally{env.restore();}
});

// A tiny asynchronous IDB surface for worker execution. It deliberately stores
// clones and commits only after request completion, like the browser API.
async function worker(){
  let stored=structuredClone(active),account=A,offline=false;const listeners={},shown=[],posts=[],opened=[];
  const indexedDB={open(){const request={};queueMicrotask(()=>{
    request.result={createObjectStore(){},close(){},transaction(){const tx={objectStore:()=>({
      get(){const result={};queueMicrotask(()=>{result.result=structuredClone(stored);result.onsuccess();tx.oncomplete();});return result;},
      put(value){const result={};queueMicrotask(()=>{stored=structuredClone(value);result.onsuccess();tx.oncomplete();});return result;},
    })};return tx;}};request.onsuccess();
  });return request;}};
  const self={location:{origin:'https://velia.example'},registration:{pushManager:{getSubscription:async()=>null},showNotification:async(title,options)=>shown.push({title,...options}),getNotifications:async()=>shown.map(item=>({close:()=>{item.closed=true;}}))},clients:{claim:async()=>{},matchAll:async()=>[],openWindow:async url=>opened.push(url)},skipWaiting:async()=>{},addEventListener:(type,listener)=>{listeners[type]=listener;}};
  const fetch=async(path,options)=>{if(path==='/web-api/v1/notification-session'){if(offline)throw Error('Offline');return {status:account?200:401,ok:!!account,json:async()=>({account})};}posts.push({path,body:JSON.parse(options.body)});return {ok:true,json:async()=>({account,device_id:device,binding_id:binding})};};
  vm.runInNewContext(await fs.readFile(new URL('../velia-sw.js',import.meta.url),'utf8'),{self,indexedDB,fetch,URL,AbortSignal,Promise,console});
  async function dispatch(type,event={}){let promise;listeners[type]({...event,waitUntil:value=>{promise=value;}});await promise;}
  async function message(data){let reply;await dispatch('message',{source:{url:'https://velia.example/'},data,ports:[{postMessage:value=>{reply=value;}}]});return reply;}
  return {shown,posts,opened,message,get stored(){return stored;},set stored(value){stored=value;},set account(value){account=value;},set offline(value){offline=value;},push:value=>dispatch('push',{data:{json:()=>value}}),click:data=>dispatch('notificationclick',{notification:{data,close(){}}}),renew:sub=>dispatch('pushsubscriptionchange',{newSubscription:sub})};
}

test('worker displays generic notification, acknowledges display, and opens only Pulse on this origin',async()=>{
  const env=await worker();await env.push(payload);assert.equal(env.shown.length,1);
  assert.ok(!env.shown[0].body.includes('Частное'));assert.ok(env.posts[0].path.endsWith('/push/receipt'));
  await env.click(env.shown[0].data);assert.deepEqual(env.opened,['https://velia.example/?pulse='+notification]);
  assert.ok(env.posts[1].path.endsWith('/'+notification+'/read'));
});

test('private previews require both preferences and the live account; offline delivery remains generic',async()=>{
  const env=await worker();env.stored={...active,preview:true};await env.push(payload);assert.equal(env.shown[0].body,payload.body);
  env.offline=true;await env.push(payload);assert.ok(!env.shown[1].body.includes('Частное'));
  env.offline=false;env.account=B;await env.push(payload);assert.equal(env.shown.length,2);assert.equal(env.stored.state,'off');
});

test('logout closes shown notices; late packets and stale clears cannot expose or remove a new account binding',async()=>{
  const env=await worker();await env.push(payload);await env.message({type:'CLEAR',owner:A,binding_id:binding});
  assert.equal(env.shown[0].closed,true);await env.push(payload);assert.equal(env.shown.length,1);
  env.account=B;env.stored={...active,owner:B,binding_id:notification};
  await env.message({type:'CLEAR',owner:A,binding_id:binding});await env.push(payload);
  assert.equal(env.stored.owner,B);assert.equal(env.stored.state,'on');assert.equal(env.shown.length,1);
  await env.click(payload);assert.deepEqual(env.opened,['https://velia.example/']);
});

test('worker refuses to bind a remembered account with another account cookie',async()=>{
  const env=await worker();env.account=B;const reply=await env.message({type:'BIND',...active});assert.equal(reply.ok,false);
  assert.equal(env.stored.owner,A);
});

test('background subscription rotation requires the current account and preserves explicit consent',async()=>{
  const env=await worker();await env.renew({toJSON:()=>({endpoint:'https://fcm.googleapis.com/new'})});
  assert.ok(env.posts[0].path.endsWith('/push/devices'));assert.equal(env.posts[0].body.account,A);assert.equal(env.stored.state,'on');
  env.account=B;const count=env.posts.length;await env.renew({toJSON:()=>({endpoint:'https://fcm.googleapis.com/newer'})});assert.equal(env.posts.length,count);
});
