/** One explicit browser permission, one account-bound subscription per device. */
export function pushSupport(env=globalThis){
  const nav=env.navigator||{},standalone=nav.standalone===true||env.matchMedia?.('(display-mode: standalone)').matches;
  const ios=/iPad|iPhone|iPod/.test(nav.userAgent||'')||nav.platform==='MacIntel'&&nav.maxTouchPoints>1;
  if(ios&&!standalone)return 'install';
  return env.isSecureContext&&nav.serviceWorker&&env.PushManager&&env.Notification?'supported':'unsupported';
}
export function applicationKey(value){return Uint8Array.from(atob(value.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-value.length%4)%4)),ch=>ch.charCodeAt(0));}
export function setupWebPush({profile,pulse}){
  let owner=null,epoch=0,registration=null,publicKey=null,current=null,busy=false,ready=false,error='',operation=Promise.resolve();
  let clearing=Promise.resolve(),syncing=null,syncAccount=null;
  const listeners=new Set(),support=pushSupport(),emit=()=>{for(const fn of listeners)fn();};
  function message(data){return new Promise((resolve,reject)=>{
    const worker=registration?.active;if(!worker)return reject(Error('Фоновая доставка ещё запускается.'));
    const channel=new MessageChannel(),timer=setTimeout(()=>{channel.port1.close();reject(Error('Не удалось сохранить настройку устройства.'));},15000);
    channel.port1.onmessage=event=>{clearTimeout(timer);channel.port1.close();event.data?.ok?resolve(event.data.value):reject(Error('Аккаунт изменился. Открой Велию заново.'));};
    worker.postMessage(data,[channel.port2]);
  });}
  async function worker(){
    registration=await navigator.serviceWorker.register('/velia-sw.js',{scope:'/',updateViaCache:'none'});
    if(!registration.active)await Promise.race([navigator.serviceWorker.ready,new Promise((_,reject)=>setTimeout(()=>reject(Error('Фоновая доставка ещё запускается.')),15000))]);
  }
  async function drop(expected,remote=true){
    const saved=await message({type:'GET'});
    if(!saved||expected&&saved.owner!==expected)return;
    const cleared=await message({type:'CLEAR',owner:saved.owner,binding_id:saved.binding_id});
    if(cleared?.state!=='off'||cleared.owner!==saved.owner)return;current=null;
    // The worker clears its binding and unsubscribes before acknowledging.
    if(remote&&saved.device_id&&profile()?.account===saved.owner){
      try{await pulse.call('/push/devices/'+saved.device_id,{method:'DELETE',data:{binding_id:saved.binding_id}});}catch{}
    }
  }
  async function bind(sub,binding,account,version){
    const result=await pulse.call('/push/devices',{method:'POST',data:{subscription:sub.toJSON(),binding_id:binding,label:'Браузер на этом устройстве'}});
    if(epoch!==version||profile()?.account!==account){await sub.unsubscribe();return;}
    const value={owner:account,device_id:result.device_id,binding_id:result.binding_id,preview:pulse.state().settings?.preview===true};
    await message({type:'BIND',...value});if(epoch!==version||profile()?.account!==account){await message({type:'CLEAR',owner:account,binding_id:binding});return;}
    current=value;
  }
  function sync(){
    const account=profile()?.account;if(busy)return Promise.resolve();
    if(syncing&&syncAccount===account)return syncing;syncAccount=account;
    const pending=refresh();syncing=pending;
    pending.finally(()=>{if(syncing===pending)syncing=null;}).catch(()=>{});return pending;
  }
  async function refresh(){
    const account=profile()?.account;if(!account)return disable();
    if(owner!==account){epoch++;owner=account;current=null;}const version=epoch;
    if(support!=='supported'){emit();return;}
    ready=false;error='';emit();
    try{
      await clearing;if(epoch!==version)return;
      await worker();const stored=await message({type:'GET'});if(epoch!==version)return;
      if(stored?.owner&&stored.owner!==account)await drop(stored.owner,false);
      if(stored?.state==='on'&&stored.owner===account)current=stored;
      const config=await pulse.call('/push/config');if(epoch!==version)return;
      if(!config.available)throw Error('Фоновая доставка временно недоступна.');publicKey=applicationKey(config.public_key);
      const sub=await registration.pushManager.getSubscription();
      if(sub&&stored?.state==='on'&&stored.owner===account&&Notification.permission==='granted'){
        const subscribed=sub.options?.applicationServerKey;
        if(subscribed&&String(new Uint8Array(subscribed))!==String(publicKey))await drop(account);
        else await bind(sub,stored.binding_id,account,version);
      }else {current=null;if(sub)await drop(stored?.owner,false);}
      if(epoch===version)ready=true;
    }catch(cause){if(epoch===version)error=cause.message;}
    finally{if(epoch===version)emit();}
  }
  function activate(){
    if(busy||!ready||!publicKey||!registration?.active)return Promise.resolve();
    const account=profile()?.account,version=epoch;if(!account||owner!==account)return Promise.resolve();
    busy=true;error='';emit();
    // Call subscribe directly inside the click, before awaiting anything (iOS).
    let subscription;
    try{subscription=registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:publicKey});}catch(cause){subscription=Promise.reject(cause);}
    operation=(async()=>{
      let sub;
      try{
        sub=await subscription;
        if(epoch!==version||profile()?.account!==account){await sub.unsubscribe();return;}
        await bind(sub,crypto.randomUUID(),account,version);
      }catch(cause){if(sub)await sub.unsubscribe().catch(()=>{});if(epoch===version)error=Notification.permission==='denied'?'Уведомления запрещены. Разреши их в настройках этого сайта в браузере.':cause.message;}
      finally{if(epoch===version){busy=false;emit();}}
    })();return operation;
  }
  function disable(){
    const expected=owner;epoch++;owner=null;current=null;ready=false;busy=false;error='';emit();
    const pending=clearing.then(async()=>{
      if(support!=='supported')return;
      try{await worker();await operation.catch(()=>{});await drop(expected);}catch(cause){error=cause.message;emit();throw cause;}
    });clearing=pending.catch(()=>{});return pending;
  }
  async function toggle(){
    if(current){busy=true;emit();try{await drop(owner);ready=true;}catch(cause){error=cause.message;}finally{busy=false;emit();}}
    else return activate();
  }
  pulse.subscribe(()=>{
    if(current&&current.owner===profile()?.account){current={...current,preview:pulse.state().settings?.preview===true};message({type:'PREVIEW',...current}).catch(()=>{});}
  });
  if(typeof document!=='undefined')document.addEventListener('visibilitychange',()=>{if(!document.hidden&&profile()?.account&&!busy)sync().catch(()=>{});});
  return {sync,disable,toggle,state:()=>({support,active:!!current,busy,ready,error,permission:typeof Notification==='undefined'?'default':Notification.permission}),subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn);}};
}

