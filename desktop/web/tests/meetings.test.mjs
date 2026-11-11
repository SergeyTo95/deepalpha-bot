import test from 'node:test';
import assert from 'node:assert/strict';
import {meetingChunks,wantsMeeting,verifyMeetingChunk,prepareMeeting,meetingReport,meetingEmail,saveMeetingTask,transcribeMeeting} from '../meetings.mjs';

const source='Анна: Я отправлю презентацию 2026-11-20.\nБорис: Решили начать пилот.\nАнна: Сколько будет стоить поддержка?';
const plan=()=>({highlights:[],decisions:[{text:'Начать пилот',evidence:'Борис: Решили начать пилот.'}],actions:[{text:'Отправить презентацию',evidence:'Анна: Я отправлю презентацию 2026-11-20.',owner:'Анна',due_date:'2026-11-20'}],questions:[{text:'Цена поддержки',evidence:'Анна: Сколько будет стоить поддержка?'}]});
const stream=(p,finish='stop')=>new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(p)},finish_reason:finish}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});
const signal=()=>new AbortController().signal;

test('meeting intents leave calendar scheduling and CRM followups on their original routes',()=>{
  for(const text of ['Подготовь протокол встречи','Выдели решения из сегодняшнего совещания','Сделай итоги созвона','Расшифруй запись'])assert.equal(wantsMeeting(text),true,text);
  for(const text of ['Расшифруй анализ крови','Назначь встречу с инвестором','Добавь клиента Анну','Что такое протокол встречи?','Прогноз по акциям'])assert.equal(wantsMeeting(text),false,text);
  assert.equal(wantsMeeting('Проанализируй файл',[{type:'audio/mpeg'}]),true);
});

test('all source characters are covered by bounded chunks without silent truncation',()=>{
  const text=('0123456789'.repeat(599)+'\n').repeat(4)+'last word';
  const chunks=meetingChunks(text);assert.equal(chunks.map(c=>c.text).join(''),text);
  chunks.forEach((c,i)=>{assert.ok(c.text.length<=6000);assert.equal(c.offset,chunks.slice(0,i).reduce((n,p)=>n+p.text.length,0));});
  assert.throws(()=>meetingChunks('x'.repeat(30001)),/30 000/);assert.throws(()=>meetingChunks('  '),/расшифровку/);
});

test('unverified evidence and invented names or deadlines cannot become actionable fields',()=>{
  const input=plan();input.highlights=[{text:'Выручка миллион',evidence:'выручка миллион рублей'}];
  input.actions[0].owner='Сергей';input.actions[0].due_date='2026-11-30';
  const result=verifyMeetingChunk(JSON.stringify(input),{text:source,offset:10});
  assert.equal(result.rejected,1);assert.equal(result.actions[0].owner,'');assert.equal(result.actions[0].due_date,'');
  assert.equal(result.actions[0].offset,10);assert.equal(result.decisions[0].evidence,'Борис: Решили начать пилот.');
  const bad=plan();bad.actions[0].due_date='2026-02-30';assert.equal(verifyMeetingChunk(JSON.stringify(bad),{text:source,offset:0}).actions[0].due_date,'');
  assert.throws(()=>verifyMeetingChunk('{"tool":"send_email"}',{text:source,offset:0}));
});

for(const model of ['velia-flash','velia-quantum','velia-pro'])test('meeting extraction uses selected model without search or any writes: '+model,async()=>{
  const calls=[];const result=await prepareMeeting({source,title:'Пилот',model,signal:signal(),id:'meeting-owned',request:async(path,payload)=>{
    calls.push(path);assert.equal(path,'meetings/plan');assert.equal(payload.model,model);assert.equal(payload.web_search,false);return stream(plan());
  }});
  assert.equal(calls.length,1);assert.equal(result.actions[0].due_date,'2026-11-20');assert.deepEqual(result.taskReceipts,{});
  const report=meetingReport(result);assert.match(report,/сама по себе не создаёт/);assert.match(report,/Цитата:/);assert.match(meetingEmail(result),/не отправлен/);
});

