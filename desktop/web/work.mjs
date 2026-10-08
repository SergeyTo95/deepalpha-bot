const node = (tag, text, cls) => {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if(cls) n.className = cls; return n;};
const states = {draft:'Черновик',queued:'В очереди',needs_revision:'Нужна доработка',planning:'Планирование',executing:'Выполнение',reviewing:'Проверка',ready:'Результат подготовлен',completed:'Результат подготовлен',failed:'Ошибка',cancelled:'Отменено',running:'В работе',interrupted:'Прервано'};
export async function mountWork(parent, {signal, toast = () => {}} = {}) {
  const root = node('section', undefined, 'feature-work'); parent.append(root);
  let timer, workspace, status, refreshing = false, connecting = false, connectionFeedback={text:'',error:false};
  const live = () => !signal?.aborted && root.isConnected;
  signal?.addEventListener('abort', () => clearTimeout(timer), {once:true});
  async function call(path, method = 'GET', data) {
    const response = await fetch('/web-api/v1/work/' + path, {method, credentials:'same-origin',signal,
      headers:method === 'GET' ? {} : {'Content-Type':'application/json','X-Velia-Request':'1'},
      body:method === 'GET' ? undefined : JSON.stringify(data || {})});
    const result = await response.json();
    const errors={upwork_connect_timeout:'Upwork не ответил вовремя. Подключение не создано; попробуй позже.',upwork_discovery_unavailable:'Сервер авторизации Upwork сейчас недоступен. Подключение не создано.',upwork_access_denied:'Upwork не подтвердил доступ. Попробуй подключиться позже.',upwork_connect_rate_limit:'Подожди минуту перед следующей проверкой Upwork.',upwork_reconnect_required:'Требуется повторный вход в Upwork.',upwork_service_unavailable:'Сервис Upwork сейчас недоступен.'};
    if(!response.ok || result.ok === false) throw new Error(response.status === 401 ? 'Войди в VELIA, чтобы продолжить.' : errors[result.error] || 'Действие недоступно: ' + (result.error || response.status));
    return result;
  }
  const id = () => crypto.randomUUID();
  function action(text, fn) {
    const b = node('button',text); b.type = 'button';
    b.onclick = async () => {b.disabled = true; try {await fn();} catch(e) {if(e.name !== 'AbortError' && live()) toast(e.message);} finally {if(live()) b.disabled = false;}};
    return b;
  }
  function form(fields, label, initial, submit) {
    const f = node('form', undefined, 'feature-form'), controls = {};
    for(const [key,title,type='text',required=true] of fields) {
      const l = node('label',undefined,'feature-input'); l.append(node('span',title));
      const c = node(key === 'network' ? 'select' : type === 'textarea' ? 'textarea':'input'); if(type !== 'textarea' && key !== 'network') c.type = type;
      if(key === 'network') for(const [value,label] of [['','Не выбрана'],['tron','TRON'],['ethereum','Ethereum'],['polygon','Polygon'],['bnb','BNB Chain'],['arbitrum','Arbitrum']]) {const option=node('option',label);option.value=value;c.append(option);}
      c.name = key; c.value = initial?.[key] ?? ''; c.required = required; c.maxLength = type === 'textarea' ? 8000 : 500;
      if(key.includes('usdt')) {c.type='text'; c.inputMode='decimal'; c.pattern='[0-9]+([.][0-9]{1,6})?';}
      controls[key]=c; l.append(c); f.append(l);
    }
    const b = node('button',label,'feature-primary'); b.type='submit'; f.append(b);
    f.onsubmit = async e => {e.preventDefault(); if(!f.reportValidity()) return; b.disabled=true;
      try {await submit(Object.fromEntries(Object.entries(controls).map(([k,c])=>[k,c.value])));} catch(err) {if(err.name !== 'AbortError' && live()) toast(err.message);} finally {if(live()) b.disabled=false;}};
    return f;
  }
  function detail(job, target) {
    target.replaceChildren();
    for(const [i,revision] of (job.revision_history || []).entries()) {
      const d=node('details');d.append(node('summary','Предыдущая версия '+(i+1)));
      const p=node('pre',revision.executor+'\n\nПроверка:\n'+revision.reviewer);p.style.whiteSpace='pre-wrap';p.style.overflowWrap='anywhere';d.append(p);target.append(d);
    }
    for(const role of ['manager','proposal','executor','reviewer','treasurer']) {
      const value=job.outputs?.[role]; if(value == null) continue;
      const d=node('details'); d.append(node('summary',({manager:'Управляющий',proposal:'Черновик заявки',executor:'Исполнитель',reviewer:'Контролёр качества',treasurer:'Казначей'})[role]));
      const p=node('pre', typeof value === 'string' ? value : JSON.stringify(value,null,2)); p.style.whiteSpace='pre-wrap'; p.style.overflowWrap='anywhere'; d.append(p);target.append(d);
    }
    if(job.error) target.append(node('p','Ошибка: '+job.error));
  }
  function draw() {
    if(!live()) return; root.replaceChildren(node('h2','Работа и заработок'));
    root.append(node('p','Команда агентов Flash готовит текстовые черновики и проверяет их содержание. В этой версии агенты не запускают код, не регистрируются на сайтах и не берут внешние заказы. Подготовленная работа не означает сдачу заказа или получение оплаты.'));
    root.append(node('p',status.available ? 'Рабочее пространство подключено.' : 'Рабочее пространство ещё не подключено. Задания и кошелёк пока недоступны.'));
    root.append(node('p','Роли: '+(status.roles || []).map(r=>r.name).join(' · ')));
    if(!status.available) {root.append(action('Обновить',refresh));return;}
    root.append(node('p','Площадки: '+(status.connectors || []).map(c=>c.name+': '+(c.connected?'подключена':'не подключена')).join(' · ')));
    if(status.upwork_available) {
      const upwork=(status.connectors || []).find(c=>c.id==='upwork');
      const connectionMessage=node('p',connectionFeedback.text);connectionMessage.setAttribute('role',connectionFeedback.error?'alert':'status');connectionMessage.setAttribute('aria-live','polite');
      const showConnection=(text,error=false)=>{connectionFeedback={text,error};connectionMessage.textContent=text;connectionMessage.setAttribute('role',error?'alert':'status');};
      root.append(node('p','Upwork: вход и разрешение доступа выполняются на сайте площадки. Сейчас подключение проверяет доступ и список инструментов; отправка заявок ещё не включена.'));
      if(upwork?.connected)root.append(node('p','Доступ проверен · инструментов: '+upwork.tool_count));
      if(upwork?.connected){const catalog=node('div');root.append(action('Инструменты Upwork',async()=>{const result=await call('upwork/capabilities');if(!live())return;catalog.replaceChildren(node('p','Проверенный каталог. Автоматическое выполнение методов ещё не включено.'));for(const tool of result.tools || []) {const d=node('details');d.append(node('summary',tool.name));const p=node('pre',JSON.stringify({description:tool.description,inputSchema:tool.inputSchema,annotations:tool.annotations},null,2));p.style.whiteSpace='pre-wrap';p.style.overflowWrap='anywhere';d.append(p);catalog.append(d);}}),catalog);}
      if(upwork?.connected || upwork?.status==='reconnect_required')root.append(action('Проверить и обновить доступ Upwork',async()=>{await call('upwork/verify','POST',{});await refresh();}));
      root.append(action(upwork?.connected?'Отключить Upwork':'Подключить Upwork',async()=>{
        if(upwork?.connected){await call('upwork/disconnect','POST',{});await refresh();return;}
        connecting=true;showConnection('Проверяю сервер авторизации Upwork…');
        try {
          const result=await call('upwork/connect','POST',{});if(!live())return;const url=new URL(result.authorization_url);
          if(url.protocol!=='https:' || !(url.hostname==='upwork.com' || url.hostname.endsWith('.upwork.com')))throw new Error('Некорректный адрес авторизации');
          showConnection('Перехожу на Upwork для входа…');window.location.assign(url.href);
        } catch(error) {
          if(error.name!=='AbortError' && live())showConnection(error.message,true);
          throw error;
        } finally {connecting=false;}
      }),connectionMessage);
    }
    root.append(node('p','Кошелёк не подключён. Подтверждённый баланс и доход недоступны.'));
    root.append(action('Обновить',refresh));
    const auto=workspace.autonomy || {enabled:false,query:'',interval_minutes:60,max_jobs_per_day:1};
    const autonomy=node('details');autonomy.append(node('summary','Автономный поиск и выполнение'));
    autonomy.append(node('p',auto.enabled?'Автономный режим включён.':'Автономный режим выключен.'));
    autonomy.append(node('p','Команда ищет возможности, оценивает условия, готовит заявку и текстовый результат. Заявки остаются черновиками до подключения площадки. Интервал 15–1440 минут, максимум 1–3 новых задания в сутки (UTC). После перезапуска сервера нужно снова открыть раздел с действующим входом.'));
    autonomy.append(form([['query','Какие текстовые задания искать'],['interval_minutes','Интервал поиска, минут','number'],['max_jobs_per_day','Новых заданий в сутки','number']], 'Сохранить направление',auto,async data=>{await call('autonomy','PUT',{...data,enabled:auto.enabled,interval_minutes:Number(data.interval_minutes),max_jobs_per_day:Number(data.max_jobs_per_day)});await refresh();}));
    autonomy.append(action(auto.enabled?'Выключить автономный режим':'Включить автономный режим',async()=>{await call('autonomy','PUT',{...auto,enabled:!auto.enabled});await refresh();}));
    if(auto.enabled)autonomy.append(action('Проверить поиск сейчас',async()=>{await call('scan','POST',{});toast('Проверка запланирована; сохраняются интервал и суточный лимит.');}));
    if(workspace.last_scan_at)autonomy.append(node('p','Последняя проверка: '+new Date(workspace.last_scan_at*1000).toLocaleString()+(workspace.last_scan_error?' · '+workspace.last_scan_error:'')));
    root.append(autonomy);
    const policy=node('details');policy.append(node('summary','Правила казначея'));
    policy.append(form([['reserve_usdt','Рабочий резерв, USDT'],['max_expense_usdt','Лимит одного расхода, USDT'],['owner_address','Адрес владельца','text',false],['network','Сеть USDT','text',false],['agent_share_percent','Доля рабочего бюджета, %','number']], 'Сохранить правила',workspace.mandate, async data=> {data.agent_share_percent=Number(data.agent_share_percent);await call('mandate','PUT',data);toast('Правила сохранены');await refresh();}));root.append(policy);
    root.append(node('h3','Новое задание'));
    const jobForm=form([['title','Название'],['brief','Требования и критерии результата','textarea'],['source_url','Ссылка на источник','url',false],['expected_usdt','Предполагаемая оплата, USDT']], 'Создать задание',{expected_usdt:'0'},async data=> {await call('jobs','POST',{...data,client_request_id:id()});await refresh();});
    jobForm.elements.title.maxLength=120;jobForm.elements.brief.maxLength=6000;
    root.append(jobForm);
    if(status.search_available) {
      root.append(node('h3','Поиск возможностей'));
      const results=node('div');
      root.append(form([['query','Что искать']], 'Найти',{},async data=> {const result=await call('discover','POST',data);if(!live())return;results.replaceChildren(node('p','Кандидаты из поиска: условия и доступность заказов ещё не проверены.'));for(const item of result.results || []) {const p=node('p');let u;try{u=new URL(item.url);}catch{continue;}if(!['https:','http:'].includes(u.protocol))continue;const a=node('a',item.title);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';p.append(a,node('span',' — '+(item.snippet||'')));p.append(action('Подготовить задание',async()=>{
        jobForm.elements.title.value=String(item.title || 'Найденная возможность').slice(0,120);
        jobForm.elements.source_url.value=u.href;
        jobForm.elements.brief.value=('Источник из поиска, условия не проверены.\n'+String(item.snippet || '')+'\n\nТребования и критерии результата: ').slice(0,6000);
        jobForm.elements.expected_usdt.value='0';
        jobForm.scrollIntoView?.({behavior:'smooth',block:'center'});jobForm.elements.brief.focus();
        toast('Ссылка добавлена. Уточни требования и оплату перед созданием задания.');
      }));results.append(p);}}),results);
    }
    root.append(node('h3','Задания'));
    if(!(workspace.jobs || []).length)root.append(node('p','Заданий пока нет.'));
    for(const job of workspace.jobs || []) {
      const card=node('article',undefined,'feature-card');card.append(node('h4',job.title),node('p',states[job.status] || job.status),node('p','Предполагаемая оплата: '+(job.expected_usdt ?? '0')+' USDT · получение не подтверждено'));
      const output=node('div');detail(job,output);
      if(job.autonomous)card.append(node('p','Найдено командой автоматически · внешний заказ не принят'));
      if(job.status==='ready') {const download=node('a','Скачать результат и проверку (ZIP)');download.href='/web-api/v1/work/jobs/'+encodeURIComponent(job.id)+'/artifacts';card.append(download);}
      card.append(action('Результаты',async()=>{const result=await call('jobs/'+encodeURIComponent(job.id));if(live())detail(result.job || result,output);}));
      if(['draft','queued','failed','interrupted'].includes(job.status)) {const start=action('Запустить команду',async()=>{await call('jobs/'+encodeURIComponent(job.id)+'/run','POST',{});await refresh();});start.disabled=!status.available;card.append(start);}
      if(['draft','queued','planning','executing','reviewing','running'].includes(job.status))card.append(action('Отменить',async()=>{await call('jobs/'+encodeURIComponent(job.id)+'/cancel','POST',{});await refresh();}));
      card.append(output);root.append(card);
    }
    const payout=node('details');payout.append(node('summary','Запросить выплату'));
    payout.append(node('p','Казначей подготовит решение. Без подключения сервиса подписи перевод не выполняется.'));
    payout.append(form([['amount_usdt','Сумма, USDT'],['reason','Причина','textarea']], 'Передать казначею',{},async data=>{await call('payouts','POST',{...data,client_request_id:id()});toast('Запрос сохранён. Реальный перевод недоступен.');await refresh();}));root.append(payout);
    for(const p of workspace.payouts || []) root.append(node('p','Запрос выплаты: '+(p.amount_usdt || '')+' USDT · '+(p.status || 'ожидает')+' · перевод не подтверждён'));
  }
  async function refresh() {
    if(refreshing || !live())return;refreshing=true;clearTimeout(timer);
    try {status=await call('status');workspace=status.available ? await call('workspace') : null;const editing=root.contains(document.activeElement) && ['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName);if(!editing && !connecting)draw();}
    finally {refreshing=false;const running=workspace?.jobs?.some(j=>['planning','executing','reviewing','running'].includes(j.status));if(live() && (running || workspace?.autonomy?.enabled))timer=setTimeout(()=>refresh().catch(e=>{if(e.name!=='AbortError'&&live())toast(e.message);}),running?4000:15000);}
  }
  root.append(node('p','Загрузка рабочего пространства…'));await refresh();
}
