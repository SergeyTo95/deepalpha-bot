import test from 'node:test';
import assert from 'node:assert/strict';
import {balanceOverview,mountBalanceOverview} from '../balance-overview.mjs';
const now=Date.parse('2026-11-12T12:00:00Z');
test('free balance formats credits and hides legacy expired subscription metadata',()=>{
  const view=balanceOverview({plan_code:'free',credits:9999065,subscription_until:'2026-05-03T22:34:28.234575',source:'free'},null,{now});
  assert.equal(view.credits.replace(/\s/g,''),'9999065');assert.equal(view.status,'Бесплатный доступ');assert.equal(view.hasUsage,false);
  assert.equal(JSON.stringify(view).includes('2026-05'),false);
});
test('paid access presents a readable period, with expired access distinguished',()=>{
  const account={plan_code:'pro',credits:3000,subscription_until:'2026-12-03T22:34:28.234575'};
  assert.match(balanceOverview(account,null,{now}).status,/Доступ до .*2026/);
  assert.equal(balanceOverview(account,null,{now:Date.parse('2027-01-01')}).status,'Период подписки завершён');
});
test('missing or malformed data is not presented as a zero balance or unlimited allowance',()=>{
  for(const credits of [undefined,'999',-1,NaN,Infinity])assert.equal(balanceOverview({credits},null,{now}).credits,'—');
  assert.equal(balanceOverview({credits:0},null,{now}).credits,'0');
});
test('only personal counted answers are selected from the debug usage snapshot',()=>{
  const view=balanceOverview({plan_code:'free',credits:100},{user_messages:2,global_messages:999,global_cost_usd:1234,per_user_daily_cost_limit_usd:999},{now});
  assert.equal(view.messages,'2');assert.equal(view.hasUsage,true);
  assert.equal(JSON.stringify(view).includes('1234'),false);assert.equal(JSON.stringify(view).includes('999'),false);
});
test('rendering allowlists fields and never inserts raw account or global server values',()=>{
  const original=globalThis.document;
  class Node {constructor(tag){this.tag=tag;this.children=[];this.textContent='';}append(...n){this.children.push(...n);}setAttribute(){}set innerHTML(_){throw Error('unsafe HTML');}}
  globalThis.document={createElement:tag=>new Node(tag)};
  try {const root=new Node('div');mountBalanceOverview(root,{account:{plan_code:'free',credits:100,source:'do-not-render-secret',evil:'<script>'},usage:{user_messages:2,global_cost_usd:777},now});
    const text=node=>[node.textContent,...node.children.map(text)].join(' '),rendered=text(root);
    assert.match(rendered,/БАЛАНС VELIA/);for(const hidden of ['do-not-render-secret','global','777','<script>'])assert.equal(rendered.includes(hidden),false);
  } finally {globalThis.document=original;}
});
