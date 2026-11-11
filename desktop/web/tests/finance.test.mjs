import test from 'node:test';
import assert from 'node:assert/strict';
import {wantsFinance,financeFollowup,financeRangeFollowup,parseFinancePlan,financeReport,runFinanceAgent} from '../finance.mjs';

const report={period_start:'2026-10-01',period_end:'2026-10-30',source:'User data',reserve_months:6,warnings:[],rows:[{
  project:null,currency:'USD',available:'850',monthly_expense:'300',reserve_required:'1800',reserve_gap:'950',runway_months:'8.5',operating_cash_flow:'-100',forecast:{3:'550',6:'250',12:'-350'},stress_runway_months:'4.25',monthly_budget:null
}]};
const stream=plan=>new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(plan)},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});

test('financial intents do not capture calendar or generic questions',()=>{
  for (const text of ['Собери финансовое состояние всех моих проектов и рассчитай денежный резерв','Проанализируй выписку','Покажи финансовую сводку','Проверь расходы','Сохрани финансовые данные','Удали финансовые данные','Calculate cashflow','Сегодня я в плюсе или минусе?','Запиши доход 500 USD сегодня','Исправь доход на 450 USD','Вчера потратил 50 EUR']) assert.equal(wantsFinance(text),true,text);
  for (const text of ['Назначь встречу','Что такое бюджет?','Покажи карту','Что такое финансовый аналитик?']) assert.equal(wantsFinance(text),false,text);
  assert.equal(financeFollowup('Остаток 1000 USD на 30 октября'),true);
  assert.equal(financeFollowup('Погода на завтра 20 градусов?'),false);
  assert.equal(financeRangeFollowup('А за прошлый месяц?'),true);
  assert.equal(financeRangeFollowup('Сегодня погода минус 5 градусов?'),false);
  assert.equal(wantsFinance('Сегодня минус 5 градусов?'),false);
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
    assert.match(reply,/850/);assert.equal(calls.length,2);assert.ok(calls.at(-1).url.endsWith('/analyze'));
  } finally {globalThis.fetch=previous;}
});

for (const approved of [true,false]) test('snapshot save requires reviewed confirmation: '+approved,async()=>{
  const previous=globalThis.fetch,calls=[];
  try {
    globalThis.fetch=async(url,options)=>{
      calls.push({url,data:JSON.parse(options.body)});
      return Response.json(url.endsWith('/analyze')?{ok:true,report}:url.endsWith('/load')?{ok:true,revision:'current',snapshot:{}}:{ok:true,revision:'new'});
    };
    const reply=await runFinanceAgent({text:'Замени все финансовые данные',history:[],model:'velia-flash',signal:new AbortController().signal,
      confirm:message=>{assert.match(message,/850/);assert.match(message,/Заменить/);return approved;},request:async()=>stream({action:'save',snapshot:{source:'test'}})});
    assert.equal(calls.some(c=>c.url.endsWith('/save')),approved);
    assert.match(reply,approved?/сохранена/:/отменено/);
    if(approved) assert.deepEqual(calls.at(-1).data,{snapshot:{source:'test'},revision:'current',confirmed:true});
  } finally {globalThis.fetch=previous;}
});

