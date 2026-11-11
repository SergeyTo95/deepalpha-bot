import test from 'node:test';
import assert from 'node:assert/strict';
import {wantsCrm,crmFollowup,crmResult,crmChange,parseCrmPlan,runCrmAgent} from '../crm.mjs';

const contact={id:'contact-anna',name:'Анна',company:'Фонд А',roles:['investor'],email:'anna@example.org',phone:'',telegram:'',notes:'Обсудить Velia',tags:[],projects:['Velia'],status:'active',links:[],last_contact_date:'2026-10-01',days_since_contact:41,waiting_reply:[],waiting_count:1,overdue_count:0,next_actions:[{kind:'reply',title:'Подготовить ответ на входящее',date:'2026-10-01'}],tasks:[],interactions:[],interaction_count:0};
const result={view:'contacts',today:'2026-11-11',time_zone:'Europe/Istanbul',contacts:[contact],total:1,more:false,offset:0,deals:[],deal_count:0,unknown_history_count:0};
const context={ok:true,configured:true,revision:'v1',today:'2026-11-11',time_zone:'Europe/Istanbul',contacts:[{id:contact.id,name:contact.name,company:contact.company,roles:contact.roles}]};
const user=()=>({role:'user',content:'',requestId:'170aa0cf-6e95-4d11-a269-a3f753d02b64'});
const stream=plan=>new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(plan)},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});

test('CRM intent and followups distinguish generic and financial questions',()=>{
  for(const text of ['Импортируй письма инвестора из Gmail в CRM','Добавь инвестора Анну','Кому из инвесторов мы давно не отвечали?','Покажи сделки по Velia','Сохрани карточку контакта в заметки','Назначь встречу с инвестором Анной','Напомни позвонить клиенту'])assert.equal(wantsCrm(text),true,text);
  for(const text of ['Что такое CRM?','Покажи финансовый резерв','Погода в Анталии'])assert.equal(wantsCrm(text),false,text);
  assert.equal(crmFollowup('Сохрани её в заметки'),true);assert.equal(crmFollowup('А ему встречу завтра'),true);assert.equal(crmFollowup('Покажи расходы'),false);
  for(const bad of ['[]','{}','{"action":"send_email"}','{"action":"query","user_id":8}'])assert.throws(()=>parseCrmPlan(bad));
});

test('CRM report distinguishes unknown history and never claims live mail or money',()=>{
  const text=crmResult({...result,view:'pipeline',deals:[{title:'Инвестиции',stage:'negotiation',project:'Velia',value:'10000.50',currency:'USD'}],unknown_history_count:2});
  assert.match(text,/автоматически не проверялись/);assert.match(text,/Нельзя определить/);assert.match(text,/не является зачисленной выручкой/);assert.match(text,/10000\.50 USD/);
  assert.doesNotMatch(text,/contact-anna/);
  const preview=crmChange({type:'batch',changes:[{type:'add_contact',before:null,after:{id:'private-id',name:'Анна',roles:['investor']}},{type:'delete_contact',before:{name:'Борис',related_removed:{interactions:2,tasks:1,deals:0}},after:null}]});
  assert.match(preview,/Инвестор/);assert.match(preview,/записей общения 2/);assert.doesNotMatch(preview,/private-id|object Object/);
});

