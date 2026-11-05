// Explicit requests only: ordinary discussion of schedules never creates one.
export function parseAutoloop(text){
  const match=String(text||'').match(/^\s*(?:повторяй\s+каждые|repeat\s+every)\s+(\d+)\s*(минут(?:у|ы)?|час(?:а|ов)?|minutes?|hours?)\s*:\s*([\s\S]+)$/i);
  if(!match)return null;
  const interval=Number(match[1])*(/^(час|hour)/i.test(match[2])?60:1);
  if(interval<15||interval>10080||match[3].trim().length>4000)throw Error('Укажи интервал от 15 минут до 7 дней и поручение до 4000 символов.');
  const instruction=match[3].trim();
  const coding=/(?:исправь|почини|доработай).*(?:баг|ошибк|код|репозитор)|\bfix\b.*\b(?:bug|code|repository|repo)\b/i.test(instruction);
  return {instruction,interval_minutes:interval,event_types:['manual'],max_runs_per_day:1,enabled:true,...(coding?{kind:'coding'}:{})};
}

export function setupAutoloop({request,profile,currentId,open,toast}){
  const panel=document.getElementById('autoloop-rules');
  let rules=[],owner=null,timer=null,generation=0;
  function reset(){generation++;owner=null;rules=[];clearTimeout(timer);timer=null;panel.replaceChildren();panel.hidden=true;}
  function render(){
    panel.replaceChildren();
    if(owner!==profile()?.account){panel.hidden=true;return;}
    for(const rule of rules.filter(r=>r.conversation_id===currentId())){
      const card=document.createElement('article');card.className='background-task';
      const heading=document.createElement('strong');heading.textContent='Фоновое поручение · '+(rule.enabled?'включено':'на паузе');
      const text=document.createElement('p');text.textContent=rule.instruction;
      const cadence=document.createElement('small');cadence.textContent=(rule.interval_minutes?`Интервал: ${rule.interval_minutes} мин. · `:'По событиям · ')+`До ${rule.max_runs_per_day} запусков в сутки`;
      const actions=document.createElement('div');actions.className='background-actions';
      const button=(label,fn)=>{const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){toast(e.message);}finally{b.disabled=false;}};actions.append(b);};
      button(rule.enabled?'Остановить повторы':'Возобновить повторы',async()=>{const account=profile()?.account;await request(`autoloop/rules/${rule.id}/${rule.enabled?'disable':'enable'}`,{});if(profile()?.account===account)await poll();});
      if(rule.enabled)button('Запустить сейчас',async()=>{await request(`autoloop/rules/${rule.id}/events`,{id:crypto.randomUUID(),type:'manual',subject:'manual',detail:''});toast('Событие принято. Задача появится в очереди с учётом дневного лимита.');});
      card.append(heading,text,cadence,actions);panel.append(card);
    }
    panel.hidden=!panel.childElementCount;
  }
  async function poll(){
    clearTimeout(timer);timer=null;
    const account=profile()?.account;
    if(!account||!profile()?.background_tasks){reset();return;}
    if(owner!==account){reset();owner=account;}
    const token=generation;
    try{const data=await request('autoloop/rules');if(generation!==token||profile()?.account!==account)return;rules=data.rules||[];render();}
    catch{/* Existing background tasks still work against an older backend. */}
    finally{if(generation===token)timer=setTimeout(poll,20000);}
  }
  async function create(cid,model,spec,key,webSearch){
    const account=profile()?.account;
    const data=await request('autoloop/rules',{...spec,conversation_id:cid,model,idempotency_key:key,web_search:!!webSearch});
    if(profile()?.account!==account)throw Error('Сессия изменилась.');
    await poll();return data.rule;
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
  return {create,poll,reset,render};
}
