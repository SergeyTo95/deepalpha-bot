import {validateAppState,initialAppState,calculateApp,appNumber,appCSV,appFormula,formatAppNumber} from './mini-app-core.mjs';
const node=(tag,text,className)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;};
const button=(text,action)=>{const e=node('button',text);e.type='button';e.onclick=action;return e;};
const number=formatAppNumber;
function chartNode(chart,type,title){
  const svg=(tag,attrs={},text)=>{const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;return e;};
  const root=node('div',undefined,'mini-app-chart'),image=svg('svg',{viewBox:'0 0 640 280',role:'img','aria-label':title});image.append(svg('title',{},title));
  const values=chart.points.flatMap(p=>chart.series.map(s=>p.values[s.id])),low=Math.min(0,...values),high=Math.max(0,...values),span=high-low||1;
  const xs=chart.points.map(p=>p.x),numericX=type==='line'&&xs.every(Number.isFinite),xmin=Math.min(...xs),xmax=Math.max(...xs);
  const y=v=>220-(v-low)/span*195,x=i=>numericX?75+(xs[i]-xmin)/(xmax-xmin||1)*540:75+(i+.5)*540/chart.points.length,zero=y(0);
  for(let i=0;i<=4;i++){const value=low+span*i/4,py=y(value);image.append(svg('line',{x1:75,x2:615,y1:py,y2:py,class:'mini-app-grid'}),svg('text',{x:68,y:py+4,'text-anchor':'end',class:'mini-app-axis'},new Intl.NumberFormat('ru',{notation:Math.abs(value)>0&&Math.abs(value)<1e-4?'scientific':'compact',maximumFractionDigits:2}).format(value)));}
  chart.series.forEach((series,j)=>{
    if(type==='line')image.append(svg('polyline',{points:chart.points.map((p,i)=>x(i)+','+y(p.values[series.id])).join(' '),class:'mini-app-line mini-app-series-'+j}));
    chart.points.forEach((p,i)=>{
      const value=p.values[series.id],py=y(value),width=Math.min(44,540/chart.points.length/chart.series.length*.72);
      const shape=type==='line'?svg('circle',{cx:x(i),cy:py,r:4,class:'mini-app-series-'+j}):svg('rect',{x:x(i)+(j-(chart.series.length-1)/2)*width-width/2,y:Math.min(py,zero),width:Math.max(1,width-1),height:Math.max(1,Math.abs(zero-py)),class:'mini-app-series-'+j});
      shape.append(svg('title',{},p.label+' · '+series.label+': '+number(value)+(series.unit?' '+series.unit:'')));image.append(shape);
    });
  });
  chart.points.forEach((p,i)=>{if(chart.points.length<=10||i%Math.ceil(chart.points.length/10)===0)image.append(svg('text',{x:x(i),y:242,'text-anchor':'middle',class:'mini-app-axis'},p.label.length>12?p.label.slice(0,11)+'…':p.label));});
  if(chart.axisLabel)image.append(svg('text',{x:345,y:270,'text-anchor':'middle',class:'mini-app-axis'},chart.axisLabel));
  root.append(image);const legend=node('div',undefined,'mini-app-legend');chart.series.forEach((s,i)=>legend.append(node('span',s.label+(s.unit?' · '+s.unit:''),'mini-app-legend-'+i)));root.append(legend);return root;
}
export function miniAppNode(value,{signal,guard=()=>{},onChange=()=>{},onSave=async()=>{},toast=()=>{},readOnly=false}={}){
  try{validateAppState(value);}catch{return node('p','Не удалось открыть сохранённое приложение. Создай его заново.','feature-notice');}
  let state=structuredClone(value),saving=false,invalid=new Set();
  const check=()=>{signal?.throwIfAborted();guard();};
  const root=node('section',undefined,'mini-app');root.setAttribute('aria-label','Интерактивное приложение: '+state.spec.title);
  root.append(node('small','ИНТЕРАКТИВНОЕ ПРИЛОЖЕНИЕ','feature-eyebrow'),node('h3',state.spec.title));if(state.spec.description)root.append(node('p',state.spec.description,'mini-app-description'));
  const fieldset=node('fieldset');fieldset.disabled=readOnly;fieldset.append(node('legend','Исходные параметры'));
  const fields=node('div',undefined,'mini-app-fields'),tableArea=node('div',undefined,'mini-app-table-wrap'),outputArea=node('div',undefined,'mini-app-outputs'),chartArea=node('div'),error=node('p',undefined,'feature-notice');error.setAttribute('role','status');error.setAttribute('aria-live','polite');
  const totals=new Map(),computed=new Map();
  let save,download,addRow,graphType;
  function fail(message){error.textContent=message;outputArea.replaceChildren();chartArea.replaceChildren();for(const cell of [...computed.values(),...totals.values()])cell.textContent='—';if(save)save.disabled=true;if(download)download.disabled=true;if(addRow)addRow.disabled=true;}
  function update(){
    try{
      if(invalid.size){fail('Исправь отмеченные поля, чтобы пересчитать результат.');return;}
      const result=calculateApp(state);error.textContent='';outputArea.replaceChildren();
      for(const output of state.spec.outputs||[]){const card=node('div',undefined,'mini-app-output');card.append(node('span',output.label),node('strong',number(result.outputs[output.id])+(output.unit?' '+output.unit:'')));outputArea.append(card);}
      result.rows.forEach((row,i)=>{for(const c of state.spec.columns)if(c.expr!==undefined)computed.get(i+':'+c.id).textContent=number(row.cells[c.id]);});
      for(const [id,cell] of totals)cell.textContent=number(result.totals[id]);
      chartArea.replaceChildren();
      if(result.chart){
        chartArea.append(chartNode(result.chart,state.chartType,state.spec.title));
        if(state.spec.type==='calculator'&&state.spec.chart.scenario){const table=node('table');const head=node('tr');head.append(node('th',result.chart.axisLabel),...result.chart.series.map(s=>node('th',s.label+(s.unit?' · '+s.unit:''))));table.append(head);for(const point of result.chart.points){const row=node('tr');row.append(node('th',point.label),...result.chart.series.map(s=>node('td',number(point.values[s.id]))));table.append(row);}const wrap=node('div',undefined,'mini-app-table-wrap');wrap.append(table);chartArea.append(wrap);}
      }
      if(save)save.disabled=readOnly||saving;if(download)download.disabled=readOnly;if(addRow)addRow.disabled=readOnly||state.rows.length>=30;
    }catch(e){fail(e.message);}
  }
  function commit(next){check();validateAppState(next);state=next;onChange(structuredClone(next));update();}
  function numericInput(value,path,apply){
    const input=node('input');input.type='text';input.inputMode='decimal';input.value=String(value);input.maxLength=40;
    input.oninput=()=>{
      try{check();const next=structuredClone(state);apply(next,appNumber(input.value));validateAppState(next);invalid.delete(path);input.removeAttribute('aria-invalid');commit(next);}
      catch(e){if(e.name==='AbortError')return;invalid.add(path);input.setAttribute('aria-invalid','true');fail(e.message);}
    };return input;
  }
  for(const definition of state.spec.inputs){
    const label=node('label',definition.label+(definition.unit?' · '+definition.unit:''));let input;
    if(definition.choices){input=node('select');for(const choice of definition.choices){const option=node('option',choice.label);option.value=String(choice.value);input.append(option);}input.value=String(state.values[definition.id]);input.onchange=()=>{try{check();commit({...state,values:{...state.values,[definition.id]:appNumber(input.value)}});}catch(e){if(e.name!=='AbortError')fail(e.message);}};}
    else input=numericInput(state.values[definition.id],'input:'+definition.id,(next,v)=>{next.values[definition.id]=v;});
    input.setAttribute('aria-label',definition.label);label.append(input,node('small','От '+number(definition.min)+' до '+number(definition.max)));fields.append(label);
  }
  function drawTable(){
    if(!state.rows)return;
    computed.clear();totals.clear();tableArea.replaceChildren();
    const table=node('table'),head=node('tr');head.append(node('th','Название'),...state.spec.columns.map(c=>node('th',c.label+(c.unit?' · '+c.unit:''))),node('th',''));table.append(head);
    state.rows.forEach((row,i)=>{
      const tr=node('tr'),labelCell=node('td'),label=node('input');label.type='text';label.value=row.label;label.maxLength=100;label.setAttribute('aria-label','Название строки '+(i+1));
      label.oninput=()=>{try{check();const next=structuredClone(state);next.rows[i].label=label.value;validateAppState(next);invalid.delete('label:'+i);label.removeAttribute('aria-invalid');commit(next);}catch(e){if(e.name!=='AbortError'){invalid.add('label:'+i);label.setAttribute('aria-invalid','true');fail(e.message);}}};labelCell.append(label);tr.append(labelCell);
      for(const column of state.spec.columns){const td=node('td');if(column.expr===undefined){const input=numericInput(row.cells[column.id],'cell:'+i+':'+column.id,(next,v)=>{next.rows[i].cells[column.id]=v;});input.setAttribute('aria-label',row.label+' · '+column.label);td.append(input);}else computed.set(i+':'+column.id,td);tr.append(td);}
      const remove=node('td');const b=button('×',()=>{try{check();if(invalid.size)throw Error('Сначала исправь отмеченные поля.');const next=structuredClone(state);next.rows.splice(i,1);validateAppState(next);state=next;drawTable();commit(next);}catch(e){if(e.name!=='AbortError')toast(e.message);}});b.setAttribute('aria-label','Удалить строку '+(i+1));b.disabled=state.rows.length===1;remove.append(b);tr.append(remove);table.append(tr);
    });
    if(state.spec.columns.some(c=>c.total)){const row=node('tr');row.append(node('th','Итого'));for(const c of state.spec.columns){const td=node('td');if(c.total)totals.set(c.id,td);row.append(td);}row.append(node('td'));table.append(row);}tableArea.append(table);
  }
  drawTable();fieldset.append(fields,tableArea);
  if(state.rows){addRow=button('Добавить строку',()=>{try{check();const next=structuredClone(state);next.rows.push({label:'Строка '+(next.rows.length+1),cells:Object.fromEntries(state.spec.columns.filter(c=>c.expr===undefined).map(c=>[c.id,0]))});validateAppState(next);state=next;drawTable();commit(next);}catch(e){if(e.name!=='AbortError')toast(e.message);}});fieldset.append(addRow);}
  if(state.spec.chart){const label=node('label','Вид графика');graphType=node('select');for(const [id,text] of [['bar','Столбцы'],['line','Линии']]){if(id==='line'&&state.spec.type==='calculator'&&!state.spec.chart.scenario)continue;const option=node('option',text);option.value=id;graphType.append(option);}graphType.value=state.chartType;graphType.onchange=()=>{try{commit({...state,chartType:graphType.value});}catch(e){if(e.name!=='AbortError')toast(e.message);}};label.append(graphType);fieldset.append(label);}
  const actions=node('div',undefined,'feature-actions');
  save=button('Сохранить в заметки',async()=>{if(save.disabled)return;saving=true;save.disabled=true;try{check();await onSave(structuredClone(state));check();toast('Приложение сохранено в заметках.');}catch(e){if(e.name!=='AbortError')toast(e.message);}finally{saving=false;if(!signal?.aborted)update();}});
  download=button('Скачать CSV',()=>{try{check();const url=URL.createObjectURL(new Blob([appCSV(state)],{type:'text/csv;charset=utf-8'})),a=node('a');a.href=url;a.download='VELIA-calculation.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){if(e.name!=='AbortError')toast(e.message);}});
  const reset=button('Сбросить параметры',()=>{try{check();const next=initialAppState(state.spec);invalid.clear();commit(next);root.replaceWith(miniAppNode(next,{signal,guard,onChange,onSave,toast,readOnly}));}catch(e){if(e.name!=='AbortError')toast(e.message);}});reset.disabled=readOnly;
  actions.append(save,download,reset);root.append(fieldset,error,outputArea,chartArea);
  const assumptions=node('details'),summary=node('summary','Формулы и допущения');assumptions.append(summary);
  const formulaFields=[...state.spec.inputs,...(state.spec.outputs||[]),...(state.spec.columns||[])];
  for(const output of formulaFields.filter(o=>o.expr!==undefined))assumptions.append(node('p',output.label+' = '+appFormula(output.expr,formulaFields),'mini-app-formula'));
  for(const line of state.spec.assumptions)assumptions.append(node('p',line));root.append(assumptions,actions,node('small','Изменения пересчитываются на этом устройстве. Сохранённое приложение доступно через заметки.','notebook-hint'));
  update();return root;
}