for(const approved of [true,false])test('Gmail import previews actual snippets and commits only reviewed token: '+approved,async()=>{
  const previous=globalThis.fetch,calls=[];let step=0;
  try{
    globalThis.fetch=async(url,options)=>{
      const data=JSON.parse(options.body);calls.push({url,data});
      return Response.json(url.endsWith('/context')?context:url.endsWith('/query')?{ok:true,revision:'v2',result:{...result,view:'card'}}:
        url.endsWith('/mail-preview')?{ok:true,count:1,token:'sealed-provider-evidence',mailbox:'me@example.org',duplicates:2,skipped:0,more:true,preview:[{date:'2026-10-02',direction:'inbound',summary:'Actual provider subject'}]}:{ok:true,count:1,revision:'v3'});
    };
    const reply=await runCrmAgent({text:'Импортируй письма инвестора Анны из Gmail в CRM за октябрь 2026',model:'velia-flash',user:user(),signal:new AbortController().signal,
      confirm:text=>{assert.match(text,/Actual provider subject/);assert.match(text,/me@example.org/);assert.match(text,/без вложений/);return approved;},
      request:async()=>stream(step++===0?{action:'lookup',query:{view:'card',search:'Анна'}}:{action:'mail_import',contact_id:contact.id,start_date:'2026-10-01',end_date:'2026-10-31'})});
    const preview=calls.find(c=>c.url.endsWith('/mail-preview'));assert.equal(preview.data.revision,'v2');assert.equal(preview.data.end_date,'2026-10-31');
    assert.equal(calls.some(c=>c.url.endsWith('/mail-commit')),approved);
    if(approved){assert.deepEqual(calls.at(-1).data,{token:'sealed-provider-evidence',confirmed:true});assert.match(reply,/сохранено писем: 1/);assert.match(reply,/часть переписки/);}
    else assert.match(reply,/отменён/);
  }finally{globalThis.fetch=previous;}
});

test('read-only CRM request cannot trigger Gmail import selected by model',async()=>{
  const previous=globalThis.fetch;
  try{
    globalThis.fetch=async url=>{assert.ok(!url.includes('mail-'));return Response.json(url.endsWith('/context')?context:{ok:true,revision:'v1',result});};
    await assert.rejects(runCrmAgent({text:'Кому из инвесторов не отвечали?',model:'velia-pro',user:user(),signal:new AbortController().signal,
      request:async()=>stream({action:'mail_import',contact_id:contact.id,start_date:'2026-10-01',end_date:'2026-10-31'})}),/попроси импортировать/);
  }finally{globalThis.fetch=previous;}
});

test('empty Gmail preview never commits and reports bounded scope',async()=>{
  const previous=globalThis.fetch;
  try{
    globalThis.fetch=async url=>{
      assert.ok(!url.endsWith('/mail-commit'));
      return Response.json(url.endsWith('/context')?context:url.endsWith('/query')?{ok:true,revision:'v1',result}:
        {ok:true,count:0,mailbox:'me@example.org',duplicates:8,skipped:1,more:true});
    };
    const reply=await runCrmAgent({text:'Загрузи переписку с контактом из почты в CRM за октябрь',model:'velia-quantum',user:user(),signal:new AbortController().signal,
      confirm:()=>{throw Error('No confirmation for empty preview');},request:async()=>stream({action:'mail_import',contact_id:contact.id,start_date:'2026-10-01',end_date:'2026-10-31'})});
    assert.match(reply,/Новых писем.*не найдено/);assert.match(reply,/Уже в CRM: 8/);assert.match(reply,/часть переписки/);
  }finally{globalThis.fetch=previous;}
});

for(const model of ['velia-flash','velia-quantum','velia-pro'])test('query uses selected model and authoritative CRM: '+model,async()=>{
  const previous=globalThis.fetch,calls=[];
  try{
    globalThis.fetch=async(url,options)=>{calls.push({url,data:JSON.parse(options.body)});return Response.json(url.endsWith('/context')?context:{ok:true,revision:'v1',result});};
    const reply=await runCrmAgent({text:'Кому из инвесторов не отвечали?',model,user:user(),signal:new AbortController().signal,
      request:async(path,payload)=>{assert.equal(path,'crm/plan');assert.equal(payload.model,model);assert.equal(payload.web_search,false);return stream({action:'query',query:{view:'waiting_reply',role:'investor'}});}});
    assert.match(reply,/Анна/);assert.equal(calls.at(-1).data.query.role,'investor');
    assert.equal(calls.some(c=>c.url.endsWith('/mutate')),false);
  }finally{globalThis.fetch=previous;}
});

