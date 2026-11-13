import test from 'node:test';
import assert from 'node:assert/strict';
import {tokenPackages,mountTokenPurchase} from '../token-purchase.mjs';
import {CREEM_SUBSCRIPTIONS,paymentDestination} from '../subscription-plans.mjs';

class Element {
  constructor(tag) {this.tag=tag;this.children=[];this.textContent='';this.events={};this.attributes={};}
  append(...items) {this.children.push(...items);}
  setAttribute(name,value) {this.attributes[name]=value;}
  addEventListener(name,callback) {this.events[name]=callback;}
  set innerHTML(_) {throw Error('Payment UI must not render HTML from the catalog');}
}
const all = root => [root,...root.children.flatMap(all)];
const catalog = {ok:true,topups:[{credits:250,store_price_usd:4.99},{credits:100,store_price_usd:2.49}]};
function withUI(run) {
  const previous=globalThis.document;
  globalThis.document={createElement:tag=>new Element(tag)};
  try {
    const result=run();
    if(result?.then)return result.finally(()=>{globalThis.document=previous;});
    globalThis.document=previous;return result;
  } catch(error) {globalThis.document=previous;throw error;}
}

test('packages come from a valid server catalog and malformed prices are never offered',()=>{
  assert.deepEqual(tokenPackages(catalog),[{credits:100,price:2.49,currency:'USD'},{credits:250,price:4.99,currency:'USD'}]);
  for(const value of [null,{}, {ok:false,topups:catalog.topups},{ok:true,topups:{}}]) assert.deepEqual(tokenPackages(value),[]);
  const bad=[null,{credits:'100',store_price_usd:1},{credits:0,store_price_usd:1},{credits:10,store_price_usd:Infinity},{credits:10,store_price_usd:-1},{credits:10,store_price_usd:'2.49'}];
  assert.deepEqual(tokenPackages({ok:true,topups:[...bad,...catalog.topups,...catalog.topups]}),tokenPackages(catalog));
});

test('selecting a package updates its total; an unrelated Google Play flag cannot enable card payments',()=>withUI(()=>{
  const root=new Element('div');
  mountTokenPurchase(root,{...catalog,billing_enabled:true,checkout_url:'https://evil.example'});
  const inputs=all(root).filter(e=>e.tag==='input');
  assert.equal(inputs[0].checked,true);
  inputs[1].events.change();
  assert.match(all(root).find(e=>e.className==='token-total').textContent,/250.*4,99/);
  const buttons=all(root).filter(e=>e.tag==='button');
  assert.equal(buttons.length,4);assert.ok(buttons.every(button=>button.disabled===true));
  assert.equal(all(root).some(e=>['iframe','script','a'].includes(e.tag)),false);
}));

test('unavailable catalog shows no invented prices and no active payment controls',()=>withUI(()=>{
  const root=new Element('div');mountTokenPurchase(root,null);
  assert.equal(all(root).some(e=>e.tag==='input'),false);
  assert.ok(all(root).some(e=>e.textContent.includes('Пакеты сейчас недоступны')));
  assert.equal(all(root).find(e=>e.tag==='button').disabled,true);
}));

test('subscription references cannot activate payment or replace prices through the remote catalog',()=>withUI(()=>{
  const root=new Element('div');
  const forged={ok:true,billing_enabled:true,creem_enabled:true,checkout_url:'https://evil.example',
    plans:[{id:'plus',store_price_usd:0.01,monthly_credits:999999,checkout_url:'https://evil.example'}],topups:[]};
  mountTokenPurchase(root,forged);
  const cards=all(root).filter(node=>node.tag==='article');
  assert.equal(cards.length,3);
  assert.match(all(cards[0]).find(node=>node.className==='subscription-price').textContent,/14,99/);
  assert.ok(all(cards[0]).some(node=>node.textContent.includes('1 200 токенов')));
  for(const node of all(root)) {
    if(node.tag==='button') {assert.equal(node.disabled,true);assert.deepEqual(node.events,{});}
    assert.equal(node.href,undefined);
    assert.equal(Object.values(node.attributes).some(value=>String(value).includes('creem.io')),false);
    assert.equal(node.textContent.includes('evil.example'),false);
    assert.equal(node.textContent.includes('999999'),false);
  }
  assert.ok(Object.isFrozen(CREEM_SUBSCRIPTIONS));
  for(const plan of CREEM_SUBSCRIPTIONS) {
    assert.ok(Object.isFrozen(plan));
    const url=new URL(plan.productUrl);
    assert.equal(url.origin,'https://www.creem.io');
    assert.equal(url.pathname,`/payment/${plan.productId}`);
    assert.equal(url.search,'');assert.equal(url.hash,'');
  }
}));