export function mountWebPush(parent,{webPush,signal}){
  if(!webPush)return;
  const title=document.createElement('h3'),description=document.createElement('p'),status=document.createElement('p'),button=document.createElement('button');
  title.textContent='Уведомления на этом устройстве';button.type='button';status.setAttribute('role','status');parent.append(title,description,button,status);
  function draw(){
    if(signal.aborted)return;const state=webPush.state();
    description.textContent=state.support==='install'?'На iPhone и iPad открой Велию в Safari: «Поделиться» → «На экран Домой». Запусти её с новой иконки и включи уведомления здесь.':state.support==='unsupported'?'Этот браузер не поддерживает фоновую доставку. Напоминания доступны в Пульсе и через Telegram.':'Напоминания приходят в системные уведомления, даже когда вкладка закрыта. В каждом браузере включаются отдельно; доставка зависит от настроек устройства и подключения.';
    button.hidden=state.support!=='supported';button.textContent=state.busy?'Сохраняю…':state.active?'Отключить на этом устройстве':'Включить на этом устройстве';button.disabled=state.busy||!state.active&&(!state.ready||state.permission==='denied');
    status.textContent=state.error|| (state.active?'Включены на этом устройстве.':state.permission==='denied'?'Разреши уведомления в настройках этого сайта в браузере.':!state.ready&&state.support==='supported'?'Готовлю фоновую доставку…':'');
  }
  button.onclick=()=>webPush.toggle();const unsubscribe=webPush.subscribe(draw);signal.addEventListener('abort',unsubscribe,{once:true});draw();webPush.sync().catch(()=>{});
}
