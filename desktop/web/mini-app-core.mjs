/** Declarative numeric apps. Expressions never execute JavaScript or access globals. */
const MAX=1e15;
const record=v=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.getPrototypeOf(v)===Object.prototype;
const keys=(v,allowed)=>record(v)&&Object.keys(v).every(k=>allowed.includes(k));
const text=(v,max)=>typeof v==='string'&&v.trim().length>0&&v.length<=max;
const numeric=v=>typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=MAX;
const identifier=v=>typeof v==='string'&&/^[a-z][a-z0-9_]{0,31}$/u.test(v)&&!['constructor','prototype','__proto__'].includes(v);
const need=(ok,message='Не удалось проверить интерактивное приложение.')=>{if(!ok)throw Error(message);};
const OPERATIONS={add:[2,12],sub:[2,2],mul:[2,12],div:[2,2],pow:[2,2],min:[2,12],max:[2,12],abs:[1,1],round:[1,1],if:[3,3],gt:[2,2],gte:[2,2],lt:[2,2],lte:[2,2],eq:[2,2]};
function validateExpression(expr,known,depth=0,budget={nodes:0}){
  need(depth<=8&&++budget.nodes<=80,'Формула слишком сложная.');
  if(numeric(expr))return;
  if(typeof expr==='string'){need(identifier(expr)&&known.has(expr),'В формуле есть неизвестное поле или циклическая ссылка.');return;}
  need(Array.isArray(expr)&&typeof expr[0]==='string'&&Object.hasOwn(OPERATIONS,expr[0]));
  const [min,max]=OPERATIONS[expr[0]];need(expr.length-1>=min&&expr.length-1<=max);
  for(const arg of expr.slice(1))validateExpression(arg,known,depth+1,budget);
}
function field(value,known,allowed){
  need(keys(value,allowed)&&identifier(value.id)&&!known.has(value.id)&&text(value.label,100));
  need(value.unit===undefined||typeof value.unit==='string'&&value.unit.length<=16);
}
function rowsValid(rows,spec){
  need(Array.isArray(rows)&&rows.length>=1&&rows.length<=30,'В таблице должно быть от 1 до 30 строк.');
  const columns=spec.columns.filter(c=>c.expr===undefined);
  for(const row of rows)need(keys(row,['label','cells'])&&text(row.label,100)&&record(row.cells)&&Object.keys(row.cells).length===columns.length&&columns.every(c=>Object.hasOwn(row.cells,c.id)&&numeric(row.cells[c.id])),'Проверь названия строк и числовые ячейки.');
}
export function validateAppSpec(spec){
  need(keys(spec,['version','type','title','description','inputs','outputs','columns','rows','chart','assumptions'])&&spec.version===1&&['calculator','table','chart'].includes(spec.type)&&text(spec.title,160));
  need(JSON.stringify(spec).length<=12000,'Приложение слишком большое.');
  need(spec.description===undefined||typeof spec.description==='string'&&spec.description.length<=400);
  need(Array.isArray(spec.inputs)&&spec.inputs.length<=12);
  need(Array.isArray(spec.assumptions)&&spec.assumptions.length<=6&&spec.assumptions.every(a=>text(a,240)));
  const known=new Set();
  for(const input of spec.inputs){
    field(input,known,['id','label','value','min','max','unit','choices']);
    need(numeric(input.value)&&numeric(input.min)&&numeric(input.max)&&input.min<input.max&&input.value>=input.min&&input.value<=input.max,'Проверь границы исходных параметров.');
    if(input.choices!==undefined)need(Array.isArray(input.choices)&&input.choices.length>=2&&input.choices.length<=8&&new Set(input.choices.map(c=>c?.value)).size===input.choices.length&&input.choices.every(c=>keys(c,['label','value'])&&text(c.label,80)&&numeric(c.value)&&c.value>=input.min&&c.value<=input.max)&&input.choices.some(c=>c.value===input.value));
    known.add(input.id);
  }
  let series;
  if(spec.type==='calculator'){
    need(spec.inputs.length>0&&spec.columns===undefined&&spec.rows===undefined&&Array.isArray(spec.outputs)&&spec.outputs.length>=1&&spec.outputs.length<=8);
    for(const output of spec.outputs){field(output,known,['id','label','expr','unit']);validateExpression(output.expr,known);known.add(output.id);}
    series=spec.outputs;
  }else{
    need(spec.outputs===undefined&&Array.isArray(spec.columns)&&spec.columns.length>=1&&spec.columns.length<=8);
    for(const column of spec.columns){field(column,known,['id','label','expr','unit','total']);need(column.total===undefined||typeof column.total==='boolean');if(column.expr!==undefined)validateExpression(column.expr,known);known.add(column.id);}
    need(spec.columns.some(c=>c.expr===undefined),'Добавь хотя бы один изменяемый столбец.');
    rowsValid(spec.rows,spec);series=spec.columns;
  }
  if(spec.chart!==undefined){
    const chart=spec.chart;need(keys(chart,['type','series','scenario'])&&['bar','line'].includes(chart.type)&&Array.isArray(chart.series)&&chart.series.length>=1&&chart.series.length<=3&&new Set(chart.series).size===chart.series.length&&chart.series.every(id=>series.some(s=>s.id===id)));
    need(new Set(chart.series.map(id=>series.find(s=>s.id===id).unit||'')).size===1,'Для общего графика выбери показатели в одинаковых единицах.');
    if(chart.scenario!==undefined){const scenario=chart.scenario,input=spec.inputs.find(i=>i.id===scenario?.input);need(spec.type==='calculator'&&keys(scenario,['input','values'])&&input&&Array.isArray(scenario.values)&&scenario.values.length>=2&&scenario.values.length<=9&&new Set(scenario.values).size===scenario.values.length&&scenario.values.every(v=>numeric(v)&&v>=input.min&&v<=input.max&&(!input.choices||input.choices.some(c=>c.value===v))));}
    need(spec.type!=='calculator'||chart.scenario!==undefined||chart.type==='bar','Для линейного графика калькулятора задай сценарии.');
  }
  need(spec.type!=='chart'||spec.chart!==undefined,'Добавь параметры графика.');
  return spec;
}
export function initialAppState(spec){
  validateAppSpec(spec);return {version:1,spec:structuredClone(spec),values:Object.fromEntries(spec.inputs.map(i=>[i.id,i.value])),...(spec.rows?{rows:structuredClone(spec.rows)}:{}),chartType:spec.chart?.type||'bar'};
}
export function validateAppState(state){
  need(keys(state,['version','spec','values','rows','chartType'])&&state.version===1);
  const spec=validateAppSpec(state.spec);need(record(state.values)&&Object.keys(state.values).length===spec.inputs.length);
  for(const input of spec.inputs){const value=state.values[input.id];need(numeric(value)&&value>=input.min&&value<=input.max&&(!input.choices||input.choices.some(c=>c.value===value)),'Проверь значение «'+input.label+'»: от '+input.min+' до '+input.max+'.');}
  if(spec.rows)rowsValid(state.rows,spec);else need(state.rows===undefined);
  need(['bar','line'].includes(state.chartType)&&!(spec.type==='calculator'&&spec.chart&&!spec.chart.scenario&&state.chartType==='line'));
  need(JSON.stringify(state).length<=20000,'Приложение слишком большое.');return state;
}
function expression(expr,values){
  if(typeof expr==='number')return expr;
  if(typeof expr==='string')return values.get(expr);
  const [op,...args]=expr;
  if(op==='if')return expression(args[0],values)!==0?expression(args[1],values):expression(args[2],values);
  const a=args.map(v=>expression(v,values));let result;
  switch(op){
    case 'add':result=a.reduce((s,v)=>s+v,0);break;case 'sub':result=a[0]-a[1];break;
    case 'mul':result=a.reduce((s,v)=>s*v,1);break;
    case 'div':need(a[1]!==0,'Деление на ноль: измени исходные параметры.');result=a[0]/a[1];break;
    case 'pow':need(Math.abs(a[1])<=12,'Показатель степени должен быть от −12 до 12.');result=a[0]**a[1];break;
    case 'min':result=Math.min(...a);break;case 'max':result=Math.max(...a);break;case 'abs':result=Math.abs(a[0]);break;case 'round':result=Math.round(a[0]);break;
    case 'gt':result=+(a[0]>a[1]);break;case 'gte':result=+(a[0]>=a[1]);break;case 'lt':result=+(a[0]<a[1]);break;case 'lte':result=+(a[0]<=a[1]);break;case 'eq':result=+(a[0]===a[1]);break;
  }
  need(numeric(result),'Результат выходит за пределы расчёта. Уменьши исходные значения.');return result;
}
function outputs(spec,values){const context=new Map(Object.entries(values)),out={};for(const output of spec.outputs){out[output.id]=expression(output.expr,context);context.set(output.id,out[output.id]);}return out;}
export function calculateApp(state){
  validateAppState(state);const {spec,values}=state;
  if(spec.type==='calculator'){
    const result=outputs(spec,values);let chart=null;
    if(spec.chart){
      const {series,scenario}=spec.chart;
      chart={series:series.map(id=>spec.outputs.find(o=>o.id===id)),points:scenario?scenario.values.slice().sort((a,b)=>a-b).map(v=>({label:String(v),x:v,values:outputs(spec,{...values,[scenario.input]:v})})):series.map(id=>({label:spec.outputs.find(o=>o.id===id).label,values:{value:result[id]}}))};
      if(!scenario)chart.series=[{id:'value',label:'Результат',unit:spec.outputs.find(o=>o.id===series[0]).unit||''}];
      chart.axisLabel=scenario?spec.inputs.find(i=>i.id===scenario.input).label:'';
    }
    return {outputs:result,chart,rows:[],totals:{}};
  }
  const rows=state.rows.map(row=>{
    const context=new Map(Object.entries(values)),cells={};
    for(const column of spec.columns){const value=column.expr===undefined?row.cells[column.id]:expression(column.expr,context);cells[column.id]=value;context.set(column.id,value);}
    return {label:row.label,cells};
  });
  const totals={};for(const c of spec.columns.filter(c=>c.total)){totals[c.id]=rows.reduce((sum,row)=>sum+row.cells[c.id],0);need(numeric(totals[c.id]),'Сумма столбца слишком велика.');}
  return {outputs:{},rows,totals,chart:spec.chart?{series:spec.chart.series.map(id=>spec.columns.find(c=>c.id===id)),points:rows.map(row=>({label:row.label,values:row.cells})),axisLabel:''}:null};
}
export function appNumber(value){
  const source=String(value).trim().replace(/\s/gu,'').replace(',','.');
  need(/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d{1,3})?$/iu.test(source),'Введи число.');const result=Number(source);need(numeric(result),'Число слишком велико.');need(result!==0||Number(source.split(/e/iu)[0])===0,'Число слишком мало для расчёта.');return result;
}
export function appSummary(state){
  const {spec}=validateAppState(state),result=calculateApp(state),number=formatAppNumber;
  const lines=[spec.title,...(spec.description?[spec.description]:[]),...spec.inputs.map(i=>i.label+': '+number(state.values[i.id])+(i.unit?' '+i.unit:''))];
  if(spec.outputs)for(const o of spec.outputs)lines.push(o.label+': '+number(result.outputs[o.id])+(o.unit?' '+o.unit:''));
  else{lines.push(['Строка',...spec.columns.map(c=>c.label+(c.unit?' ('+c.unit+')':''))].join(' | '));for(const row of result.rows)lines.push([row.label,...spec.columns.map(c=>number(row.cells[c.id]))].join(' | '));}
  if(spec.assumptions.length)lines.push('Допущения:',...spec.assumptions);return lines.join('\n');
}
export function appCSV(state){
  const result=calculateApp(state),{spec}=state;
  const safeText=s=>{const text=String(s).replace(/[\u0000-\u001F\u007F]/gu,' ');return /^\s*[=+\-@]/u.test(text)?"'"+text:text;};
  const quote=v=>'"'+String(v).replace(/"/gu,'""')+'"';
  const rows=spec.outputs?[['Параметр','Значение','Единица'],...spec.inputs.map(i=>[safeText(i.label),state.values[i.id],safeText(i.unit||'')]),...spec.outputs.map(o=>[safeText(o.label),result.outputs[o.id],safeText(o.unit||'')])]:[['Строка',...spec.columns.map(c=>safeText(c.label+(c.unit?' ('+c.unit+')':'')))],...result.rows.map(r=>[safeText(r.label),...spec.columns.map(c=>r.cells[c.id])])];
  return '\uFEFF'+rows.map(row=>row.map(quote).join(';')).join('\r\n');
}
export function formatAppNumber(value){
  return new Intl.NumberFormat('ru',Math.abs(value)>0&&Math.abs(value)<1e-8?{notation:'scientific',maximumFractionDigits:6}:{maximumFractionDigits:8}).format(Object.is(value,-0)?0:value);
}
export function appFormula(expr,fields){
  if(typeof expr==='number')return String(expr);
  if(typeof expr==='string')return fields.find(f=>f.id===expr)?.label||expr;
  const [op,...args]=expr,labels=args.map(a=>appFormula(a,fields)),symbols={add:'+',sub:'−',mul:'×',div:'÷',pow:'^',gt:'>',gte:'≥',lt:'<',lte:'≤',eq:'='};
  if(Object.hasOwn(symbols,op))return '('+labels.join(' '+symbols[op]+' ')+')';
  if(op==='if')return 'если '+labels[0]+', то '+labels[1]+', иначе '+labels[2];
  return ({min:'Минимум',max:'Максимум',round:'Округление',abs:'Модуль'}[op]||op)+'('+labels.join('; ')+')';
}