const billingReady={ok:true,enabled:true,mode:'prod'};
const noSubscription={ok:true,plan_code:'free',has_subscription:false,has_active_subscription:false};

test('enabled checkout is account-bound through the server and never opens the static product URL',()=>withUI(async()=>{
  const root=new Element('div'),calls=[],opened=[];
  mountTokenPurchase(root,catalog,{billing:billingReady,account:noSubscription,
    request:async(path,options)=>{calls.push([path,options]);return {ok:true,checkout_url:'https://www.creem.io/checkout/prod_example/ch_example'};},
    navigate:url=>opened.push(url)});
  const buy=all(root).find(node=>node.textContent==='Выбрать Pro');
  assert.equal(buy.disabled,false);
  await buy.events.click();
  assert.equal(calls[0][0],'economy/creem/checkout');
  assert.equal(calls[0][1].data.plan,'pro');
  assert.match(calls[0][1].data.attempt,/^[0-9a-f-]{36}$/);
  assert.deepEqual(Object.keys(calls[0][1].data).sort(),['attempt','plan']);
  assert.deepEqual(opened,['https://www.creem.io/checkout/prod_example/ch_example']);
}));

test('a late payment response cannot redirect a different account or closed view',()=>withUI(async()=>{
  const root=new Element('div'),opened=[];let current=true,finish;
  mountTokenPurchase(root,catalog,{billing:billingReady,account:noSubscription,
    request:()=>new Promise(resolve=>{finish=resolve;}),isCurrent:()=>current,navigate:url=>opened.push(url)});
  const pending=all(root).find(node=>node.textContent==='Выбрать Plus').events.click();
  current=false;finish({ok:true,checkout_url:'https://www.creem.io/checkout/prod_example/ch_example'});
  await pending;assert.deepEqual(opened,[]);
}));

test('an existing subscription opens only the owner portal',()=>withUI(async()=>{
  const root=new Element('div'),calls=[],opened=[];
  mountTokenPurchase(root,catalog,{billing:billingReady,account:{ok:true,plan_code:'pro',has_subscription:true,has_active_subscription:true},
    request:async(path,options)=>{calls.push([path,options]);return {ok:true,portal_url:'https://www.creem.io/portal/cust_example?token=fixture'};},
    navigate:url=>opened.push(url)});
  await all(root).find(node=>node.textContent==='Управлять подпиской').events.click();
  assert.equal(calls[0][0],'economy/creem/portal');assert.deepEqual(calls[0][1].data,{});
  assert.equal(opened.length,1);
}));

test('payment redirects reject foreign hosts, credentials, non-HTTPS and test checkout',()=>{
  for(const url of ['https://evil.test/checkout/ch_a','https://www.creem.io.evil.test/checkout/ch_a',
    'http://www.creem.io/checkout/ch_a','https://user@www.creem.io/checkout/ch_a',
    'https://www.creem.io/test/payment/prod_example','https://www.creem.io:444/checkout/ch_a',
    'https://www.creem.io/','https://www.creem.io/checkout/ch_a#fragment',
    'https://www.creem.io/checkout/ch_a;param','https://www.creem.io/checkout/ch_a\n'])assert.throws(()=>paymentDestination(url));
});