test('partial inference failure never returns a complete-looking protocol',async()=>{
  let calls=0;
  await assert.rejects(prepareMeeting({source:'x'.repeat(6100),model:'velia-flash',signal:signal(),request:async()=>stream({highlights:[],decisions:[],actions:[],questions:[]},++calls===1?'stop':'length')}),/Полный протокол не подготовлен/);
  assert.equal(calls,2);
  const controller=new AbortController();controller.abort();
  await assert.rejects(prepareMeeting({source,model:'velia-flash',signal:controller.signal,request:()=>{throw Error('must not call');}}),{name:'AbortError'});
});

for(const approve of [true,false])test('saving meeting action in CRM requires contact selection, review and a stable receipt: '+approve,async()=>{
  const previous=globalThis.fetch,calls=[];
  try{
    const meeting={id:'meeting-owned',title:'Встреча',taskReceipts:{},actions:[{text:'Отправить презентацию',evidence:'Анна: Я отправлю презентацию 2026-11-20.',owner:'Анна',due_date:'2026-11-20'}]};
    globalThis.fetch=async(url,options)=>{const body=JSON.parse(options.body);calls.push({url,body});return Response.json(url.endsWith('/query')?{ok:true,revision:'r1',result:{contacts:[{id:'anna-exact',name:'Анна · Фонд А'}]}}:url.endsWith('/preview')?{ok:true,change:{type:'add_task',before:null,after:{title:body.edit.task.title}}}:{ok:true,revision:'r2'});};
    let persists=0;const result=await saveMeetingTask({meeting,index:0,contactId:'anna-exact',signal:signal(),persist:async()=>{persists++;},confirm:message=>{assert.match(message,/Фонд А/);return approve;}});
    assert.equal(Boolean(result),approve);assert.equal(calls.some(c=>c.url.endsWith('/mutate')),approve);
    const preview=calls.find(c=>c.url.endsWith('/preview'));assert.equal(preview.body.edit.task.contact_id,'anna-exact');assert.equal(preview.body.edit.task.due_date,'2026-11-20');
    if(approve){assert.equal(meeting.taskReceipts[0].state,'saved');assert.equal(persists,2);assert.equal(calls.at(-1).body.operation_id,'meeting-owned:task:0');await assert.rejects(saveMeetingTask({meeting,index:0,contactId:'anna-exact',signal:signal()}),/уже передавалось/);}
    else assert.deepEqual(meeting.taskReceipts,{});
  }finally{globalThis.fetch=previous;}
});

test('a lost CRM response preserves an uncertain receipt rather than retrying the write',async()=>{
  const previous=globalThis.fetch;let mutations=0;
  try{
    const meeting={id:'meeting-owned',title:'Встреча',taskReceipts:{},actions:[{text:'Проверить пилот',evidence:'Проверить пилот после встречи',owner:'',due_date:''}]};
    globalThis.fetch=async url=>{if(url.endsWith('/mutate')){mutations++;throw Error('Network lost');}return Response.json(url.endsWith('/query')?{ok:true,revision:'r1',result:{contacts:[{name:'Анна'}]}}:{ok:true,change:{type:'add_task',before:null,after:{title:'Проверить пилот'}}});};
    const args={meeting,index:0,contactId:'anna',signal:signal(),confirm:()=>true};
    await assert.rejects(saveMeetingTask(args),/Network lost/);assert.equal(meeting.taskReceipts[0].state,'attempted');
    await assert.rejects(saveMeetingTask(args),/уже передавалось/);assert.equal(mutations,1);
  }finally{globalThis.fetch=previous;}
});

test('audio transport uses raw bytes, account credentials and explicit unavailable errors',async()=>{
  const previous=globalThis.fetch;
  try{
    const file=new File(['ID3 audio'],'meeting.mp3',{type:'audio/mpeg'});
    globalThis.fetch=async(url,options)=>{assert.equal(url,'/web-api/v1/meetings/transcribe');assert.equal(options.body,file);assert.equal(options.credentials,'same-origin');return Response.json({ok:true,text:source,duration_seconds:15});};
    assert.equal((await transcribeMeeting(file,{signal:signal()})).text,source);
    globalThis.fetch=async()=>Response.json({ok:false,error:'meeting_transcription_unavailable'},{status:503});
    await assert.rejects(transcribeMeeting(file,{signal:signal()}),/текстовую расшифровку/);
  }finally{globalThis.fetch=previous;}
});
