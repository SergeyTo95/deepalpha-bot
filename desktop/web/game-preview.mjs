const previewPaths = new Map([
  ['https://velyoncore.ai','/game/'],
  ['https://velia-game-preview-production.up.railway.app','/'],
  ['https://games.velyoncore.ai','/'],
]);

export function wantsGame(text) {
  const match = String(text).trim().match(/^(?:велия[,\s]+)?(?:(?:создай|сделай|разработай|собери|напиши)\s+(?:[\p{L}\p{N}_-]+\s+){0,5}(?:(?:2d|html5)[- ])?(?:игру|мини[- ]?игру|игрушку)|(?:create|build|make|develop)\s+(?:[\p{L}\p{N}_-]+\s+){0,5}(?:(?:2d|html5)[- ])?(?:game|mini[- ]?game))(?=$|[\s,.!:?])/iu);
  if (!match) return false;
  const [prefix] = match[0].split(/(?:игру|мини[- ]?игру|игрушку|\bgame\b|mini[- ]?game)/iu, 1);
  return !/(?:^|\s)(?:план|концепци\p{L}*|иде[яию]|сценари[йя]|plan|concept|idea)(?=\s|$)/iu.test(prefix);
}

export function gamePreviewLink(raw) {
  try {
    const url = new URL(raw);
    if (url.pathname === previewPaths.get(url.origin) && !url.search && !url.username && !url.password
        && url.hash.startsWith('#v1=') && url.hash.length <= 1701) return 'https://velyoncore.ai/game/' + url.hash;
  } catch { /* Invalid results never become navigation targets. */ }
  return '';
}

export function gameDownloadLink(raw) {
  const play = gamePreviewLink(raw);
  if (!play) return '';
  try {
    const url = new URL(play);
    const spec = decodeURIComponent(url.hash.slice(4));
    JSON.parse(spec);
    return new URL('download?' + new URLSearchParams({spec}), url).href;
  } catch { return ''; }
}

export function mountGameExport({url,client,id,step,signal}) {
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
  const toggle=node('button','В GitHub');toggle.type='button';controls.append(toggle);
  const form=node('form');form.className='feature-form';form.hidden=true;
  const repositories=node('select');repositories.setAttribute('aria-label','Репозиторий GitHub');
  const install=node('select');install.setAttribute('aria-label','Аккаунт GitHub');
  const submit=node('button','Загрузить в новую ветку');submit.type='submit';submit.disabled=true;
  const status=node('p');status.setAttribute('role','status');
  const result=node('div');
  form.append(install,repositories,submit,status,result);
  box.append(controls,form);
  const request=(suffix,options={})=>client.gameGithub(id,step,suffix,{...options,signal});
  const option=(label,value)=>{const o=node('option',label);o.value=String(value);return o;};
  let busy=false;
  function lock(value){
    busy=value;install.disabled=value;repositories.disabled=value;
    submit.disabled=value||!repositories.value;
  }
  async function loadRepos(){
    repositories.replaceChildren(option('Выбери репозиторий', ''));
    if(!install.value)return;
    const payload=await request('/repositories?installation_id='+encodeURIComponent(install.value));
    const repos=payload.repositories.filter(item=>!item.archived&&Number.isSafeInteger(item.id)&&item.id>0);
    for(const item of repos)repositories.append(option(item.full_name+(item.private?' (приватный)':''),item.id));
    if(repos.length===1)repositories.value=String(repos[0].id);
    status.textContent=repos.length?'':'В этом подключении нет доступных репозиториев.';
  }
  toggle.onclick=async()=>{
    form.hidden=!form.hidden;
    toggle.setAttribute('aria-expanded',String(!form.hidden));
    if(form.hidden||busy||install.options.length)return;
    lock(true);status.textContent='Проверяю подключение GitHub…';
    try{
      const payload=await request('/installations');
      if(signal.aborted)return;
      if(!payload.installations.length){
        status.textContent='Подключи GitHub в разделе «Интеграции» и нажми «В GitHub» снова.';
        return;
      }
      install.replaceChildren(option('Выбери аккаунт GitHub',''));
      for(const item of payload.installations)install.append(option(item.account_login,item.installation_id));
      if(payload.installations.length===1)install.value=String(payload.installations[0].installation_id);
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
  repositories.onchange=()=>{submit.disabled=!repositories.value;status.textContent='';result.replaceChildren();};
  form.onsubmit=async event=>{
    event.preventDefault();
    if(busy||!install.value||!repositories.value)return;
    lock(true);result.replaceChildren();status.textContent='Отправляю файлы в выбранный репозиторий…';
    try{
      const payload=await request('',{method:'POST',data:{installation_id:Number(install.value),repository_id:Number(repositories.value)}});
      if(signal.aborted)return;
      const remote=new URL(payload.export.url);
      if(remote.origin!=='https://github.com'||remote.username||remote.password||
          !/^\/[\w.-]+\/[\w.-]+\/tree\/[a-f0-9]{40}\/games\/velia-[a-f0-9-]{36}$/u.test(remote.pathname))
        throw Error('GitHub вернул некорректную ссылку.');
      status.textContent='Файлы загружены в новую ветку.';
      result.append(link('Открыть файлы в GitHub',remote.href));
    }catch(error){if(!signal.aborted)status.textContent=error.message;}
    finally{lock(false);}
  };
  return box;
}