test('account change prevents saving after planning',async()=>{
  const previous=globalThis.fetch;
  try {
    globalThis.fetch=url=>{assert.ok(url.endsWith('/context'));return Response.json({ok:true,revision:''});};
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

for(const approved of [true,false])test('edit selects real entry, previews before/after and preserves date filter: '+approved,async()=>{
  const previous=globalThis.fetch,calls=[],plans=[];
  const query={period_start:'2026-10-03',period_end:'2026-10-03'};
  const before={id:'entry-server-id',account_id:'bank',date:'2026-10-03',kind:'revenue',amount:'500'};
  const after={...before,amount:'450'};
  const change={type:'update_transaction',before,after};
  try{
    globalThis.fetch=async(url,options)=>{
      const data=JSON.parse(options.body);calls.push({url,data});
      if(url.endsWith('/context'))return Response.json({ok:true,revision:'version-1',today:'2026-10-04',time_zone:'Europe/Istanbul',accounts:[{id:'bank',project:'Velia',currency:'USD'}]});
      if(url.endsWith('/analyze'))return Response.json({ok:true,report:{...report,transactions:[before],transaction_count:1}});
      return Response.json({ok:true,revision:url.endsWith('/preview')?'version-1':'version-2',change,report:{...report,period_start:query.period_start,period_end:query.period_end}});
    };
    let round=0,selected;
    const reply=await runFinanceAgent({text:'Исправь доход на 450',history:[],model:'velia-flash',signal:new AbortController().signal,
      timeZone:'Europe/Istanbul',operationId:'stable-request',selectedQuery:query,onQuery:value=>{selected=value;},
      confirm:message=>{assert.match(message,/500/);assert.match(message,/450/);return approved;},
      request:async(path,payload)=>{plans.push(payload);return stream(round++===0?{action:'entries'}:
        {action:'mutate',edit:{type:'update_transaction',transaction_id:'entry-server-id',changes:{amount:'450'}}});}});
    assert.equal(plans.length,2);assert.match(plans[1].messages.at(-1).content,/entry-server-id/);
    assert.equal(calls.some(c=>c.url.endsWith('/mutate')),approved);
    for(const call of calls.filter(c=>/\/(analyze|preview|mutate)$/.test(c.url)))assert.deepEqual(call.data.query,query);
    if(approved){assert.equal(calls.at(-1).data.operation_id,'stable-request');assert.equal(calls.at(-1).data.revision,'version-1');assert.equal(selected.period_start,'2026-10-03');assert.match(reply,/обновлена/);}
    else assert.match(reply,/отменено/);
  }finally{globalThis.fetch=previous;}
});

test('today analytics sends browser time zone and renders plus without invented balance',async()=>{
  const previous=globalThis.fetch;
  try{
    globalThis.fetch=async(url,options)=>{
      const data=JSON.parse(options.body);assert.equal(data.time_zone,'Europe/Istanbul');
      if(url.endsWith('/context'))return Response.json({ok:true,today:'2026-10-04',revision:'current',accounts:[]});
      assert.deepEqual(data.query,{preset:'today'});
      return Response.json({ok:true,report:{...report,period_start:'2026-10-04',period_end:'2026-10-04',time_zone:'Europe/Istanbul',warnings:['closing_balance_missing_for_selected_period'],rows:[{
        ...report.rows[0],revenue:'500',expense:'50',operating_cash_flow:'450',result:'plus',available:null,reserve_gap:null,forecast:null,runway_months:null
      }]}});
    };
    const reply=await runFinanceAgent({text:'Сегодня плюс или минус?',history:[],model:'velia-pro',timeZone:'Europe/Istanbul',signal:new AbortController().signal,
      request:async(path,payload)=>{assert.match(payload.messages[0].content,/2026-10-04/);return stream({action:'report',query:{preset:'today'}});}});
    assert.match(reply,/плюс \+450/);assert.match(reply,/Остаток на конец выбранного периода неизвестен/);assert.doesNotMatch(reply,/8\.5 мес|550 \/ 250/);
  }finally{globalThis.fetch=previous;}
});

for(const action of ['save','delete'])test('single-income edit cannot replace or delete the whole ledger: '+action,async()=>{
  const previous=globalThis.fetch,calls=[];
  try{
    globalThis.fetch=async(url)=>{calls.push(url);return Response.json(url.endsWith('/context')?{ok:true,revision:'existing',accounts:[]}:{ok:true,report});};
    await assert.rejects(runFinanceAgent({text:'Исправь этот доход на 450 USD',history:[],model:'velia-flash',signal:new AbortController().signal,
      confirm:()=>{throw new Error('must not confirm whole-history changes');},request:async()=>stream({action,...(action==='save'?{snapshot:{}}:{})})}),/история не заменена|Вся история не удалена/);
    assert.equal(calls.some(url=>url.endsWith('/save')||url.endsWith('/delete')),false);
  }finally{globalThis.fetch=previous;}
});
