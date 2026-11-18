import {CREEM_SUBSCRIPTIONS} from './subscription-plans.mjs';

function node(tag,text,className){
  const value=document.createElement(tag);
  if(text!==undefined)value.textContent=text;
  if(className)value.className=className;
  return value;
}

export function testPaymentDestination(value){
  if(typeof value!=='string'||value.length>=2048||/[\x00-\x20\\]/.test(value))throw Error('Некорректная тестовая ссылка.');
  const url=new URL(value);
  if(url.protocol!=='https:'||!['www.creem.io','creem.io'].includes(url.hostname)||url.username||url.password||url.hash||
     url.port&&url.port!=='443'||!/^\/test\/(?:checkout|payment)\/[A-Za-z0-9_/-]+$/.test(url.pathname))
    throw Error('Некорректная тестовая ссылка.');
  return url.href;
}

export function mountSandboxBilling(root,{request,isCurrent=()=>true,autoOpen=false,onModeChange=()=>{},navigate=url=>location.assign(url)}={}){
  const section=node('section',undefined,'feature-card sandbox-billing');
  const open=node('button','Тестовая оплата','subscription-manage');open.type='button';
  const panel=node('div');panel.hidden=true;
  const note=node('p','Режим владельца: покупка появится в тестовом балансе этого аккаунта. Тестовые токены не дают реальный платный доступ.','subscription-notice');
  const content=node('div');
  const status=node('p','','subscription-feedback');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const refresh=node('button','Проверить тестовую покупку','subscription-manage');refresh.type='button';
  const close=node('button','Вернуться к рабочим тарифам','subscription-manage');close.type='button';
  panel.append(node('h3','Проверка покупки в аккаунте'),note,content,status,refresh,close);
  section.append(open,panel);root.append(section);
  let busy=false,active=false,controls=[];
  const attempts=new Map();
  const current=()=>active&&typeof request==='function'&&isCurrent();
  const lock=value=>{open.disabled=value;refresh.disabled=value;close.disabled=value;controls.forEach(b=>b.disabled=value||b.unavailable);};
  const safe=result=>result?.ok===true&&result.mode==='test'&&result.preview_only===true;

  async function buy(plan){
    if(!current()||busy)return;
    busy=true;lock(true);status.textContent='Открываю тестовую оплату, связанную с твоим аккаунтом…';
    try{
      if(!attempts.has(plan))attempts.set(plan,crypto.randomUUID());
      const result=await request('economy/creem-test/checkout',{method:'POST',data:{plan,attempt:attempts.get(plan)}});
      if(!current())return;
      if(result?.ok!==true||result.mode!=='test')throw Error('Неверный режим оплаты.');
      navigate(testPaymentDestination(result.checkout_url));
    }catch(error){
      if(current())status.textContent=error.code==='test_purchase_already_completed'?
        'Этот тестовый тариф уже куплен. Нажми «Проверить тестовую покупку».':
        'Не удалось открыть тестовую оплату. Проверь статус покупки и повтори тот же тариф.';
    }finally{busy=false;if(current())lock(false);}
  }

  async function load(){
    if(!current()||busy)return;
    busy=true;lock(true);status.textContent='Проверяю покупку на сервере…';
    try{
      const [catalog,account]=await Promise.all([request('economy/creem-test/catalog'),request('economy/creem-test/account')]);
      if(!current())return;
      if(!safe(catalog)||!safe(account)||account.real_access_granted!==false)throw Error('Неверный режим проверки.');
      content.replaceChildren();controls=[];
      const currentPlan=CREEM_SUBSCRIPTIONS.find(p=>p.code===account.plan_code)?.name||'Free';
      const balance=Number.isSafeInteger(account.test_token_balance)&&account.test_token_balance>=0?
        new Intl.NumberFormat('ru-RU').format(account.test_token_balance):'—';
      content.append(node('p','Тестовый тариф: '+currentPlan,'sandbox-plan'),
        node('p','Тестовые токены: '+balance,'sandbox-balance'));
      const until=Date.parse(account.subscription_until||'');
      if(Number.isFinite(until))content.append(node('p','Тестовый период до '+
        new Intl.DateTimeFormat('ru-RU',{dateStyle:'long'}).format(until),'field-hint'));
      const grid=node('div',undefined,'sandbox-plan-grid');
      for(const plan of CREEM_SUBSCRIPTIONS){
        const verified=Array.isArray(catalog.plans)?catalog.plans.find(p=>p?.code===plan.code):null;
        const bought=Array.isArray(account.purchases)&&account.purchases.some(p=>p?.plan===plan.code&&p.credits>0);
        const button=node('button',bought?plan.name+' · тестовая покупка подтверждена':
          'Тест '+plan.name+' · '+new Intl.NumberFormat('ru-RU',{style:'currency',currency:'USD'}).format(plan.price),'subscription-manage');
        button.type='button';button.unavailable=bought||verified?.enabled!==true;button.disabled=true;
        button.addEventListener('click',()=>buy(plan.code));controls.push(button);grid.append(button);
      }
      content.append(grid);
      status.textContent=account.sync_error?'Подтверждение ещё не получено. Повтори проверку после завершения оплаты.':
        account.purchases?.length?'Покупка подтверждена сервером и отображается в тестовом балансе.':
        'Выбери тестовый тариф. После оплаты вернись сюда — покупка будет проверена и показана в аккаунте.';
    }catch(error){if(current())status.textContent='Тестовая проверка недоступна. Убедись, что вошёл в аккаунт владельца, и повтори.';}
    finally{busy=false;if(current())lock(false);}
  }
  const show=()=>{if(!isCurrent()||typeof request!=='function')return;active=true;panel.hidden=false;open.hidden=true;onModeChange(true);return load();};
  close.addEventListener('click',()=>{if(busy||!isCurrent())return;active=false;panel.hidden=true;open.hidden=false;onModeChange(false);});
  open.addEventListener('click',show);refresh.addEventListener('click',load);
  if(autoOpen)show();
  return section;
}
