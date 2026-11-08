import test from 'node:test';
import assert from 'node:assert/strict';
import {reminderDraft,readyDraft,wantsPulse,setupPulse,pulseDateReply,pulseMessageContext,pulseWhen} from '../pulse.mjs';
import {runPulseCommand} from '../pulse-agent.mjs';
const instant=Date.parse('2026-11-08T21:30:00Z'),timezone='Europe/Istanbul';
function draft(text,pending){return reminderDraft(text,{now:instant,timezone,pending});}
test('reminder dates follow the device timezone and preserve repeats',()=>{
  const value=draft('Напомни завтра в 10 позвонить клиенту');assert.equal(value.schedule.date,'2026-11-10');assert.equal(value.schedule.time,'10:00');assert.equal(value.title,'позвонить клиенту');assert.ok(readyDraft(value));
  assert.deepEqual(draft('Напомни каждый день в 9:15 проверить заказы').schedule,{kind:'daily',time:'09:15'});
  assert.deepEqual(draft('Напомни каждую пятницу в 18 проверить оплату').schedule,{kind:'weekly',time:'18:00',weekdays:[4]});
  assert.equal(draft('Напомни в пятницу в 10 проверить оплату').schedule.date,'2026-11-13');
  const relative=draft('Напомни через 20 минут проверить письмо');assert.equal(relative.schedule.time,'00:50');assert.equal(relative.schedule.date,'2026-11-09');
});
test('missing dates/times remain drafts and unrelated questions do not create reminders',()=>{
  const value=draft('Напомни позвонить Ивану');assert.equal(readyDraft(value),false);
  const completed=draft('завтра в 10',value);assert.equal(completed.title,'позвонить Ивану');assert.ok(readyDraft(completed));
  assert.equal(draft('Расскажи про напоминания'),null);assert.equal(wantsPulse('Нужно бы напомнить об оплате'),false);
  assert.ok(wantsPulse('Напомнить мне завтра в 10 позвонить'));assert.equal(wantsPulse('Пульс 110 — это нормально?'),false);assert.equal(pulseDateReply('Сегодня пульс 110'),false);assert.ok(pulseDateReply('в пятницу в 10'));assert.ok(pulseDateReply('10:30'));
  assert.ok(wantsPulse('Напомни завтра в 10 позвонить'));assert.ok(wantsPulse('Покажи мои напоминания'));
});
test('relative minutes keep seconds instead of rounding to the next minute',()=>{
  const now=Date.parse('2026-11-08T12:27:23Z');
  const value=reminderDraft('Напомни через 1 минуту проверить уведомления',{now,timezone});
  assert.deepEqual(value.schedule,{kind:'once',date:'2026-11-08',time:'15:28',second:23});
  assert.ok(pulseWhen({due_at:(now+60000)/1000,timezone}).includes('15:28:23'));
  const midnight=reminderDraft('Напомни через 1 минуту проверить уведомления',{now:Date.parse('2026-11-08T20:59:43Z'),timezone});
  assert.deepEqual(midnight.schedule,{kind:'once',date:'2026-11-09',time:'00:00',second:43});
});
test('chat snooze carries the exact second to the server',async()=>{
  const f=fixture();f.items.push({item_id:'selected',title:'Проверить',timezone,state:'active',revision:1,due_at:instant/1000+3600});
  await runPulseCommand({text:'Перенеси это напоминание через 1 минуту',user:{},history:[{role:'assistant',pulseIds:['selected']}],pulse:f.pulse,model:'velia-flash',now:Date.parse('2026-11-08T12:27:23Z'),request:async()=>{throw Error('Unneeded model call');}});
  assert.deepEqual(f.calls[0],{action:'snooze',extra:{date:'2026-11-08',time:'15:28',second:23,timezone}});
});
function fixture(){
  let items=[],calls=[];
  const pulse={state:()=>({items,notifications:[]}),sync:async()=>{},create:async(data,id)=>{calls.push(data);const item={...data,item_id:id,revision:1,state:'active',due_at:instant/1000+3600,notify_at:instant/1000+3600};items.unshift(item);return item;},act:async(item,action,extra)=>{calls.push({action,extra});return {...item,state:action==='complete'?'done':action==='cancel'?'cancelled':'active',revision:item.revision+1};}};
  return {pulse,calls,items};
}
for(const model of ['velia-flash','velia-quantum','velia-pro'])test(model+' saves a simple reminder without calling a model and marks only the referenced item',async()=>{
  const f=fixture(),user={role:'user',content:'Напомни завтра в 10 позвонить клиенту'};
  const first=await runPulseCommand({text:user.content,user,pulse:f.pulse,model,now:instant,request:async()=>{throw Error('Unneeded model call');}});assert.equal(f.calls.length,1);assert.equal(first.ids.length,1);
  const finish=await runPulseCommand({text:'Отметь первое напоминание выполненным. Результат: дозвонился',user:{},history:[{role:'assistant',pulseIds:first.ids}],pulse:f.pulse,model,now:instant,request:async()=>{throw Error('Unneeded model call');}});assert.equal(f.calls[1].action,'complete');assert.equal(f.calls[1].extra.result,'дозвонился');assert.ok(finish.reply.includes('выполнение'));
});
for(const model of ['velia-flash','velia-quantum','velia-pro'])test(model+' uses its own planner for a nontrivial request and never trusts unexecuted success text',async()=>{
  const f=fixture(),content='Напомни через полчаса проверить почту';let seen;
  const request=async(path,data)=>{seen=data.model;assert.ok(!JSON.stringify(data.messages).includes('UNRELATED_PRIVATE_REMINDER'));return new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify({tool:'create_reminder',arguments:{title:'Проверить почту',timezone:'Europe/Istanbul',schedule:{kind:'once',date:'2026-11-09',time:'01:00'}}})},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});};
  f.items.push({item_id:'private-existing',title:'UNRELATED_PRIVATE_REMINDER',timezone:'UTC',state:'active'});
  const result=await runPulseCommand({text:content,user:{},pulse:f.pulse,model,now:instant,request});assert.equal(seen,model);assert.equal(f.calls.length,1);assert.equal(result.ids.length,1);
  const bad=await runPulseCommand({text:content,user:{},pulse:f.pulse,model,now:instant,request:async()=>new Response('data: '+JSON.stringify({choices:[{delta:{content:'{"answer":"Сохранила напоминание."}'},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}})});assert.ok(!bad.reply.includes('Сохранила'));assert.equal(f.calls.length,1);
});
test('late Pulse responses after logout cannot restore another account inbox',async()=>{
  const previousDocument=globalThis.document;globalThis.document={addEventListener(){},hidden:false};let profile={account:'a'.repeat(24)},finish;
  try{const api=setupPulse({profile:()=>profile,request:()=>new Promise(resolve=>finish=resolve)});const pending=api.sync();profile=null;api.reset();finish({items:[{title:'private'}],notifications:[]});await assert.rejects(pending,{name:'AbortError'});assert.deepEqual(api.state().items,[]);}finally{globalThis.document=previousDocument;}
});


