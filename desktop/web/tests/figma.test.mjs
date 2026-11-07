import test from 'node:test';
import assert from 'node:assert/strict';
import {parseFigmaPlan, runFigmaAgent, wantsFigma} from '../figma.mjs';

test('Figma routing requires a design request, with strict single-action plans', () => {
  assert.equal(wantsFigma('Создай в Figma карточку товара'), true);
  assert.equal(wantsFigma('Create an onboarding screen in Figma'), true);
  assert.equal(wantsFigma('Что такое Figma?'), false);
  assert.equal(wantsFigma('Нарисуй карточку на бумаге'), false);
  assert.deepEqual(parseFigmaPlan('```json\n{"answer":"Готово"}\n```'), {answer:'Готово'});
  for(const text of ['[]','null','{"tool":"figma_execute"}','{"answer":"done","tool":"figma_execute","arguments":{}}']) {
    assert.throws(()=>parseFigmaPlan(text));
  }
});

test('Figma planning uses the selected model, schema and actual tool evidence', async () => {
  const original=globalThis.fetch, calls=[], planned=[];
  try {
    globalThis.fetch=async(url,options)=>{
      calls.push({url,data:options.body&&JSON.parse(options.body)});
      if(url.endsWith('/verify'))return Response.json({ok:true,connected:true,connection_id:'connection-1'});
      if(url.endsWith('/tools'))return Response.json({ok:true,tools:[{name:'figma_create_child',description:'Create a node',inputSchema:{type:'object'}}]});
      assert.ok(url.endsWith('/call'));
      return Response.json({ok:true,isError:false,text:'{"id":"1:2"}'});
    };
    const plans=[{schema:'figma_create_child'},{tool:'figma_create_child',arguments:{nodeType:'FRAME'}},{answer:'Создала фрейм в макете.'}];
    const user={role:'user',content:'Создай фрейм в Figma',requestId:'request-1234'};
    const text=await runFigmaAgent({model:'velia-pro',user,history:[user],signal:new AbortController().signal,progress:()=>{},request:async(path,payload)=>{
      planned.push(structuredClone(payload));assert.equal(path,'figma/plan');assert.equal(payload.model,'velia-pro');assert.equal(payload.web_search,false);
      const event={choices:[{delta:{content:JSON.stringify(plans.shift())},finish_reason:'stop'}]};
      return new Response('data: '+JSON.stringify(event)+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});
    }});
    assert.equal(text,'Создала фрейм в макете.');
    assert.equal(user.figmaConnection,'connection-1');
    const writes=calls.filter(c=>c.url.endsWith('/call'));
    assert.equal(writes.length,1);assert.equal(writes[0].data.operation_id,'request-1234:1');
    assert.ok(planned.at(-1).messages.some(m=>m.content.includes('1:2')));
    await assert.rejects(runFigmaAgent({user}), /уже выполнялось/);
  } finally {globalThis.fetch=original;}
});

test('an old turn cannot edit a newly paired file', async()=>{
  const original=globalThis.fetch;
  try {
    globalThis.fetch=async()=>Response.json({ok:true,connected:true,connection_id:'new-file'});
    await assert.rejects(runFigmaAgent({model:'velia-flash',user:{figmaConnection:'old-file'},signal:new AbortController().signal}),/изменилось/);
  } finally {globalThis.fetch=original;}
});
