import test from 'node:test';
import assert from 'node:assert/strict';
import {mountSandboxBilling,testPaymentDestination} from '../sandbox-billing.mjs';
import {mountSubscriptionPlans} from '../subscription-plans.mjs';

class Element{
  constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.events={};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(){}
  addEventListener(event,callback){this.events[event]=callback;}
  set innerHTML(value){throw Error('No HTML rendering in billing');}
}
const all=root=>[root,...root.children.flatMap(all)];
async function withUI(run){const previous=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
  try{await run();}finally{globalThis.document=previous;}}
const catalog={ok:true,mode:'test',preview_only:true,plans:[{code:'pro',enabled:true},{code:'plus',enabled:true},{code:'max',enabled:false}]};
const account={ok:true,mode:'test',preview_only:true,real_access_granted:false,plan_code:'free',test_token_balance:0,purchases:[]};

test('test UI creates a server-bound test session rather than using static product links',()=>withUI(async()=>{
  const root=new Element('div'),calls=[],opened=[];
  mountSandboxBilling(root,{request:async(path,options)=>{calls.push([path,options]);
    return path.endsWith('/catalog')?catalog:path.endsWith('/account')?account:
      {ok:true,mode:'test',checkout_url:'https://www.creem.io/test/checkout/prod_test/ch_test'};
  },navigate:url=>opened.push(url)});
  await all(root).find(n=>n.textContent==='Тестовая оплата').events.click();
  const pro=all(root).find(n=>n.tag==='button'&&n.textContent.startsWith('Тест Pro'));
  assert.equal(pro.disabled,false);
  assert.equal(all(root).find(n=>n.tag==='button'&&n.textContent.startsWith('Тест Max')).disabled,true);
  await pro.events.click();
  const [path,options]=calls.at(-1);
  assert.equal(path,'economy/creem-test/checkout');
  assert.deepEqual(Object.keys(options.data).sort(),['attempt','plan']);
  assert.equal(options.data.plan,'pro');
  assert.match(options.data.attempt,/^[0-9a-f-]{36}$/);
  assert.deepEqual(opened,['https://www.creem.io/test/checkout/prod_test/ch_test']);
}));

test('refresh displays only server-verified test purchase and balance',()=>withUI(async()=>{
  let paid=false;
  const root=new Element('div');
  mountSandboxBilling(root,{request:async path=>path.endsWith('/catalog')?catalog:paid?
    {...account,plan_code:'pro',test_token_balance:3000,purchases:[{plan:'pro',credits:3000}]}:account});
  await all(root).find(n=>n.textContent==='Тестовая оплата').events.click();
  assert.equal(all(root).find(n=>n.className==='sandbox-plan').textContent,'Тестовый тариф: Free');
  paid=true;
  await all(root).find(n=>n.textContent==='Обновить тестовый статус').events.click();
  assert.equal(all(root).find(n=>n.className==='sandbox-plan').textContent,'Тестовый тариф: Pro');
  assert.match(all(root).find(n=>n.className==='sandbox-balance').textContent,/3\s000/);
  assert.equal(all(root).find(n=>n.tag==='button'&&n.textContent.includes('Pro · тестовая покупка подтверждена')).disabled,true);
}));

test('the Plus-to-Pro button upgrades only on click and refresh reads status',()=>withUI(async()=>{
  const root=new Element('div'),calls=[];
  let current={...account,plan_code:'plus',test_token_balance:4200,plus_to_pro:{status:'ready'}};
  mountSandboxBilling(root,{request:async(path,options)=>{
    calls.push([path,options]);
    if(path.endsWith('/catalog'))return catalog;
    if(path.endsWith('/upgrade-plus'))current={...current,plan_code:'pro',plus_to_pro:{status:'applied'}};
    return current;
  }});
  await all(root).find(n=>n.textContent==='Тестовая оплата').events.click();
  assert.equal(all(root).find(n=>n.className==='sandbox-plan').textContent,'Тестовый тариф: Plus');
  assert.equal(calls.filter(([,options])=>options?.method==='POST').length,0);
  await all(root).find(n=>n.textContent==='Перейти с Plus на Pro в тесте').events.click();
  assert.deepEqual(calls.filter(([,options])=>options?.method==='POST'),
    [['economy/creem-test/upgrade-plus',{method:'POST',data:{}}]]);
  assert.equal(all(root).find(n=>n.className==='sandbox-plan').textContent,'Тестовый тариф: Pro');
  assert.match(all(root).find(n=>n.className==='subscription-feedback').textContent,/доплату ещё проверяем/);
  await all(root).find(n=>n.textContent==='Обновить тестовый статус').events.click();
  assert.equal(calls.filter(([,options])=>options?.method==='POST').length,1);
}));

