const gamesOrigin = 'https://velyoncore.ai';
const privatePath = /^\/game\/project\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/s[1-8]\/$/u;
const sharePath = /^\/game\/share\/[A-Za-z0-9_-]{150,350}\/$/u;

export function wantsGame(text) {
  const match = String(text).trim().match(/^(?:велия[,\s]+)?(?:(?:создай|сделай|разработай|собери|напиши)\s+(?:[\p{L}\p{N}_-]+\s+){0,5}(?:(?:2d|html5)[- ])?(?:игру|мини[- ]?игру|игрушку|(?:ретро[- ]?)?шутер)|(?:create|build|make|develop)\s+(?:[\p{L}\p{N}_-]+\s+){0,5}(?:(?:2d|html5)[- ])?(?:game|mini[- ]?game|(?:retro[- ]?)?shooter|fps))(?=$|[\s,.!:?])/iu);
  if (!match) return false;
  const [prefix] = match[0].split(/(?:игру|мини[- ]?игру|игрушку|(?:ретро[- ]?)?шутер|\b(?:game|mini[- ]?game|(?:retro[- ]?)?shooter|fps)\b)/iu, 1);
  return !/(?:^|\s)(?:план|концепци\p{L}*|иде[яию]|сценари[йя]|plan|concept|idea)(?=\s|$)/iu.test(prefix);
}

export function wantsGameRevision(text) {
  const value=String(text).trim().replace(/^велия[,\s]+/iu,'');
  return /^(?:доработай|обнови|измени|переделай|исправь|переименуй)\s+(?:эту\s+|мою\s+|нашу\s+)?(?:мини[- ]?)?игру(?=$|[\s,.!:?])/iu.test(value)
      || /^сделай\s+(?:(?:эту\s+)?игру\s+)?(?:сложнее|проще|легче|быстрее|медленнее)(?=$|[\s,.!:?])/iu.test(value)
      || /^(?:добавь|убери|увеличь|уменьши|поменяй|подними|снизь|поставь)\s+(?:\d+\s+секунд|[^\n]*(?:таймер|сложност|светляч|предмет|скорост|тему\s+игры))/iu.test(value)
      || /^(?:edit|revise|update|rename)\s+(?:(?:this|the|my|our)\s+)?(?:mini[- ]?)?game\b/iu.test(value);
}

export function gamePreviewLink(raw) {
  if (typeof raw !== 'string') return '';
  try {
    const url = new URL(raw, gamesOrigin);
    if (url.origin === gamesOrigin && privatePath.test(url.pathname) && !url.search && !url.hash
        && !url.username && !url.password) return url.href;
  } catch { /* Invalid results never become navigation targets. */ }
  return '';
}

export function gameDownloadLink(raw) {
  const play = gamePreviewLink(raw);
  return play ? new URL('download', play).href : '';
}

