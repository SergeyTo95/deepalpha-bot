import {apiError} from "./core.mjs";
// Server owns execution. This module only submits, polls and renders task state.
export function setupBackgroundTasks({request, profile, busy, currentId, open, refresh, speak, toast, chatWithFlash}) {
  const panel=document.getElementById('background-tasks'), option=document.getElementById('background-option'), toggle=document.getElementById('background-enabled');
  let jobs=[], owner=null, timer=null, loading=false, generation=0;
  const notified=new Set(), restored=new Set(), preferences=new Map();
  let selectedModel=null;
  toggle.onchange=()=>{if(selectedModel)preferences.set(selectedModel,toggle.checked);};
  const active=j=>['queued','running'].includes(j.status);
  const labels={queued:'В очереди',running:'Готовлю результат',completed:'Готово',error:'Задача не выполнена',cancelled:'Отменена'};
  function reset(){generation++;owner=null;jobs=[];notified.clear();restored.clear();preferences.clear();selectedModel=null;clearTimeout(timer);timer=null;loading=false;panel.replaceChildren();panel.hidden=true;option.hidden=true;toggle.checked=false;}
  function modelChanged(model){
    selectedModel=model;option.hidden=!profile()?.background_tasks||!['velia-quantum','velia-pro'].includes(model);
    if(!option.hidden)toggle.checked=preferences.get(model)??true;
  }
  function wants(model){return !option.hidden&&toggle.checked&&['velia-quantum','velia-pro'].includes(model);}
  function render(){
    if(panel.contains(document.activeElement)&&document.activeElement.tagName==='TEXTAREA')return;
    panel.replaceChildren();panel.hidden=!jobs.length;
    for(const job of jobs.filter(j=>active(j)||j.conversation_id===currentId()||Date.now()/1000-Number(j.updated_at||j.created_at||0)<86400).slice(0,4)){
      const card=document.createElement('article');card.className='background-task';
      const heading=document.createElement('strong');heading.textContent=`${job.model==='pro'?'PRO':'Quantum'} · ${labels[job.status]||'Проверяю состояние'}`;
      const title=document.createElement('p');title.textContent=job.title;
      card.append(heading,title);
      if(job.notes?.length){const note=document.createElement('small');note.textContent='Уточнение: '+job.notes.at(-1);card.append(note);}
      const actions=document.createElement('div');actions.className='background-actions';
      const button=(text,fn)=>{const b=document.createElement('button');b.type='button';b.textContent=text;b.onclick=fn;actions.append(b);return b;};
      button(job.status==='completed'?'Открыть результат':'Открыть диалог',()=>open(job.conversation_id));
      if(active(job)){
        const hint=document.createElement('p');hint.className='background-hint';
        hint.textContent=`Пока ждёшь результат ${job.model==='pro'?'PRO':'Quantum'}, можно пообщаться с Flash. Задача продолжится в фоне, а ответ появится в исходном диалоге.`;
        card.append(hint);
        const chat=button('Поговорить с Flash',()=>{if(!busy())chatWithFlash();});
        chat.disabled=busy();
        button('Отменить',async()=>{try{await request(`background-tasks/${job.id}/cancel`,{});await poll();}catch(e){toast(e.message);}});
        button('Дополнить',()=>{
          if(card.querySelector('textarea'))return;
          const input=document.createElement('textarea');input.maxLength=2000;input.rows=2;input.placeholder='Что учесть в задаче?';input.setAttribute('aria-label','Уточнение задачи');
          const submit=document.createElement('button');submit.type='button';submit.textContent='Передать уточнение';let key=crypto.randomUUID(),last='';
          submit.onclick=async()=>{const note=input.value.trim();if(!note)return;if(note!==last){key=crypto.randomUUID();last=note;}submit.disabled=true;try{await request(`background-tasks/${job.id}/revise`,{note,idempotency_key:key});input.blur();await poll();}catch(e){toast(e.message);submit.disabled=false;}};
          card.append(input,submit);input.focus();
        });
      }else if(job.status==='completed')button('Озвучить результат',async()=>{try{const data=await request(`background-tasks/${job.id}`);speak(data.job.result);}catch(e){toast(e.message);}});
      else if(job.error){const error=document.createElement('small');error.textContent=apiError(job.error);card.append(error);}
      card.append(actions);panel.append(card);
    }
    panel.hidden=!panel.childElementCount;
  }
  async function poll(){
    clearTimeout(timer);timer=null;
    const account=profile()?.account;
    if(!account||!profile()?.background_tasks){reset();return;}
    if(loading)return;
    if(owner!==account){reset();owner=account;}
    const token=generation;loading=true;
    try{
      const data=await request('background-tasks');
      if(generation!==token||profile()?.account!==account)return;
      jobs=data.jobs||[];render();
      for(const job of jobs){
        if(job.status==='error'&&!notified.has(job.id)){notified.add(job.id);toast(`${job.model==='pro'?'PRO':'Quantum'}: ${apiError(job.error)}`);}
        if(job.status==='completed'){
          if(!notified.has(job.id)){notified.add(job.id);toast(`${job.model==='pro'?'PRO':'Quantum'}: результат готов.`);}
          if(!restored.has(job.id)&&job.conversation_id===currentId()&&!busy()){
            await refresh(job.conversation_id);
            if(generation!==token||profile()?.account!==account)return;
            restored.add(job.id);render();
          }
        }
      }
    }catch{/* A failed poll does not change a durable task into an error. */}
    finally{
      if(token===generation){loading=false;timer=setTimeout(poll,jobs.some(active)||jobs.some(j=>j.status==='completed'&&j.conversation_id===currentId()&&!restored.has(j.id))?3000:20000);}
    }
  }
  async function submit(cid,payload){
    const account=profile()?.account;
    const data=await request(`conversations/${cid}/tasks`,payload);
    if(!account||profile()?.account!==account)throw Error('Сессия изменилась.');
    owner=account;jobs=[data.job,...jobs.filter(j=>j.id!==data.job.id)];render();
    clearTimeout(timer);timer=setTimeout(poll,1000);
    return data.job;
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
  return {poll,reset,modelChanged,wants,submit,render};
}

export function taskAcknowledgement(locale){
  const language=String(locale||'').toLowerCase().split(/[-_]/)[0];
  return ({ru:'Поняла, готовлю результат. Пока можем пообщаться в режиме Flash.',en:'I’m preparing the result. We can keep talking in Flash mode.',tr:'Sonucu hazırlıyorum. Bu sırada Flash modunda konuşabiliriz.',es:'Estoy preparando el resultado. Mientras tanto, podemos hablar en modo Flash.',fr:'Je prépare le résultat. Nous pouvons discuter en mode Flash.',de:'Ich bereite das Ergebnis vor. Wir können inzwischen im Flash-Modus weiterreden.',pt:'Estou preparando o resultado. Podemos conversar no modo Flash.',it:'Sto preparando il risultato. Intanto possiamo parlare in modalità Flash.',zh:'我正在准备结果。我们可以在 Flash 模式下继续聊天。',ja:'結果を準備しています。その間は Flash モードで会話を続けられます。',ko:'결과를 준비하고 있어요. 그동안 Flash 모드로 대화할 수 있어요.',ar:'أجهز النتيجة. يمكننا مواصلة الحديث في وضع Flash.',uk:'Готую результат. Тим часом можемо поспілкуватися в режимі Flash.'})[language]||'';
}