test('a production catalog cannot activate test controls',()=>withUI(async()=>{
  const root=new Element('div');
  mountSandboxBilling(root,{request:async()=>({...catalog,mode:'prod'})});
  await all(root).find(n=>n.textContent==='Тестовая оплата').events.click();
  assert.equal(all(root).some(n=>n.textContent.startsWith('Тест Pro')),false);
}));

test('an account change prevents late test checkout navigation',()=>withUI(async()=>{
  const root=new Element('div'),opened=[];let active=true,finish;
  mountSandboxBilling(root,{isCurrent:()=>active,navigate:url=>opened.push(url),request:async path=>
    path.endsWith('/catalog')?catalog:path.endsWith('/account')?account:new Promise(resolve=>{finish=resolve;})});
  await all(root).find(n=>n.textContent==='Тестовая оплата').events.click();
  const pending=all(root).find(n=>n.tag==='button'&&n.textContent.startsWith('Тест Pro')).events.click();
  active=false;finish({ok:true,mode:'test',checkout_url:'https://www.creem.io/test/checkout/prod_test/ch_test'});
  await pending;assert.deepEqual(opened,[]);
}));

test('test destination rejects production and foreign URLs',()=>{
  for(const value of ['https://www.creem.io/checkout/prod_x/ch_x','https://www.creem.io/payment/prod_x',
    'https://evil.example/test/checkout/x','https://www.creem.io/test/payment/x#fake'])
    assert.throws(()=>testPaymentDestination(value));
});

test('live upgrades require a price review and explicit consent without a second checkout',()=>withUI(async()=>{
  const root=new Element('div'),calls=[],navigations=[];
  const quote={ok:true,estimate_cents:750,period_end:1797000000,monthly_cents:2999,tax_included:true,credits:900};
  mountSubscriptionPlans(root,{
    billing:{ok:true,enabled:true,mode:'prod'},account:{ok:true,plan_code:'plus',has_active_subscription:true,can_upgrade_plus:true},
    navigate:url=>navigations.push(url),
    request:async(path,options)=>{
      calls.push([path,options]);
      return options?.method==='POST'?{ok:true,plus_upgrade:{status:'applied',paid_cents:749,credits:900}}:quote;
    },
  });
  const find=text=>all(root).find(node=>node.tag==='button'&&node.textContent===text);
  const trigger=find('Перейти с Plus на Pro');
  await trigger.events.click();
  assert.deepEqual(calls,[['economy/creem/upgrade-plus',undefined]]);
  assert(all(root).some(n=>/Расчётная доплата сейчас.*7,50/.test(n.textContent)));
  find('Отмена').events.click();
  assert.equal(find('Подтвердить переход и оплатить'),undefined);
  await trigger.events.click();
  await find('Подтвердить переход и оплатить').events.click();
  assert.deepEqual(calls.at(-1),['economy/creem/upgrade-plus',{method:'POST',data:{
    period_end:quote.period_end,estimate_cents:750,tax_included:true,
  }}]);
  assert(all(root).some(n=>/Списано 7,49/.test(n.textContent)));
  assert.deepEqual(navigations,[]);
}));

test('entering and leaving test view explicitly toggles the real-payment panels',()=>withUI(async()=>{
  const root=new Element('div'),modes=[];
  mountSandboxBilling(root,{onModeChange:value=>modes.push(value),request:async path=>path.endsWith('/catalog')?catalog:account});
  await all(root).find(n=>n.textContent==='Тестовая оплата').events.click();
  assert.deepEqual(modes,[true]);
  all(root).find(n=>n.textContent==='Вернуться к рабочим тарифам').events.click();
  assert.deepEqual(modes,[true,false]);
}));