export function mountGameExport({url,client,id,step,model,chatBound,signal}) {
  const node=(tag,text)=>{
    const el=document.createElement(tag);
    if(text)el.textContent=text;
    return el;
  };
  const link=(text,href)=>{
    const a=node('a',text);a.href=href;a.target='_blank';a.rel='noopener noreferrer';a.className='mission-file';
    return a;
  };
  const box=node('section');
  const controls=node('div');controls.className='feature-actions';
  controls.append(link('Играть',url));
  const zip=gameDownloadLink(url);
  if(zip)controls.append(link('Скачать ZIP',zip));
  const edit=node('button','Доработать');edit.type='button';edit.setAttribute('aria-expanded','false');controls.append(edit);
  const editForm=node('form');editForm.className='feature-form';editForm.hidden=true;
  const instruction=node('textarea');instruction.setAttribute('aria-label','Что изменить в игре');
  instruction.placeholder='Например, поменяй название или оформление';
  instruction.rows=3;instruction.maxLength=1500;instruction.required=true;
  const revise=node('button','Создать новую версию');revise.type='submit';
  const editStatus=node('p');editStatus.setAttribute('role','status');
  editForm.append(instruction,revise,editStatus);
  let revisionKey='', lastInstruction='';
  edit.onclick=()=>{
    editForm.hidden=!editForm.hidden;
    edit.setAttribute('aria-expanded',String(!editForm.hidden));
    if(!editForm.hidden)instruction.focus();
  };
  editForm.onsubmit=async event=>{
    event.preventDefault();
    const value=instruction.value.trim();
    if(revise.disabled||!value||signal.aborted)return;
    if(value!==lastInstruction){lastInstruction=value;revisionKey=crypto.randomUUID();}
    revise.disabled=true;instruction.disabled=true;editStatus.textContent='Сохраняю правки…';
    try{
      await client.reviseGame(id,step,{instruction:value,model,request_id:revisionKey},signal);
      instruction.value='';lastInstruction='';
      editStatus.textContent=chatBound?'Велия готовит новую версию. Она появится в исходном чате и в «Задачах».':'Велия готовит новую версию. Она появится в «Задачах».';
    }catch(error){if(!signal.aborted)editStatus.textContent=error.message;}
    finally{revise.disabled=false;instruction.disabled=false;}
  };
  const access=node('button','Доступ');access.type='button';
  access.setAttribute('aria-expanded','false');controls.append(access);
  const sharing=node('section');sharing.hidden=true;
  const shareForm=node('form');shareForm.className='feature-form';
  const defaultAccess=node('p','Игра закрыта по умолчанию. Выдай отдельную ссылку, чтобы открыть доступ.');
  const scope=node('select');scope.setAttribute('aria-label','Кому дать доступ');
  const placeholder=node('option','Выбери, кому дать доступ');placeholder.value='';
  const everyone=node('option','Всем, у кого есть ссылка');everyone.value='public';
  const individual=node('option','Конкретному аккаунту');individual.value='personal';
  scope.append(placeholder,everyone,individual);scope.required=true;
  const recipient=node('input');recipient.type='text';recipient.placeholder='Код получателя';
  recipient.autocomplete='off';recipient.maxLength=24;recipient.pattern='[a-fA-F0-9]{24}';
  recipient.setAttribute('aria-label','Код получателя');
  const recipientHelp=node('label');recipientHelp.hidden=true;
  recipientHelp.append(node('span','Попроси получателя прислать его код Велии.'),recipient,
                       link('Где получить код',gamesOrigin+'/game/access-code'));
  const createShare=node('button','Создать ссылку');createShare.type='submit';createShare.disabled=true;
  const shareStatus=node('p');shareStatus.setAttribute('role','status');
  const shareList=node('div');
  recipient.disabled=true;
  shareForm.append(defaultAccess,scope,recipientHelp,createShare,shareStatus,shareList);sharing.append(shareForm);
  let sharingBusy=false;
  const shareRequest=(suffix,options={})=>client.gameShares(id,step,suffix,{...options,signal});
  const lockSharing=value=>{
    sharingBusy=value;scope.disabled=value;recipient.disabled=value||recipientHelp.hidden;createShare.disabled=value||!scope.value;
    for(const button of shareList.querySelectorAll('button'))button.disabled=value;
  };
  function drawShares(shares){
    shareList.replaceChildren();
    for(const share of shares){
      if(!sharePath.test(share.url)||!/^\w{32}$/.test(share.id))continue;
      const title=share.scope==='personal'?'Персональная ссылка':'Для всех по ссылке';
      const row=node('div');row.className='feature-actions';
      const address=node('input');address.value=new URL(share.url,gamesOrigin).href;
      address.readOnly=true;address.setAttribute('aria-label',title);
      const copy=node('button','Копировать');copy.type='button';
      copy.onclick=async()=>{
        try{await navigator.clipboard.writeText(address.value);shareStatus.textContent='Ссылка скопирована.';}
        catch{address.select();shareStatus.textContent='Скопируй выделенную ссылку.';}
      };
      const revoke=node('button','Отозвать');revoke.type='button';
      revoke.onclick=async()=>{
        if(sharingBusy)return;
        lockSharing(true);
        try{
          drawShares((await shareRequest('/'+share.id,{method:'DELETE',data:{}})).shares);
          shareStatus.textContent='Ссылка отозвана.';
        }catch(error){if(!signal.aborted)shareStatus.textContent=error.message;}
        finally{lockSharing(false);}
      };
      row.append(node('span',title),address,copy,revoke);shareList.append(row);
    }
  }
  access.onclick=async()=>{
    sharing.hidden=!sharing.hidden;
    access.setAttribute('aria-expanded',String(!sharing.hidden));
    if(sharing.hidden||sharingBusy)return;
    lockSharing(true);shareStatus.textContent='Проверяю доступ…';
    try{drawShares((await shareRequest('')).shares);shareStatus.textContent='';}
    catch(error){if(!signal.aborted)shareStatus.textContent=error.message;}
    finally{lockSharing(false);}
  };
  scope.onchange=()=>{
    recipientHelp.hidden=scope.value!=='personal';recipient.required=!recipientHelp.hidden;
    lockSharing(false);
  };
  shareForm.onsubmit=async event=>{
    event.preventDefault();
    if(sharingBusy||!scope.value)return;
    lockSharing(true);
    try{
      const data=scope.value==='personal'?{scope:'personal',recipient:recipient.value.trim().toLowerCase()}:{scope:'public'};
      drawShares((await shareRequest('',{method:'POST',data})).shares);
      recipient.value='';shareStatus.textContent='Ссылка создана.';
    }catch(error){if(!signal.aborted)shareStatus.textContent=error.message;}
    finally{lockSharing(false);}
  };
  const toggle=node('button','В GitHub');toggle.type='button';toggle.setAttribute('aria-expanded','false');controls.append(toggle);
  const form=node('form');form.className='feature-form';form.hidden=true;
  const repositories=node('select');repositories.setAttribute('aria-label','Репозиторий GitHub');
  const install=node('select');install.setAttribute('aria-label','Аккаунт GitHub');
  const submit=node('button','Сохранить в GitHub');submit.type='submit';submit.disabled=true;
  const status=node('p');status.setAttribute('role','status');
  const result=node('div');
  form.append(install,repositories,submit,status,result);
  box.append(controls,editForm,sharing,form);
  const request=(suffix,options={})=>client.gameGithub(id,step,suffix,{...options,signal});
  const option=(label,value)=>{const o=node('option',label);o.value=String(value);return o;};
  let busy=false,target=null;
  const selectedTarget=()=>target&&String(target.installation_id)===install.value&&String(target.repository_id)===repositories.value;
  function lock(value){
    busy=value;install.disabled=value;repositories.disabled=value;
    submit.disabled=value||!repositories.value;
    submit.textContent=selectedTarget()?'Обновить ветку GitHub':'Сохранить в GitHub';
  }
  async function loadRepos(){
    repositories.replaceChildren(option('Выбери репозиторий', ''));
    if(!install.value)return;
    const payload=await request('/repositories?installation_id='+encodeURIComponent(install.value));
    const repos=payload.repositories.filter(item=>!item.archived&&Number.isSafeInteger(item.id)&&item.id>0);
    for(const item of repos)repositories.append(option(item.full_name+(item.private?' (приватный)':''),item.id));
    if(target&&repos.some(item=>item.id===target.repository_id)&&String(target.installation_id)===install.value)repositories.value=String(target.repository_id);
    else if(repos.length===1)repositories.value=String(repos[0].id);
    status.textContent=repos.length?'':'В этом подключении нет доступных репозиториев.';
  }
  toggle.onclick=async()=>{
    form.hidden=!form.hidden;
    toggle.setAttribute('aria-expanded',String(!form.hidden));
    if(form.hidden||busy||install.options.length)return;
    lock(true);status.textContent='Проверяю подключение GitHub…';
    try{
      const [payload,saved]=await Promise.all([request('/installations'),request('/target').catch(()=>null)]);
      target=saved?.target||null;
      if(signal.aborted)return;
      if(!payload.installations.length){
        status.textContent='Подключи GitHub в разделе «Интеграции» и нажми «В GitHub» снова.';
        return;
      }
      install.replaceChildren(option('Выбери аккаунт GitHub',''));
      for(const item of payload.installations)install.append(option(item.account_login,item.installation_id));
      if(target&&payload.installations.some(item=>item.installation_id===target.installation_id))install.value=String(target.installation_id);
      else if(payload.installations.length===1)install.value=String(payload.installations[0].installation_id);
      status.textContent='';
      await loadRepos();
    }catch(error){if(!signal.aborted)status.textContent=error.message;}
    finally{lock(false);}
  };
  install.onchange=async()=>{
    if(busy)return;
    lock(true);result.replaceChildren();status.textContent='Загружаю репозитории…';
    try{await loadRepos();}catch(error){if(!signal.aborted)status.textContent=error.message;}
    finally{lock(false);}
  };
  repositories.onchange=()=>{lock(false);status.textContent='';result.replaceChildren();};
  form.onsubmit=async event=>{
    event.preventDefault();
    if(busy||!install.value||!repositories.value)return;
    lock(true);result.replaceChildren();status.textContent='Сохраняю версию в выбранном репозитории…';
    try{
      const payload=await request('',{method:'POST',data:{installation_id:Number(install.value),repository_id:Number(repositories.value)}});
      if(signal.aborted)return;
      const remote=new URL(payload.export.url);
      if(remote.origin!=='https://github.com'||remote.username||remote.password||
          !/^\/[\w.-]+\/[\w.-]+\/tree\/[a-f0-9]{40}\/games\/velia-[a-f0-9-]{36}$/u.test(remote.pathname))
        throw Error('GitHub вернул некорректную ссылку.');
      target={installation_id:Number(install.value),repository_id:Number(repositories.value)};
      status.textContent='Версия сохранена в ветке игры.';
      result.append(link('Открыть файлы в GitHub',remote.href));
    }catch(error){if(!signal.aborted)status.textContent=error.message;}
    finally{lock(false);}
  };
  return box;
}