for(const approved of [true,false])test('mutation retrieves IDs and requires reviewed confirmation: '+approved,async()=>{
  const previous=globalThis.fetch,calls=[];let step=0;
  try{
    const change={type:'update_contact',before:{name:'Анна',company:'Фонд А'},after:{id:contact.id,name:'Анна',company:'Фонд B'}};
    globalThis.fetch=async(url,options)=>{calls.push({url,data:JSON.parse(options.body)});return Response.json(url.endsWith('/context')?context:url.endsWith('/query')?{ok:true,revision:'v2',result}:{ok:true,revision:'v3',change,result});};
    const reply=await runCrmAgent({text:'Исправь компанию Анны на Фонд B',model:'velia-flash',user:user(),signal:new AbortController().signal,
      confirm:text=>{assert.match(text,/Фонд А/);assert.match(text,/Фонд B/);return approved;},
      request:async()=>stream(step++===0?{action:'lookup',query:{search:'Анна',view:'card'}}:{action:'mutate',edit:{type:'update_contact',contact_id:contact.id,changes:{company:'Фонд B'}}})});
    assert.equal(calls.some(c=>c.url.endsWith('/mutate')),approved);
    assert.equal(calls.find(c=>c.url.endsWith('/preview')).data.revision,'v2');assert.match(reply,approved?/CRM обновлена/:/отменено/);
  }finally{globalThis.fetch=previous;}
});

test('account changes stop before reading or writing CRM after planning',async()=>{
  const previous=globalThis.fetch;let guards=0;
  try{
    globalThis.fetch=async url=>{assert.ok(url.endsWith('/context'));return Response.json(context);};
    await assert.rejects(runCrmAgent({text:'Добавь контакт',model:'velia-pro',user:user(),signal:new AbortController().signal,
      guard:()=>{if(++guards>2)throw new DOMException('Account changed','AbortError');},request:async()=>stream({action:'mutate',edit:{type:'add_contact',contact:{name:'Анна'}}})}),/Account changed/);
  }finally{globalThis.fetch=previous;}
});

test('ambiguous contacts cannot create a note for an arbitrarily chosen person',async()=>{
  const previous=globalThis.fetch;let step=0;
  try{
    globalThis.fetch=async url=>Response.json(url.endsWith('/context')?context:{ok:true,revision:'v1',result:{...result,total:2,contacts:[contact,{...contact,id:'second',company:'Фонд B'}]}});
    await assert.rejects(runCrmAgent({text:'Сохрани контакт Анны в заметки',model:'velia-pro',user:user(),signal:new AbortController().signal,
      notebook:{save:()=>{throw Error('Must not save ambiguous contact');}},request:async()=>stream(step++===0?{action:'lookup',query:{search:'Анна'}}:{action:'note',contact_id:contact.id})}),/несколько контактов/);
  }finally{globalThis.fetch=previous;}
});

test('notebook export saves authoritative card and records its link',async()=>{
  const previous=globalThis.fetch,calls=[],notes=[];
  try{
    globalThis.fetch=async(url,options)=>{calls.push({url,data:JSON.parse(options.body)});return Response.json(url.endsWith('/context')?context:{ok:true,revision:'v2',result:{...result,view:'card'}});};
    const reply=await runCrmAgent({text:'Сохрани карточку контакта в заметки',model:'velia-flash',user:user(),signal:new AbortController().signal,confirm:()=>true,
      notebook:{save:async note=>{notes.push(note);return note.id;}},request:async()=>stream({action:'note',contact_id:contact.id})});
    assert.equal(notes[0].id,'crm:'+contact.id);assert.match(notes[0].content,/Анна/);assert.match(reply,/связана/);
    assert.equal(calls.at(-1).data.edit.type,'link_contact');assert.equal(calls.at(-1).data.edit.link.kind,'note');
  }finally{globalThis.fetch=previous;}
});

