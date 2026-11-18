import test from 'node:test';
import assert from 'node:assert/strict';
import {mountSandboxBilling,testPaymentDestination} from '../sandbox-billing.mjs';

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
  await all(root).find(n=>n.textContent==='Проверить тестовую покупку').events.click();
  assert.equal(all(root).find(n=>n.className==='sandbox-plan').textContent,'Тестовый тариф: Pro');
  assert.match(all(root).find(n=>n.className==='sandbox-balance').textContent,/3\s000/);
  assert.equal(all(root).find(n=>n.tag==='button'&&n.textContent.includes('Pro · тестовая покупка подтверждена')).disabled,true);
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

test('entering and leaving test view explicitly toggles the real-payment panels',()=>withUI(async()=>{
  const root=new Element('div'),modes=[];
  mountSandboxBilling(root,{onModeChange:value=>modes.push(value),request:async path=>path.endsWith('/catalog')?catalog:account});
  await all(root).find(n=>n.textContent==='Тестовая оплата').events.click();
  assert.deepEqual(modes,[true]);
  all(root).find(n=>n.textContent==='Вернуться к рабочим тарифам').events.click();
  assert.deepEqual(modes,[true,false]);
}));