test('a cookie account change cannot place another user inbox under the old profile',async()=>{
  const previousDocument=globalThis.document;globalThis.document={addEventListener(){},hidden:false};let changed=false;
  try{const api=setupPulse({profile:()=>({account:'a'.repeat(24)}),accountChanged:()=>changed=true,request:async()=>({account:'b'.repeat(24),items:[{title:'another-account-private'}],notifications:[]})});await assert.rejects(api.sync(),{name:'AbortError'});assert.equal(changed,true);assert.deepEqual(api.state().items,[]);}finally{globalThis.document=previousDocument;}
});


test('ordinary chat receives only already displayed reminders for follow-up planning',()=>{
  const message={role:'assistant',content:'Нашла напоминание.',pulseIds:['own-selected']};
  const rows=[{item_id:'own-selected',title:'Позвонить клиенту',state:'active',due_at:1794200000,timezone:'Europe/Istanbul',schedule:{kind:'once',date:'2026-11-09',time:'10:00'}},{item_id:'unrelated',title:'UNRELATED_PRIVATE_REMINDER',state:'active'}];
  const context=pulseMessageContext(message,rows);assert.ok(context.includes('Позвонить клиенту'));assert.ok(!context.includes('UNRELATED_PRIVATE_REMINDER'));assert.equal(pulseMessageContext(message,[]),message.content);
});
