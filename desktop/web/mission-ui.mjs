import {MISSION_LABELS,missionError} from './missions.mjs';
import {financeReport} from './finance.mjs';
const node=(tag,text,className)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;};
const button=(text,action)=>{const e=node('button',text);e.type='button';e.onclick=action;return e;};
const active=m=>['running','pausing','cancelling'].includes(m.status);
function resultText(step){
  const data=step.result?.data;if(!data)return '';
  if(step.result.kind==='draft')return data.text;
  if(step.tool==='finance.report'){try{return financeReport(data);}catch{return 'Финансовые данные получены.';}}
  if(step.tool==='notebook.search')return data.records.map(r=>r.title+'\n'+r.text).join('\n\n')||'Подходящих записей нет.';
  if(step.tool==='crm.search')return data.contacts.map(c=>[c.name,c.company,c.email,c.phone,...c.next_actions.map(a=>a.title)].filter(Boolean).join(' · ')).join('\n')||'Контакты не найдены.';
  if(step.tool==='calendar.list')return data.events.map(e=>e.title+' · '+(e.start?.dateTime||e.start?.date||'')).join('\n')||'Событий за этот период нет.';
  if(step.tool==='calendar.create')return 'Событие создано: '+data.event.title+'. Приглашения не отправлялись.';
  if(step.tool==='gmail.draft')return 'Черновик создан в Gmail. Письмо не отправлено.';
  if(step.tool==='notebook.save')return 'Сохранено в заметки: '+data.title;
  if(step.tool==='review')return 'Результат согласован пользователем.';
  return '';
}
export function mountMissionCard(parent,{client,id,signal,toast=()=>{}}){
  const root=node('article',undefined,'mission-card feature-card');parent.append(root);let signature='',working=false;
  const unwatch=client.watch(id);
  async function run(m,name,b){
    if(working||signal.aborted)return;working=true;b.disabled=true;
    try{await client.action(m,name);}catch(e){if(!signal.aborted){toast(e.message);await client.refresh(id).catch(()=>{});}}
    finally{working=false;if(!signal.aborted){signature='';draw();}}
  }
  function draw(){
    if(signal.aborted)return;
    const m=client.get(id);if(!m){root.replaceChildren(node('p',client.loaded?.()&&!client.list().some(m=>m.id===id)?'Миссия удалена или недоступна.':'Загружаю состояние миссии…'));return;}
    const next=m.revision+':'+(client.status?.()||'');if(next===signature)return;signature=next;
    root.replaceChildren(node('small','VELYON MISSION','feature-eyebrow'),node('h3',m.title),node('p',MISSION_LABELS[m.status]||'Проверяю состояние','mission-state'));
    const done=m.steps.filter(s=>s.status==='completed').length;root.append(node('p',done+' из '+m.steps.length+' шагов · '+({'velia-flash':'Flash','velia-quantum':'Quantum','velia-pro':'PRO'}[m.model]||'VELIA'),'notebook-hint'));
    if(client.status?.())root.append(node('p','Не удалось обновить состояние. Показан последний полученный результат.','feature-notice'));
    const steps=node('ol',undefined,'mission-steps');
    for(const step of m.steps){
      const row=node('li');const label=step.status==='completed'?(step.result?.kind==='draft'?'Черновик готов':'Готово'):({pending:'Ожидает',running:'Выполняется',awaiting_approval:'Нужно подтверждение',failed:'Не выполнен',uncertain:'Нужно проверить результат'})[step.status]||step.status;
      row.append(node('strong',step.title),node('small',label));
      if(step.error)row.append(node('p',missionError(step.error),'feature-notice'));
      if(step.result){
        if(step.result.kind==='file'){
          const a=node('a','Скачать '+step.result.data.format.toUpperCase()+' · '+Math.ceil(step.result.data.bytes/1024)+' КБ');a.href='/web-api/v1/missions/'+encodeURIComponent(id)+'/files/'+encodeURIComponent(step.id);a.className='mission-file';row.append(a);
        }else{
          const details=node('details'),summary=node('summary',step.result.kind==='draft'?'Проверить текст':'Результат шага');details.append(summary);
          if(step.tool==='web.search')for(const item of step.result.data.results||[]){const p=node('p',item.snippet||item.content||''),a=node('a',item.title||item.url);try{const url=new URL(item.url);if(!['https:','http:'].includes(url.protocol))continue;a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';}catch{continue;}details.append(a,p);}
          else details.append(node('p',resultText(step),'mission-text'));
          if(step.result.evidence?.checked_at)details.append(node('small','Результат получен: '+new Date(step.result.evidence.checked_at*1000).toLocaleString()));
          if(step.result.kind==='draft')details.append(node('p','Текст подготовлен моделью. Проверь факты и источники перед использованием.','notebook-hint'));
          row.append(details);
        }
      }
      if(step.status==='awaiting_approval'&&step.approval){
        const approval=step.approval,p=approval.preview,box=node('section',undefined,'mission-approval');
        box.append(node('strong',p.action==='calendar.create'?'Создать событие в основном календаре?':p.action==='gmail.draft'?'Создать черновик в Gmail?':'Согласовать подготовленный текст?'));
        if(p.action==='calendar.create')box.append(node('p',[p.title,p.start+' → '+p.end,p.time_zone,'Приглашения участникам не отправляются.'].join('\n'),'mission-text'));
        else if(p.action==='gmail.draft')box.append(node('p','Кому: '+p.to+'\nТема: '+p.subject+'\n\n'+p.body,'mission-text'));
        else box.append(node('p',p.text,'mission-text'));
        const expired=approval.expires_at*1000<=Date.now();
        const confirm=button(expired?'Обновить подтверждение':p.action==='calendar.create'?'Подтвердить и создать событие':p.action==='gmail.draft'?'Создать черновик Gmail':'Согласовать',()=>run(m,expired?'refresh-approval':'approve',confirm));box.append(confirm);row.append(box);
      }
      steps.append(row);
    }
    root.append(steps);
    if(m.status==='uncertain')root.append(node('p','Проверь внешний сервис: операция могла выполниться. Автоматический повтор заблокирован.','feature-notice'));
    const actions=node('div',undefined,'feature-actions');
    if(['draft','paused','failed','interrupted'].includes(m.status)){const b=button(m.status==='draft'?'Запустить':'Продолжить с сохранённого шага',()=>run(m,'run',b));actions.append(b);}
    if(active(m)){const pause=button('Приостановить',()=>run(m,'pause',pause));actions.append(pause);}
    if(!['completed','cancelled'].includes(m.status)){const cancel=button('Отменить миссию',()=>run(m,'cancel',cancel));actions.append(cancel);}
    const refresh=button('Обновить',async()=>{try{await client.refresh(id);}catch(e){if(!signal.aborted)toast(e.message);}});actions.append(refresh);
    if(!active(m)){const remove=button('Удалить',async()=>{if(!confirm('Удалить миссию и её файлы?'))return;try{await client.remove(m);if(!signal.aborted)root.replaceChildren(node('p','Миссия удалена.'));}catch(e){if(!signal.aborted)toast(e.message);}});actions.append(remove);}
    root.append(actions);
  }
  const unsubscribe=client.subscribe(draw);signal.addEventListener('abort',()=>{unsubscribe();unwatch();},{once:true});
  draw();if(!client.get(id))client.refresh(id).catch(e=>{if(!signal.aborted)root.replaceChildren(node('p',e.message,'feature-notice'));});
  return root;
}
export async function mountMissionList(parent,{client,signal,toast,prepare,selectedId}){
  const list=node('div'),detail=node('div');parent.append(button('Новая миссия в чате',prepare),button('Обновить миссии',()=>client.poll().catch(e=>toast(e.message))),list,detail);
  let controller=null,signature='';
  function open(id){controller?.abort();controller=new AbortController();detail.replaceChildren();mountMissionCard(detail,{client,id,signal:controller.signal,toast});}
  function draw(){
    if(signal.aborted)return;const rows=client.list(),next=rows.map(m=>m.id+':'+m.revision).join('|');if(next===signature&&list.childNodes.length)return;signature=next;
    list.replaceChildren();if(!rows.length)list.append(node('p','Миссий пока нет. Опиши в чате цель и нужные результаты.','feature-empty'));
    for(const m of rows){const card=node('article',undefined,'feature-card');card.append(node('strong',m.title),node('p',MISSION_LABELS[m.status]||m.status),button('Открыть шаги и результаты',()=>open(m.id)));list.append(card);}
  }
  const unsubscribe=client.subscribe(draw);signal.addEventListener('abort',()=>{unsubscribe();controller?.abort();},{once:true});draw();
  try{await client.poll();if(!signal.aborted&&selectedId)open(selectedId);}catch(e){if(!signal.aborted)list.replaceChildren(node('p',e.message,'feature-notice'));}
}