test('reminder uses real Pulse result and links only after success',async()=>{
  const previous=globalThis.fetch,calls=[],created=[];
  try{
    globalThis.fetch=async(url,options)=>{calls.push({url,data:JSON.parse(options.body)});return Response.json(url.endsWith('/context')?context:{ok:true,revision:'v2',result:{...result,view:'card'}});};
    const u=user(),reply=await runCrmAgent({text:'Напомни позвонить инвестору завтра в 10',model:'velia-quantum',user:u,signal:new AbortController().signal,confirm:()=>true,
      chatResource:{kind:'chat',id:'chat-owned'},pulse:{create:async(draft,id)=>{created.push({draft,id});return {item_id:'pulse-owned',due_at:1794474000,timezone:'Europe/Istanbul'};}},
      request:async()=>stream({action:'remind',contact_id:contact.id,draft:{title:'Позвонить Анне',timezone:'Europe/Istanbul',schedule:{kind:'once',date:'2026-11-12',time:'10:00'},channels:['email']}})});
    assert.equal(created[0].id,u.requestId);assert.equal(created[0].draft.channels,undefined);assert.equal(created[0].draft.resource.id,'chat-owned');
    assert.match(reply,/Напоминание создано/);assert.equal(calls.at(-1).data.edit.link.kind,'pulse');
  }finally{globalThis.fetch=previous;}
});

test('calendar link comes from provider evidence even if final model answer fails',async()=>{
  const previous=globalThis.fetch,calls=[];
  try{
    globalThis.fetch=async(url,options)=>{calls.push({url,data:JSON.parse(options.body)});return Response.json(url.endsWith('/context')?context:{ok:true,revision:'v2',result:{...result,view:'card'}});};
    const event={id:'google-event',title:'Встреча с Анной',url:'https://calendar.google.com/calendar/event?eid=fixture'};
    const reply=await runCrmAgent({text:'Назначь встречу с инвестором Анной завтра',model:'velia-pro',user:user(),signal:new AbortController().signal,
      request:async()=>stream({action:'calendar',contact_id:contact.id}),runCalendar:async options=>{assert.equal(options.model,'velia-pro');assert.deepEqual(options.allowedTools,['calendar_events_list','calendar_freebusy','calendar_event_create']);assert.match(options.user.content,/Не отправляй приглашения/);options.onToolResult('calendar_event_create',{isError:false,text:JSON.stringify({created:true,event})});throw Error('Model response timeout');}});
    assert.match(reply,/Встреча создана/);assert.equal(calls.at(-1).data.edit.link.url,event.url);
  }finally{globalThis.fetch=previous;}
});

test('read-only request cannot silently call calendar or export notes',async()=>{
  const previous=globalThis.fetch;
  try{
    globalThis.fetch=async url=>Response.json(url.endsWith('/context')?context:{ok:true,revision:'v1',result:{...result,view:'card'}});
    for(const action of ['note','calendar','remind'])await assert.rejects(runCrmAgent({text:'Покажи контакты инвесторов',model:'velia-flash',user:user(),signal:new AbortController().signal,
      request:async()=>stream({action,contact_id:contact.id}),notebook:{save:()=>{throw Error('Must not save');}},runCalendar:()=>{throw Error('Must not create');}}),/попроси|Уточни|можно создать/);
  }finally{globalThis.fetch=previous;}
});

test('preparing an answer is visibly unsent and never creates outbound history',async()=>{
  const previous=globalThis.fetch,calls=[];
  try{
    globalThis.fetch=async url=>{calls.push(url);assert.ok(url.endsWith('/context'));return Response.json(context);};
    const reply=await runCrmAgent({text:'Подготовь ответ инвестору Анне',model:'velia-pro',user:user(),signal:new AbortController().signal,
      request:async()=>stream({answer:'Анна, спасибо за интерес к проекту.'})});
    assert.match(reply,/Черновик — не отправлен/);assert.equal(calls.length,1);
  }finally{globalThis.fetch=previous;}
});