test('server-returned payment and checkout redirect families open without rewriting the URL',()=>withUI(async()=>{
  for(const family of ['payment','checkout']){
    const root=new Element('div'),opened=[];
    const url='https://www.creem.io/'+family+'/'+CREEM_SUBSCRIPTIONS[0].productId+'?checkout_id=ch_example';
    mountTokenPurchase(root,catalog,{billing:billingReady,account:noSubscription,
      request:async()=>({ok:true,checkout_url:url}),navigate:value=>opened.push(value)});
    await all(root).find(node=>node.textContent==='Выбрать Plus').events.click();
    assert.deepEqual(opened,[url]);
  }
}));

test('verified tax mode is shown per plan without assuming all prices include tax',()=>withUI(()=>{
  const root=new Element('div');
  const billing={...billingReady,plans:[{code:'plus',tax_mode:'inclusive'},{code:'pro',tax_mode:'exclusive'},{code:'max',tax_mode:'unknown'}]};
  mountTokenPurchase(root,catalog,{billing,account:noSubscription,request:async()=>({ok:true})});
  const cards=all(root).filter(node=>node.tag==='article');
  const labels=cards.map(card=>all(card).find(node=>node.className?.includes('subscription-tax'))?.textContent);
  assert.deepEqual(labels,['Налог включён в цену','Налог добавляется при оплате','Итоговая сумма — на странице оплаты']);
}));

test('unverified catalog never claims tax is included',()=>withUI(()=>{
  const root=new Element('div');
  mountTokenPurchase(root,catalog,{billing:{...billingReady,enabled:false,plans:[{code:'plus',tax_mode:'inclusive'}]},account:noSubscription,request:async()=>({ok:true})});
  assert.equal(all(root).some(node=>node.textContent==='Налог включён в цену'),false);
}));


test('renewal management remains available while new purchases are disabled',()=>withUI(async()=>{
  const root=new Element('div'),calls=[];
  mountTokenPurchase(root,catalog,{billing:{ok:true,enabled:false,mode:'prod'},
    account:{ok:true,plan_code:'pro',has_subscription:true,has_active_subscription:true,can_manage:true},
    request:async(path,options)=>{calls.push(path);return {ok:true,portal_url:'https://www.creem.io/portal/cust_example'};},navigate:()=>{}});
  const manage=all(root).find(node=>node.textContent==='Платежи и управление продлением');
  assert.equal(manage.disabled,false);
  await manage.events.click();
  assert.deepEqual(calls,['economy/creem/portal']);
  const other=all(root).filter(node=>node.textContent==='Подписка уже оформлена');
  assert.ok(other.every(node=>node.disabled===true));
}));


test('unavailable billing explains the failure, offers a safe refresh and never enables checkout',()=>withUI(()=>{
  const root=new Element('div');let refreshes=0;
  mountTokenPurchase(root,catalog,{billingError:{code:'billing_upstream_invalid_response',message:'must-not-show-key'},
    account:noSubscription,request:async()=>{throw Error('must not create checkout');},onRefresh:()=>refreshes++});
  assert.ok(all(root).some(n=>n.textContent.includes('Не удалось подключиться к серверу оплаты')));
  const disabled=all(root).filter(n=>n.textContent==='Оплата недоступна');
  assert.equal(disabled.length,3);assert.ok(disabled.every(n=>n.disabled));
  all(root).find(n=>n.textContent==='Проверить оплату ещё раз').events.click();assert.equal(refreshes,1);
  assert.equal(all(root).some(n=>n.textContent.includes('must-not-show-key')),false);
}));


test('checkout failure is visible next to the clicked plan and button text recovers',()=>withUI(async()=>{
  const root=new Element('div');
  mountTokenPurchase(root,catalog,{billing:billingReady,account:noSubscription,
    request:async()=>{throw Object.assign(new Error('redacted'),{code:'billing_provider_timeout'});}});
  const card=all(root).find(n=>n.tag==='article' && n.className.includes('subscription-plan-plus'));
  const buy=all(card).find(n=>n.textContent==='Выбрать Plus');await buy.events.click();
  assert.equal(buy.textContent,'Выбрать Plus');assert.equal(buy.disabled,false);
  assert.ok(all(card).some(n=>n.textContent.includes('сохранённый заказ будет продолжен')));
}));
