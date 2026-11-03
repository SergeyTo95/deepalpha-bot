import test from 'node:test';
import assert from 'node:assert/strict';
import {AGENTIX_MODES,AGENTIX_LAYERS,AGENTIX_DELIVERY} from '../agentix.mjs';

test('Agentix preview describes three non-operational integration modes',()=>{
  assert.deepEqual(AGENTIX_MODES.map(x=>x.title),['Agentix API','Agentix Embed','Agentix Agents']);
  assert.ok(AGENTIX_MODES.every(x=>x.points.length>=3));
});
test('Agentix architecture covers isolation, execution control and billing planning',()=>{
  assert.equal(AGENTIX_LAYERS.length,6);
  assert.ok(AGENTIX_LAYERS.some(x=>x[2].includes('проекты')));
  assert.ok(AGENTIX_DELIVERY.some(x=>x[2].includes('биллинг')));
});


import {BUILDER_FEATURES,BUILDER_STYLES,BUILDER_DELIVERY,BUILDER_EXAMPLE,buildBusinessBrief,mountBusinessBuilder} from '../agentix-business-builder.mjs';

test('Business Builder normalizes a plain-language request without any external action',()=>{
  assert.equal(BUILDER_FEATURES.length,6);
  assert.ok(BUILDER_STYLES.some(item=>item.id==='auto'));
  assert.ok(BUILDER_DELIVERY.some(item=>item.id==='zip'));
  assert.throws(()=>buildBusinessBrief({idea:'цветы'}),/Опиши идею/);
  const brief=buildBusinessBrief({idea:BUILDER_EXAMPLE,style:'elegant',delivery:'zip',features:['catalog','orders','assistant']});
  assert.match(brief,/цветочный интернет-магазин/);
  assert.match(brief,/Элегантный/);
  assert.match(brief,/ZIP-архивом/);
  assert.match(brief,/не запускай GPU-серверы/);
  assert.match(brief,/не проси прислать API-ключи/);
});

test('Business Builder sends a reviewed draft only through the explicit callback',()=>{
  class Fake {
    constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.attributes={};this.dataset={};this.textContent='';this.value='';this.classList={add:()=>{}};}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=nodes;}
    addEventListener(name,fn){this.listeners[name]=fn;}
    setAttribute(key,value){this.attributes[key]=value;}
    focus(){}
    click(){this.listeners.click?.();}
    find(fn){if(fn(this))return this;for(const child of this.children){const hit=child.find?.(fn);if(hit)return hit;}return null;}
  }
  const saved=globalThis.document;
  globalThis.document={createElement:tag=>new Fake(tag)};
  try{
    const root=new Fake('div');let handedOff=null;
    mountBusinessBuilder(root,{onHandoff:brief=>{handedOff=brief;}});
    const button=title=>root.find(n=>n.tagName==='button'&&n.textContent===title);
    button('Попробовать пример с цветочным магазином').click();
    button('Продолжить').click();
    assert.ok(button('Посмотреть план'));
    button('Посмотреть план').click();
    assert.equal(handedOff,null);
    button('Продолжить с Velia').click();
    assert.match(handedOff,/цветочный интернет-магазин/);
    assert.match(handedOff,/Сначала согласуй со мной план/);
  }finally{globalThis.document=saved;}
});

import {businessPassportFromDraft,businessDraftFromProject,BUSINESS_PROJECT_MARKER} from '../agentix-business-builder.mjs';

test('Agentix Business Builder project passport is bounded and resumable',()=>{
  const original={idea:BUILDER_EXAMPLE,style:'elegant',delivery:'zip',features:['catalog','orders','assistant']};
  const passport=businessPassportFromDraft(original);
  assert.ok(passport.title.startsWith('Agentix · '));
  assert.equal(passport.audience,BUSINESS_PROJECT_MARKER);
  for(const [field,max] of Object.entries({title:120,goal:1200,audience:600,style:800,constraints:1000}))
    assert.ok(passport[field].length<=max,field);
  const restored=businessDraftFromProject({id:'p',passport});
  assert.equal(restored.idea,original.idea);
  assert.equal(restored.style,original.style);
  assert.equal(restored.delivery,original.delivery);
  assert.deepEqual(restored.features,original.features);
  assert.equal(businessDraftFromProject({passport:{...passport,audience:'Some other project'}}),null);
  assert.throws(()=>businessPassportFromDraft({...original,idea:'я'.repeat(1201)}),/1200/);
});
