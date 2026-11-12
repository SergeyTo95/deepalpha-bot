import test from 'node:test';
import assert from 'node:assert/strict';
import {tokenPackages,mountTokenPurchase} from '../token-purchase.mjs';

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
  try {run();} finally {globalThis.document=previous;}
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
  assert.equal(buttons.length,1);assert.equal(buttons[0].disabled,true);
  assert.equal(all(root).some(e=>['iframe','script','a'].includes(e.tag)),false);
}));

test('unavailable catalog shows no invented prices and no active payment controls',()=>withUI(()=>{
  const root=new Element('div');mountTokenPurchase(root,null);
  assert.equal(all(root).some(e=>e.tag==='input'),false);
  assert.ok(all(root).some(e=>e.textContent.includes('Пакеты сейчас недоступны')));
  assert.equal(all(root).find(e=>e.tag==='button').disabled,true);
}));
