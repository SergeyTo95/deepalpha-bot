import test from 'node:test';
import assert from 'node:assert/strict';
import {wantsFinance,financeFollowup,parseFinancePlan,financeReport,runFinanceAgent} from '../finance.mjs';

const report={period_start:'2026-10-01',period_end:'2026-10-30',source:'User data',reserve_months:6,warnings:[],rows:[{
  project:null,currency:'USD',available:'850',monthly_expense:'300',reserve_required:'1800',reserve_gap:'950',runway_months:'8.5',operating_cash_flow:'-100',forecast:{3:'550',6:'250',12:'-350'},stress_runway_months:'4.25',monthly_budget:null
}]};
const stream=plan=>new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(plan)},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});

test('financial intents do not capture calendar or generic questions',()=>{
  for (const text of ['Собери финансовое состояние всех моих проектов и рассчитай денежный резерв','Проанализируй выписку','Покажи финансовую сводку','Проверь расходы','Сохрани финансовые данные','Удали финансовые данные','Calculate cashflow']) assert.equal(wantsFinance(text),true,text);
  for (const text of ['Назначь встречу','Что такое бюджет?','Покажи карту','Что такое финансовый аналитик?']) assert.equal(wantsFinance(text),false,text);
  assert.equal(financeFollowup('Остаток 1000 USD на 30 октября'),true);
  assert.equal(financeFollowup('Погода на завтра 20 градусов?'),false);
});

test('planner JSON is validated without executing arbitrary tools',()=>{
  assert.equal(parseFinancePlan('```json\n{"action":"report"}\n```').action,'report');
  assert.equal(parseFinancePlan('{"answer":"Какой период?"}').answer,'Какой период?');
  for (const value of ['{}','[]','{"action":"transfer"}','{"tool":"trade","arguments":{}}','{"action":"save"}']) assert.throws(()=>parseFinancePlan(value));
});

test('render uses authoritative decimals and marks empty flow as unknown',()=>{
  const text=financeReport(report);
  assert.match(text,/850/);assert.match(text,/8\.5 мес/);assert.match(text,/550 \/ 250 \/ -350/);
  assert.match(text,/не бухгалтерская прибыль/);
  const empty=financeReport({...report,warnings:['no_transactions_zero_flow_is_not_verified']});
  assert.match(empty,/нет данных/);assert.doesNotMatch(empty,/8\.5 мес|550 \/ 250/);
});

for (const model of ['velia-flash','velia-quantum','velia-pro']) test('selected model plans; server computes: '+model,async()=>{
  const previous=globalThis.fetch,calls=[];
  try {
    globalThis.fetch=async(url,options)=>{calls.push({url,data:JSON.parse(options.body)});return Response.json({ok:true,report});};
    const reply=await runFinanceAgent({text:'Покажи финансовую сводку',history:[],model,signal:new AbortController().signal,
      request:async(path,payload)=>{assert.equal(path,'finance/plan');assert.equal(payload.model,model);assert.equal(payload.web_search,false);return stream({action:'report'});}});
    assert.match(reply,/850/);assert.equal(calls.length,1);assert.ok(calls[0].url.endsWith('/analyze'));
  } finally {globalThis.fetch=previous;}
});

for (const approved of [true,false]) test('snapshot save requires reviewed confirmation: '+approved,async()=>{
  const previous=globalThis.fetch,calls=[];
  try {
    globalThis.fetch=async(url,options)=>{
      calls.push({url,data:JSON.parse(options.body)});
      return Response.json(url.endsWith('/analyze')?{ok:true,report}:url.endsWith('/load')?{ok:true,revision:'current',snapshot:{}}:{ok:true,revision:'new'});
    };
    const reply=await runFinanceAgent({text:'Сохрани финансовые данные',history:[],model:'velia-flash',signal:new AbortController().signal,
      confirm:message=>{assert.match(message,/850/);assert.match(message,/Заменить/);return approved;},request:async()=>stream({action:'save',snapshot:{source:'test'}})});
    assert.equal(calls.some(c=>c.url.endsWith('/save')),approved);
    assert.match(reply,approved?/сохранена/:/отменено/);
    if(approved) assert.deepEqual(calls.at(-1).data,{snapshot:{source:'test'},revision:'current',confirmed:true});
  } finally {globalThis.fetch=previous;}
});

test('account change prevents saving after planning',async()=>{
  const previous=globalThis.fetch;
  try {
    globalThis.fetch=()=>{throw new Error('must not call financial data route');};
    let calls=0;
    await assert.rejects(runFinanceAgent({text:'Сохрани финансовые данные',history:[],model:'velia-pro',signal:new AbortController().signal,
      guard:()=>{if(++calls>1)throw new DOMException('Account changed','AbortError');},request:async()=>stream({action:'save',snapshot:{}})}),/Account changed/);
  } finally {globalThis.fetch=previous;}
});

for (const approved of [true,false]) test('deletion requires confirmation and explains chat retention: '+approved,async()=>{
  const previous=globalThis.fetch,calls=[];
  try {
    globalThis.fetch=async(url,options)=>{calls.push({url,data:JSON.parse(options.body)});return Response.json(url.endsWith('/load')?{ok:true,revision:'current',snapshot:{source:'test'}}:{ok:true,deleted:true});};
    const reply=await runFinanceAgent({text:'Удали финансовые данные',history:[],model:'velia-flash',signal:new AbortController().signal,
      confirm:message=>{assert.match(message,/истории чата сохранятся/);return approved;},request:async()=>stream({action:'delete'})});
    assert.equal(calls.some(c=>c.url.endsWith('/delete')),approved);
    assert.match(reply,approved?/остаются в истории чата/:/отменено/);
  } finally {globalThis.fetch=previous;}
});
