import test from 'node:test';
import assert from 'node:assert/strict';
import {cloudProvider,runCloudAgent} from '../connections.mjs';

test('cloud requests distinguish provider actions from general questions',()=>{
  assert.equal(cloudProvider('Проверь деплой на Railway'),'railway');
  assert.equal(cloudProvider('Покажи мои проекты в Vercel'),'vercel');
  assert.equal(cloudProvider('Что такое Vercel?'),null);
  assert.equal(cloudProvider('Найди письма в Gmail'),'google');
  assert.equal(cloudProvider('Сохрани план в Notion'),'notion');
  assert.equal(cloudProvider('Обнови описание товара в Shopify'),'shopify');
  assert.equal(cloudProvider('Покажи мои встречи в календаре на завтра'),'google');
  assert.equal(cloudProvider('Назначь встречу на следующей неделе'),'google');
  assert.equal(cloudProvider('Что такое Google Calendar?'),null);
  assert.equal(cloudProvider('Сравни Railway и Vercel'),null);
});

for(const provider of ['railway','vercel','google','notion','shopify'])for(const model of ['velia-flash','velia-quantum','velia-pro']){
  test(provider+' tasks use '+model+' and authoritative tool results',async()=>{
    const original=globalThis.fetch,calls=[];
    try{
      globalThis.fetch=async(url,options)=>{
        calls.push({url,data:options.body&&JSON.parse(options.body)});
        if(url.endsWith('/verify'))return Response.json({ok:true,connected:true,connection_id:'connection-1'});
        if(url.endsWith('/tools'))return Response.json({ok:true,tools:[{name:'list_projects',description:'List projects',inputSchema:{type:'object'}}]});
        assert.ok(url.endsWith('/call'));
        return Response.json({ok:true,isError:false,text:'{"projects":[{"id":"project-1"}]}'});
      };
      const plans=[{tool:'list_projects',arguments:{}},{answer:'Найден проект project-1.'}];
      const user={role:'user',content:'Покажи мои проекты',requestId:'request-1234'};
      const answer=await runCloudAgent({provider,model,user,history:[user],signal:new AbortController().signal,progress:()=>{},persist:async()=>{},
        request:async(path,payload)=>{
          assert.equal(path,'connections/'+provider+'/plan');assert.equal(payload.model,model);
          if(plans.length===1)assert.ok(payload.messages.at(-1).content.includes('project-1'));
          const event={choices:[{delta:{content:JSON.stringify(plans.shift())},finish_reason:'stop'}]};
          return new Response('data: '+JSON.stringify(event)+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});
        }});
      assert.equal(answer,'Найден проект project-1.');assert.equal(calls.filter(call=>call.url.endsWith('/call')).length,1);
      await assert.rejects(runCloudAgent({provider,user}),/уже выполнялось/);
    }finally{globalThis.fetch=original;}
  });
}

for (const approved of [true,false]) test('calendar create requires direct request and explicit approval: '+approved,async()=>{
  const previousFetch=globalThis.fetch,previousWindow=globalThis.window,calls=[];
  try{
    globalThis.window={confirm(message){assert.ok(message.includes('Investor call'));assert.ok(message.includes('2027-01-01'));return approved;}};
    globalThis.fetch=async(url,options)=>{
      calls.push(url);
      if(url.endsWith('/verify'))return Response.json({ok:true,connected:true,connection_id:'google-connection'});
      if(url.endsWith('/tools'))return Response.json({ok:true,tools:[{name:'calendar_event_create',description:'Create calendar event',inputSchema:{type:'object'}}]});
      assert.ok(url.endsWith('/call'));
      return Response.json({ok:true,text:'{"created":true,"event":{"id":"new-event"}}'});
    };
    const event={title:'Investor call',start:'2027-01-01T12:00:00+03:00',end:'2027-01-01T13:00:00+03:00'};
    const steps=[{tool:'calendar_event_create',arguments:event},{answer:'Создала событие.'}];
    const user={role:'user',content:'Создай встречу Investor call',requestId:'calendar-req-1'};
    const answer=await runCloudAgent({provider:'google',model:'velia-flash',user,history:[user],signal:new AbortController().signal,
      progress:()=>{},persist:async()=>{},request:async()=>{
        const plan=steps.shift();
        return new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(plan)},finish_reason:'stop'}]})+'\\n\\ndata: [DONE]\\n\\n',
          {headers:{'Content-Type':'text/event-stream'}});
      }});
    assert.equal(calls.filter(url=>url.endsWith('/call')).length,approved?1:0);
    assert.ok(answer.includes(approved?'Создала':'отменено'));
  }finally{globalThis.fetch=previousFetch;globalThis.window=previousWindow;}
});

test('a calendar read request cannot silently create an event',async()=>{
  const previousFetch=globalThis.fetch,previousWindow=globalThis.window;
  try{
    globalThis.window={confirm(){throw Error('unexpected confirmation');}};
    let writes=0;
    globalThis.fetch=async(url)=>{
      if(url.endsWith('/verify'))return Response.json({ok:true,connected:true,connection_id:'google-connection'});
      if(url.endsWith('/tools'))return Response.json({ok:true,tools:[{name:'calendar_event_create',description:'Create event',inputSchema:{type:'object'}}]});
      writes++;return Response.json({ok:true});
    };
    const user={role:'user',content:'Покажи мои встречи',requestId:'calendar-req-2'};
    const answer=await runCloudAgent({provider:'google',model:'velia-flash',user,history:[user],signal:new AbortController().signal,
      progress:()=>{},persist:async()=>{},request:async()=>new Response(
        'data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify({tool:'calendar_event_create',arguments:{title:'Unwanted',start:'2027-01-01T12:00:00+03:00',end:'2027-01-01T13:00:00+03:00'}})},finish_reason:'stop'}]})+'\\n\\ndata: [DONE]\\n\\n',
        {headers:{'Content-Type':'text/event-stream'}})});
    assert.equal(writes,0);assert.match(answer,/прямо создать/);
  }finally{globalThis.fetch=previousFetch;globalThis.window=previousWindow;}
});
