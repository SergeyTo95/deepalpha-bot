import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {Notebook} from '../notebook.mjs';
import {validateAppSpec,validateAppState,initialAppState,calculateApp,appNumber,appCSV,appSummary,formatAppNumber} from '../mini-app-core.mjs';
import {wantsMiniApp,wantsMiniAppEdit,appClarification,startupApp,budgetApp,prepareMiniApp} from '../mini-apps.mjs';
const stream=(value,finish='stop')=>new Response('data: '+JSON.stringify({choices:[{delta:{content:typeof value==='string'?value:JSON.stringify(value)},finish_reason:finish}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});

test('startup scenarios calculate enterprise and equity value from editable inputs without currency conversion',()=>{
  const state=initialAppState(startupApp('TON'));state.values.debt=1000000;state.values.cash=200000;
  let result=calculateApp(state);assert.equal(result.outputs.enterprise,5000000);assert.equal(result.outputs.equity,4200000);
  state.values.multiple=7;result=calculateApp(state);assert.equal(result.outputs.equity,6200000);assert.equal(result.chart.points.find(p=>p.label==='2').values.equity,1200000);
  assert.match(appSummary(state),/TON/);assert.match(appSummary(state),/6.?200.?000/);
});
test('tables calculate each row, negative results, totals and chart series from current cells',()=>{
  const state=initialAppState(budgetApp('EUR'));let result=calculateApp(state);
  assert.equal(result.rows[1].cells.profit,-1000);assert.equal(result.totals.profit,9000);
  state.rows[1].cells.revenue=10000;result=calculateApp(state);assert.equal(result.totals.profit,11000);assert.equal(result.chart.points[1].values.profit,1000);
  assert.ok(!Object.hasOwn(state.rows[1].cells,'profit'),'derived cells are never accepted as editable raw values');
});
test('bounded expressions reject code, unknown references, cycles, prototype fields and excess nesting',()=>{
  for(const expr of ['globalThis',['fetch','https://evil.example'],['eval',1],['add','equity',1],['sub','missing',1]]){const s=startupApp();s.outputs[0].expr=expr;assert.throws(()=>validateAppSpec(s));}
  const deep=startupApp();let expr=1;for(let i=0;i<12;i++)expr=['add',expr,1];deep.outputs[0].expr=expr;assert.throws(()=>validateAppSpec(deep),/сложная/);
  const prototype=startupApp();prototype.inputs[0].id='constructor';assert.throws(()=>validateAppSpec(prototype));
  const html=startupApp();html.html='<script>alert(1)</script>';assert.throws(()=>validateAppSpec(html));
  const extra=initialAppState(startupApp());extra.values.network=1;assert.throws(()=>validateAppState(extra));
});
test('zero division and overflow produce errors, while if evaluates only the chosen branch',()=>{
  const s=startupApp();s.outputs=[{id:'ratio',label:'Отношение',expr:['div',10,'debt']}];delete s.chart;
  assert.throws(()=>calculateApp(initialAppState(s)),/Деление на ноль/);
  s.outputs[0].expr=['if',['eq','debt',0],0,['div',10,'debt']];assert.equal(calculateApp(initialAppState(s)).outputs.ratio,0);
  s.outputs[0].expr=['pow',1000000,12];assert.throws(()=>calculateApp(initialAppState(s)),/пределы/);
  s.outputs[0].expr=['pow',2,100];assert.throws(()=>calculateApp(initialAppState(s)),/степени/);
});
test('choices, chart units, row count, numeric bounds and zero are validated explicitly',()=>{
  const s=startupApp();s.inputs[1].choices=[{label:'Низкий',value:3},{label:'Высокий',value:5}];s.chart.scenario.values=[3,5];assert.ok(validateAppSpec(s));
  const state=initialAppState(s);state.values.multiple=4;assert.throws(()=>validateAppState(state));
  const mixed=startupApp();mixed.outputs[1].unit='EUR';mixed.chart.series=['enterprise','equity'];assert.throws(()=>validateAppSpec(mixed),/единицах/);
  const table=budgetApp();table.rows=Array.from({length:31},()=>table.rows[0]);assert.throws(()=>validateAppSpec(table));
  assert.equal(appNumber('0'),0);assert.equal(appNumber('1 234,5'),1234.5);assert.equal(appNumber('-0.01'),-.01);assert.equal(appNumber('1e-8'),1e-8);
  for(const invalid of ['', 'NaN', 'Infinity','0x10','1e999','1e-999'])assert.throws(()=>appNumber(invalid));
  assert.notEqual(formatAppNumber(1e-8),'0');assert.notEqual(formatAppNumber(1e-12),'0');
});
test('CSV preserves calculated numbers and neutralizes spreadsheet formula text',()=>{
  const state=initialAppState(budgetApp());state.rows[0].label='=HYPERLINK("https://evil.example")';
  const csv=appCSV(state);assert.ok(csv.startsWith('\uFEFF'));assert.match(csv,/'=HYPERLINK/);assert.match(csv,/-1000/);assert.match(csv,/Прибыль/);
});
test('explicit app requests route before finance but ordinary reports and deployment do not',()=>{
  for(const text of ['Создай калькулятор оценки стартапа','Сделай интерактивную таблицу расходов','Построй интерактивный график продаж','Хочу конфигуратор стоимости заказа'])assert.equal(wantsMiniApp(text),true,text);
  for(const text of ['Посчитай мои расходы за ноябрь','Построй график моих расходов','Создай репозиторий калькулятора на GitHub','Объясни что такое калькулятор'])assert.equal(wantsMiniApp(text),false,text);
  assert.equal(appClarification('Выручка 10 млн, мультипликатор 6'),true);assert.equal(appClarification('Напомни завтра в 10'),false);
});
test('app edits use current control values and avoid recording actual financial operations',async()=>{
  const previous=initialAppState(startupApp());previous.values.revenue=2000000;
  assert.equal(wantsMiniAppEdit('Измени выручку на 3000000',previous),true);
  assert.equal(wantsMiniAppEdit('Добавь график в этом калькуляторе',previous),true);
  assert.equal(wantsMiniAppEdit('Добавь доход на счёт 3000000',previous),false);
  const result=await prepareMiniApp({text:'Измени выручку на 3000000',previous,model:'velia-flash',signal:new AbortController().signal,request:async(_path,payload)=>{
    const context=JSON.parse(payload.messages[1].content).current_app;assert.equal(context.inputs[0].value,2000000);context.inputs[0].value=3000000;return stream(context);
  }});assert.equal(calculateApp(result.app).outputs.enterprise,15000000);assert.equal(previous.values.revenue,2000000);
});
test('the generic valuation example opens locally and custom apps use exactly the selected model once',async()=>{
  const signal=new AbortController().signal;
  const demo=await prepareMiniApp({text:'Создай калькулятор оценки стартапа, где я могу менять выручку и мультипликаторы',model:'velia-flash',signal,request:async()=>{throw Error('must not call');}});assert.equal(demo.app.spec.type,'calculator');
  for(const model of ['velia-flash','velia-quantum','velia-pro']){
    let calls=0;const result=await prepareMiniApp({text:'Создай калькулятор оценки стартапа: выручка 2000000 USD, множитель 3',model,signal,request:async(path,payload)=>{
      calls++;assert.equal(path,'apps/plan');assert.equal(payload.model,model);assert.equal(payload.web_search,false);const spec=startupApp();spec.inputs[0].value=2000000;spec.inputs[1].value=3;return stream(spec);
    }});assert.equal(calls,1);assert.equal(calculateApp(result.app).outputs.enterprise,6000000);
  }
});
test('invalid or interrupted definitions never become executable apps and a question can be clarified',async()=>{
  const signal=new AbortController().signal;
  for(const [value,finish] of [['<html>bad</html>','stop'],[{html:'<script>fetch()</script>'},'stop'],[startupApp(),'length']])await assert.rejects(prepareMiniApp({text:'Создай калькулятор доставки с 2 тарифами',model:'velia-flash',signal,request:async()=>stream(value,finish)}));
  const question=await prepareMiniApp({text:'Создай калькулятор доставки',model:'velia-pro',signal,request:async()=>stream({question:'Какие тарифы нужно учесть?'})});assert.equal(question.pending.request,'Создай калькулятор доставки');
  const result=await prepareMiniApp({text:'100 и 200 USD',pending:question.pending,model:'velia-pro',signal,request:async(_path,payload)=>{assert.match(payload.messages[1].content,/Какие тарифы/);return stream(startupApp());}});assert.ok(validateAppState(result.app));
  await assert.rejects(prepareMiniApp({text:'Создай калькулятор',model:'velia-pro',signal,guard:()=>{throw new DOMException('Account changed','AbortError');},request:async()=>stream(startupApp())}),{name:'AbortError'});
});
test('saved apps reopen with current values in another encrypted notebook instance',async()=>{
  let stored=null,revision=0;
  const request=async(_path,options={})=>{if(options.method==='PUT'){stored=options.data.record;revision++;return {revision};}return {account:'a'.repeat(24),owner:'7',encryption_key:'1'.repeat(64),record:stored,revision};};
  const first=new Notebook(request,webcrypto);await first.unlock('a'.repeat(24));const state=initialAppState(startupApp());state.values.multiple=8;
  const id=await first.save({kind:'miniapp',title:state.spec.title,content:appSummary(state),tags:[],miniApp:state});assert.doesNotMatch(JSON.stringify(stored),/Стоимость|revenue|multiple/);
  const second=new Notebook(request,webcrypto);await second.unlock('a'.repeat(24));const entry=(await second.load()).find(e=>e.id===id);assert.equal(calculateApp(entry.miniApp).outputs.enterprise,8000000);
  second.lock();await assert.rejects(second.load(),/Войди/);
});
