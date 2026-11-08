/* No fetch handler or offline cache: private chats and files stay account-bound. */
'use strict';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[45][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const OWNER=/^[a-f0-9]{24}$/;
let queue=Promise.resolve();
function serial(fn){const next=queue.then(fn);queue=next.catch(()=>{});return next;}
function store(value){return new Promise((resolve,reject)=>{
  const open=indexedDB.open('velia-push-v1',1);
  open.onupgradeneeded=()=>open.result.createObjectStore('state');
  open.onerror=()=>reject(open.error);
  open.onsuccess=()=>{
    const db=open.result,tx=db.transaction('state',value===undefined?'readonly':'readwrite'),items=tx.objectStore('state');
    const request=value===undefined?items.get('active'):items.put(value,'active');let result;
    request.onsuccess=()=>{result=request.result;};
    tx.oncomplete=()=>{db.close();resolve(value===undefined?result:value);};
    tx.onerror=()=>{db.close();reject(tx.error);};
  };
});}
function matches(a,b){return a?.owner===b?.owner&&a?.device_id===b?.device_id&&a?.binding_id===b?.binding_id;}
async function closeNotices(){for(const notice of await self.registration.getNotifications())notice.close();try{await self.navigator?.clearAppBadge?.();}catch{}}
async function clear(expected){const active=await store();if(!active||expected?.owner&&active.owner!==expected.owner||expected?.binding_id&&active.binding_id!==expected.binding_id)return false;await store({state:'off',owner:active.owner});await closeNotices();const sub=await self.registration.pushManager.getSubscription();if(sub)await sub.unsubscribe();return true;}
async function session(){
  try{const response=await fetch('/web-api/v1/notification-session',{credentials:'include',cache:'no-store',signal:AbortSignal.timeout(8000)});if(response.status===401)return {account:null};if(!response.ok)return null;return await response.json();}catch{return null;}
}
function post(path,data,method='POST'){return fetch('/web-api/v1/features/pulse/'+path,{method,credentials:'include',cache:'no-store',headers:{'Content-Type':'application/json','X-Velia-Request':'1'},body:JSON.stringify(data),signal:AbortSignal.timeout(8000)}).catch(()=>{});}
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('message',event=>{
  // Only documents from this origin may change the local account binding.
  if(!event.source?.url||new URL(event.source.url).origin!==self.location.origin)return;
  event.waitUntil(serial(async()=>{
    const data=event.data||{};let value;
    if(data.type==='GET')value=await store();
    else if(data.type==='CLEAR'){await clear(data);value=await store();}
    else if(data.type==='BIND'&&OWNER.test(data.owner)&&UUID.test(data.device_id)&&UUID.test(data.binding_id)){
      // Check the live cookie as well as the caller's remembered account.
      const identity=await session();if(identity?.account!==data.owner)throw Error('Account changed');
      value=await store({state:'on',owner:data.owner,device_id:data.device_id,binding_id:data.binding_id,preview:data.preview===true});
    }else if(data.type==='PREVIEW'){
      value=await store();if(matches(value,data))value=await store({...value,preview:data.preview===true});
    }else throw Error('Invalid binding');
    event.ports[0]?.postMessage({ok:true,value});
  }).catch(()=>event.ports[0]?.postMessage({ok:false})));
});
self.addEventListener('push',event=>event.waitUntil(serial(async()=>{
  let payload;try{payload=event.data.json();}catch{return;}
  if(!OWNER.test(payload?.owner)||!['device_id','binding_id','notification_id','delivery_id'].every(key=>UUID.test(payload[key])))return;
  const active=await store();if(active?.state!=='on'||!matches(active,payload))return;
  const identity=await session();
  if(identity&&identity.account!==active.owner){await clear(active);return;}
  const preview=identity?.account===active.owner&&active.preview&&payload.preview===true;
  await self.registration.showNotification('Велия · Пульс',{
    body:preview&&typeof payload.body==='string'?payload.body.slice(0,500):'Есть напоминание. Открой Велию, чтобы посмотреть подробности.',
    icon:'/web/icon-192.png',badge:'/web/badge-96.png',tag:'velia-pulse:'+payload.delivery_id,
    data:{owner:payload.owner,device_id:payload.device_id,binding_id:payload.binding_id,notification_id:payload.notification_id},
  });
  // Accepted by a push service and displayed by the browser are distinct states.
  await post('push/receipt',{account:active.owner,device_id:active.device_id,binding_id:active.binding_id,delivery_id:payload.delivery_id});
})));
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  event.waitUntil(serial(async()=>{
    const data=event.notification.data,active=await store(),identity=await session();
    const valid=active?.state==='on'&&matches(active,data)&&identity?.account===active.owner&&UUID.test(data?.notification_id);
    const target=self.location.origin+(valid?'/?pulse='+data.notification_id:'/');
    if(valid)await post('notifications/'+data.notification_id+'/read',{account:active.owner});
    const windows=await self.clients.matchAll({type:'window',includeUncontrolled:true});
    const window=windows.find(client=>new URL(client.url).origin===self.location.origin);
    if(window){await window.navigate(target);await window.focus();}else await self.clients.openWindow(target);
  }));
});

// Browsers that rotate a subscription can renew it without a second prompt.
// An interrupted registration is recovered from the native subscription on visit.
self.addEventListener('pushsubscriptionchange',event=>event.waitUntil(serial(async()=>{
  const active=await store();if(active?.state!=='on')return;
  const identity=await session();if(identity?.account!==active.owner)return;
  const sub=event.newSubscription||await self.registration.pushManager.subscribe(event.oldSubscription.options);
  const response=await post('push/devices',{account:active.owner,binding_id:active.binding_id,subscription:sub.toJSON(),label:'Браузер на этом устройстве'});
  if(!response?.ok)return;
  const result=await response.json();
  if(result.account!==active.owner||!UUID.test(result.device_id)||result.binding_id!==active.binding_id)return;
  await store({...active,device_id:result.device_id});
  if(result.device_id!==active.device_id)await post('push/devices/'+active.device_id,{account:active.owner,binding_id:active.binding_id},'DELETE');
}).catch(()=>{})));
