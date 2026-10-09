import {renderMarkdown} from './core.mjs';
import {mountWork} from './work.mjs';

export const SECTIONS = Object.freeze([
  ['studio', 'Studio'], ['projects', 'Проекты'], ['research', 'Исследования'],
  ['medical', 'Медицинский центр'], ['agents', 'Мои агенты'],
  ['work', 'Работа и заработок'], ['autopilot', 'Автопилот'], ['profile', 'Персонализация'],
  ['plugins', 'Плагины'], ['balance', 'Баланс и использование'], ['voice', 'Голос'],
]);
export function mediaPath(value) {
  try {
    const u = new URL(value, location.origin);
    // Only existing signed mobile media routes, never a model supplied host.
    const match = u.pathname.match(/^\/api\/mobile\/(images|videos|music)\/([A-Za-z0-9_-]+)\/content$/);
    const ref = u.pathname.match(/^\/api\/mobile\/studio\/assets\/([A-Za-z0-9_-]+)\/content$/);
    if (!match && !ref) return null;
    if ([...u.searchParams.keys()].some(k => !['user_id', 'expires', 'signature'].includes(k))) return null;
    return '/web-api/v1/features/' + (match ? 'media/' + match[1] + '/' + match[2] : 'studio-assets/' + ref[1]) + '/content' + u.search;
  } catch { return null; }
}
export async function featureRequest(path, {method = 'GET', data, file, digest, key, signal} = {}) {
  const headers = method === 'GET' ? {} : {'X-Velia-Request': '1'};
  let body;
  if (file) {
    if (method === 'PUT') {body = file; headers['Content-Type'] = 'application/octet-stream'; headers['X-Velia-Chunk-SHA256'] = digest;}
    else {body = new FormData(); body.append('file', file);}
  } else if (method !== 'GET') {headers['Content-Type'] = 'application/json'; body = JSON.stringify(data || {});}
  if (key) headers['Idempotency-Key'] = key;
  const response = await fetch('/web-api/v1/features/' + path, {method, headers, body, credentials: 'same-origin', signal});
  const result = await response.json();
  if (!response.ok || result.ok === false) {
    const error = new Error(response.status === 401 ? 'Войди в VELIA, чтобы продолжить.' :
      response.status === 503 ? 'Этот раздел сейчас недоступен. Повтори позже.' :
      response.status === 402 ? 'Недостаточно токенов на балансе.' : 'Не удалось выполнить действие: ' + (result.error || response.status));
    error.code = result.error; throw error;
  }
  return result;
}
export function researchReportView(item) {
  const report=item?.report || {};
  const citations=Array.isArray(report.evidence?.citations)?report.evidence.citations:[];
  return {goal:report.mission?.goal || '',summary:report.conclusion?.summary || '',
    limitations:report.limitations || [],questions:report.open_questions || [],
    boundary:report.conclusion?.boundary || '',
    findings:Array.isArray(report.conclusion?.claim_findings)?report.conclusion.claim_findings:[],
    assessments:(Array.isArray(report.evidence?.assessments)?report.evidence.assessments:[]).filter(a=>citations.some(c=>c.source_id===a.source_id)).map(a=>({...a,source:citations.find(c=>c.source_id===a.source_id)?.title || a.source_id})),
    sources:citations.map(source=>{let url=null;try{const parsed=new URL(source.url);if(['https:','http:'].includes(parsed.protocol)&&!parsed.username&&!parsed.password)url=parsed.href;}catch{}return {title:source.title || source.doi || 'Source',year:source.published_year || '',url};})};
}
export function medicalDocumentPrompt(mode){
 const tasks={summary:'Объясни содержание медицинского заключения простым языком. Отдели написанное врачом от своих пояснений.',compare:'Сравни прикреплённые медицинские заключения по датам. Не сравнивай показатели с разными единицами или условиями как одинаковые. Если даты, единицы или референсные диапазоны отсутствуют, укажи это.',visit:'Подготовь краткое резюме прикреплённых заключений и вопросы для обсуждения с врачом.'};
 if(!tasks[mode])throw new Error('Unknown medical document task');
 return tasks[mode]+' Используй только данные файлов, не придумывай показатели. Приводи название файла и страницу, только если она известна. Отметь неразборчивые места и недостаток данных. Не ставь окончательный диагноз и не назначай лечение. Ответь на языке пользователя.';
}
export function medicalUploadFormat(file){
  if(!file || !Number.isFinite(file.size) || file.size<=0)throw new Error('Выбери непустой файл исследования.');
  if(/\.zip$/i.test(file.name))return 'dicom_zip';
  if(/\.nii\.gz$/i.test(file.name))return 'nifti_gz';
  if(/\.nii$/i.test(file.name))return 'nifti';
  throw new Error('Поддерживаются DICOM ZIP, .nii и .nii.gz. Фото и PDF здесь не анализируются.');
}
export function medicalFindings(result){
  if(result?.score_semantics!=='model_score_not_calibrated_probability')return [];
  return (Array.isArray(result.findings)?result.findings:[]).filter(x=>typeof x.score==='number'&&Number.isFinite(x.score)&&x.score>=0&&x.score<=1).map(x=>({organ:String(x.organ || ''),finding:String(x.finding || ''),score:x.score.toFixed(3)}));
}
function renderMedicalResult(item){
  const box=node('section',undefined,'medical-result');
  box.append(node('h3','Результат анализа'),node('p','Оценка модели не является вероятностью заболевания или подтверждённым диагнозом. Результат требует проверки врачом.','feature-notice'));
  const findings=medicalFindings(item.result);
  if(!findings.length){box.append(node('p',item.status==='completed'?'Нет находок для отображения. Это не подтверждает отсутствие заболевания.':'Результат пока не готов. Обнови статус после завершения обработки.'));return box;}
  const table=node('table');const head=node('tr');for(const label of ['Орган','Находка','Оценка модели'])head.append(node('th',label));table.append(head);
  for(const finding of findings){const row=node('tr');for(const value of [finding.organ,finding.finding,finding.score])row.append(node('td',value));table.append(row);}box.append(table);return box;
}
export function researchReportJSON(item){return JSON.stringify(item?.report || {},null,2);}
function renderResearchReport(item) {
  const view=researchReportView(item),box=node('article',undefined,'feature-card research-report');
  const ru=(document.documentElement.lang || navigator.language || '').startsWith('ru');
  box.append(node('h3',view.goal || (ru?'Отчёт исследования':'Research report')));
  for(const [title,value] of [[ru?'Вывод':'Conclusion',view.summary],[ru?'Ограничения':'Limitations',view.limitations],[ru?'Открытые вопросы':'Open questions',view.questions],[ru?'Границы выводов':'Evidence boundary',view.boundary]]){box.append(node('h4',title),display(value));}
  if(view.findings.length)box.append(node('h4',ru?'Выводы реестра утверждений':'Claim ledger findings'),display(view.findings));
  if(view.assessments.length){box.append(node('h4',ru?'Оценка источников':'Source assessment'));for(const assessment of view.assessments){const entry=node('div');entry.append(node('strong',assessment.source),node('p',assessment.notes));box.append(entry);}}
  const download=button(ru?'Скачать отчёт · JSON':'Download report · JSON',()=>{const url=URL.createObjectURL(new Blob([researchReportJSON(item)],{type:'application/json;charset=utf-8'}));const link=node('a');link.href=url;link.download='VELIA-research-report.json';box.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});box.append(download);
  box.append(node('h4',ru?'Источники':'Sources'));
  if(!view.sources.length)box.append(node('p',ru?'Источники не приложены. Выводы требуют проверки.':'No sources attached. Conclusions need verification.'));
  for(const source of view.sources){const row=node('p');row.append(node('span',source.title+(source.year?' · '+source.year:'')));if(source.url){const link=node('a',ru?' Открыть источник':' Open source');link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';row.append(link);}box.append(row);}
  box.append(node('small',ru?'Список источников сам по себе не подтверждает каждый вывод.':'A source list alone does not verify every conclusion.'));
  return box;
}
const node = (tag, text, cls) => {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n;};
const button = (text, action) => {const b = node('button', text); b.type = 'button'; b.onclick = action; return b;};
const labels = {items:'Результаты',notes:'Описание',completed:'Выполнено',start:'Начало',end:'Окончание',goal:'Цель', title:'Название', name:'Имя', description:'Описание', instructions:'Инструкции', audience:'Аудитория', style:'Стиль', constraints:'Ограничения', status:'Статус', domain:'Направление', content:'Содержание', summary:'Результат', conclusion:'Вывод', text:'Текст', created_at:'Создано', updated_at:'Обновлено', preferred_name:'Как обращаться', about_me:'О себе', credits:'Токены', user_messages:'Сообщения сегодня', user_cost_usd:'Расход сегодня, $', enabled:'Включено', available:'Доступно', revision:'Версия', query:'Поисковый запрос', kind:'Тип', prompt:'Описание', duration_seconds:'Длительность, сек.', error_code:'Ошибка', rationale:'Обоснование'};
const statuses={draft:'Черновик',planned:'Запланировано',paused:'Приостановлено',active:'Активно',completed:'Завершено',failed:'Ошибка',pending:'Ожидает',running:'Выполняется',queued:'В очереди',ready:'Готово',awaiting_approval:'Ожидает подтверждения',cancelled:'Отменено'};
const descriptions = {work:'Команда агентов, задания и управление рабочим бюджетом.',studio:'Создавай изображения, видео и музыку в одном месте.',projects:'Собирай идеи, задачи и материалы вокруг одной цели.',research:'От вопроса к источникам, выводам и отчёту.',medical:'Загружай исследования и следи за результатами анализа.',agents:'Настраивай помощников под свои задачи.',autopilot:'Задачи по расписанию, результаты и разработка в одном месте.',profile:'Помоги Велии лучше понимать тебя.',plugins:'Выбирай инструменты, которые нужны в работе.',balance:'Следи за доступными токенами и использованием.',voice:'Настрой язык и голос для разговора с Велией.'};
const hiddenKeys = new Set(['id','user_id','project_id','session_id','generation_id','client_request_id','signature','provider','model','input_sha256','worker_status']);
function display(value, depth = 0) {
  const box = node('div', undefined, 'feature-detail');
  if (depth > 5 || value == null) return box;
  if (typeof value !== 'object') {box.textContent = typeof value === 'boolean' ? value ? 'Да' : 'Нет' : String(value); return box;}
  for (const [k, v] of Object.entries(value)) {
    if (hiddenKeys.has(k) || (k.endsWith('_url') && k!=='source_url') || k.endsWith('_hash') || v == null || v === '') continue;
    const row = node('div', undefined, 'feature-field');
    if (!Array.isArray(value)) row.append(node('strong', labels[k] || k.replaceAll('_',' ')));
    if(['url','source_url'].includes(k) && typeof v === 'string'){try{const u=new URL(v);if(['https:','http:'].includes(u.protocol)&&!u.username&&!u.password){const a=node('a',v);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';row.append(a);box.append(row);continue;}}catch{}}
    if (typeof v === 'string' && v.length > 100) {const t = node('div'); t.innerHTML = renderMarkdown(v); row.append(t);}
    else row.append(display(v, depth + 1));
    box.append(row);
  }
  return box;
}

export function setupFeatures({signedIn, openAuth, toast, isBusy, voiceSettings, openImage, openConversation, prepareMedicalChat}) {
  const root = document.getElementById('feature-view'), nav = document.getElementById('feature-nav');
  let epoch = 0, section = null, controller = null;
  const close = () => {epoch++; controller?.abort(); root.replaceChildren(); root.hidden = true; section = null; nav.querySelectorAll('button').forEach(b=>b.removeAttribute('aria-current')); document.getElementById('conversation-scroll').hidden = false; document.querySelector('.composer-area').hidden = false;};
  const call = (p, o = {}) => featureRequest(p, {...o, signal: controller?.signal});
  const run = async (b, fn) => {const ticket = epoch; b.disabled = true; try {await fn();} catch (e) {if (e.name !== 'AbortError' && ticket === epoch) toast(e.message);} finally {if (ticket === epoch) b.disabled = false;}};
  function form(fields, label, submit, initial = {}) {
    const f = node('form', undefined, 'feature-form'), controls = {};
    for (const spec of fields) {
      const [key, title, type = 'text', options] = spec, l = node('label', undefined, type === 'checkbox' ? 'feature-check' : 'feature-input');
      const caption = node('span',title); l.append(caption);
      const input = node(type === 'textarea' ? 'textarea' : ['select','multi'].includes(type) ? 'select' : 'input');
      if(type === 'multi') input.multiple = true;
      if (!['textarea','select','multi'].includes(type)) input.type = type;
      if (options) for (const [value, text] of options) {const o = node('option', text); o.value = value; input.append(o);}
      if (type === 'checkbox') input.checked = !!initial[key]; else if(initial[key] !== undefined || !['select','multi'].includes(type)) input.value = initial[key] ?? '';
      input.name = key; input.maxLength = type === 'textarea' ? 8000 : 200; input.required = !['checkbox','file','multi'].includes(type) && !['description','about_me','audience','style','constraints','lyrics','notes'].includes(key);
      if (type === 'file') input.accept = '.zip,.nii,.gz';
      controls[key] = input; if(type==='checkbox') l.prepend(input); else l.append(input); if(type==='multi') l.append(node('small','На компьютере удерживай Ctrl или ⌘, чтобы выбрать несколько.', 'field-hint')); f.append(l);
    }
    const b = node('button', label, 'feature-primary'); b.type = 'submit'; f.append(b);
    f.onsubmit = e => {e.preventDefault(); run(b, async () => {
      const data = Object.fromEntries(Object.entries(controls).map(([k,c]) => [k, c.multiple ? [...c.selectedOptions].map(o=>o.value) : c.type === 'checkbox' ? c.checked : c.type === 'file' ? c.files[0] : c.type === 'number' ? Number(c.value) : c.value]));
      await submit(data); toast('Сохранено');
    });}; return f;
  }
  function actions(parent, items) {
    const bar = node('div', undefined, 'feature-actions');
    for (const [title, fn] of items) {const b = button(title, () => run(b, fn)); bar.append(b);} parent.append(bar);
  }
  async function collection(path, key, target, detail, offset = 0) {
    const ticket = epoch, result = await call(path + (path.includes('?') ? '&' : '?') + 'offset=' + offset);
    if (ticket !== epoch) return;
    if (!offset) target.replaceChildren();
    const values = result[key] || [];
    if (!values.length && !offset) target.append(node('div', 'Здесь появятся твои материалы. Создай первый с помощью формы выше.', 'feature-empty'));
    for (const item of values) {
      item.id ||= item.mission_id || item.task_id || item.run_id;
      const card = node('article', undefined, 'feature-card');
      const title = item.passport?.title || item.title || item.name || item.goal || item.instruction || 'Открыть';
      card.append(node('h3', title));
      if (item.status) card.append(node('small', statuses[item.status] || item.status));
      if (detail) actions(card, [['Открыть', () => detail(item, card)]]); else card.append(display(item));
      target.append(card);
    }
    if (result.next_offset != null) {const more = button('Показать ещё', () => run(more, async () => {more.remove(); await collection(path,key,target,detail,result.next_offset);})); target.append(more);}
  }
  async function open(name, draft = {}) {
    if (isBusy()) {toast('Дождись завершения ответа.'); return;}
    if (!signedIn() && name !== 'voice') {openAuth(); return;}
    close(); section = name; controller = new AbortController(); const ticket = epoch;
    root.hidden = false; document.getElementById('conversation-scroll').hidden = true; document.querySelector('.composer-area').hidden = true;
    document.getElementById('sidebar').classList.remove('open'); document.getElementById('scrim').hidden = true; document.getElementById('menu').setAttribute('aria-expanded','false'); nav.querySelectorAll('button').forEach(b=>{if(b.dataset.section===name)b.setAttribute('aria-current','page');});
    const head = node('div', undefined, 'feature-header'), heading=node('div'); heading.append(node('span','ВОЗМОЖНОСТИ VELIA','feature-eyebrow'),node('h1', SECTIONS.find(s=>s[0]===name)?.[1] || 'VELIA'),node('p',descriptions[name])); head.append(heading, button('К диалогу', close)); root.append(head);
    const body = node('div',undefined,'feature-body'); root.append(body); const loading=node('p','Загрузка…','feature-empty'); loading.setAttribute('role','status'); body.append(loading);
    try {
      if (name === 'voice') {body.replaceChildren(); voiceSettings(body); return;}
      if (name === 'profile') {
        const r = await call('profile'); if (ticket !== epoch) return; body.replaceChildren(form([['preferred_name','Как обращаться'],['about_me','О себе','textarea']], 'Сохранить', d=>call('profile',{method:'PATCH',data:d}), r.profile));
      } else if (name === 'plugins') {
        const r = await call('plugins'); if (ticket !== epoch) return; body.replaceChildren();
        const names = {weather:'Погода',web_search:'Поиск в интернете',research:'Исследования',image_generation:'Изображения',file_analyst:'Анализ файлов',deepalpha_markets:'DeepAlpha'};
        for (const [key,v] of Object.entries(r.plugins || {})) {const l=node('label',names[key] || key,'feature-toggle'), c=node('input'); c.type='checkbox'; c.checked=v.enabled; c.disabled=!v.available; c.onchange=()=>run(c,async()=>{try{await call('plugins',{method:'PATCH',data:{plugins:{[key]:c.checked}}});}catch(e){c.checked=!c.checked;throw e;}}); l.append(c); if(!v.available) l.append(node('small','Сейчас недоступно')); body.append(l);
          if(!v.available && ['research','image_generation'].includes(key)){
            const destination=key==='research'?'research':'studio';
            const ru=(document.documentElement.lang || navigator.language || '').startsWith('ru');
            body.append(node('small',ru?'Этот переключатель относится к плагину чата. Доступность отдельного раздела проверяется отдельно.':'This switch controls a chat plugin. The dedicated section has separate availability.'));
            body.append(button(ru?(destination==='research'?'Открыть исследования':'Открыть Studio'):(destination==='research'?'Open Research':'Open Studio'),()=>open(destination)));
          }
        }
        const overview=node('div',undefined,'feature-card');
        const ru=(document.documentElement.lang || navigator.language || '').startsWith('ru');
        const check=button(ru?'Проверить доступность разделов':'Check service availability',()=>run(check,async()=>{
          const response=await fetch('/web-api/v1/platform/status',{credentials:'same-origin',signal:controller.signal});
          if(!response.ok)throw new Error(ru?'Не удалось проверить доступность':'Availability check failed');
          const result=await response.json();if(ticket!==epoch)return;
          overview.replaceChildren(node('p',ru?'Ответ сервиса не подтверждает выполнение задания.':'A service response does not verify task execution.'));
          const domains=ru?{plugins:'Инструменты',research:'Исследования',media:'Медиа',tools:'Действия',specialists:'Помощники',software:'Разработка'}:{plugins:'Tools',research:'Research',media:'Media',tools:'Actions',specialists:'Assistants',software:'Software'};
          const states=ru?{partial:'Работает частично',responding:'Сервис отвечает',disabled:'Отключено',unavailable:'Недоступно',timeout:'Нет ответа вовремя',access_denied:'Нет доступа',not_exposed:'Не подключено'}:{partial:'Partially available',responding:'Service responding',disabled:'Disabled',unavailable:'Unavailable',timeout:'Timed out',access_denied:'Access denied',not_exposed:'Not exposed'};
          const missingNames=ru?{worker_enabled:'Исполнение фоновых задач отключено',coding_enabled:'Разработка кода отключена',write_enabled:'Изменение кода отключено',worker_ready:'Исполнитель не готов',weather_unavailable:'Погода недоступна',web_search_unavailable:'Поиск недоступен',research_unavailable:'Исследовательский плагин не подключён',image_generation_unavailable:'Плагин изображений не подключён',file_analyst_unavailable:'Анализ файлов недоступен',deepalpha_markets_unavailable:'DeepAlpha не подключён'}:{worker_enabled:'Background execution disabled',coding_enabled:'Coding disabled',write_enabled:'Code changes disabled',worker_ready:'Executor not ready',weather_unavailable:'Weather unavailable',web_search_unavailable:'Search unavailable',research_unavailable:'Research plugin not connected',image_generation_unavailable:'Image plugin not connected',file_analyst_unavailable:'File analysis unavailable',deepalpha_markets_unavailable:'DeepAlpha not connected'};
          for(const item of result.observations || []){
            overview.append(node('p',(domains[item.domain] || item.domain)+': '+(states[item.state] || item.state)));
            for(const reason of item.missing_prerequisites || [])if(missingNames[reason])overview.append(node('small',missingNames[reason]));
          }
        }));body.append(check,overview);
      } else if (name === 'balance') {
        const results = await Promise.all([call('economy/me'),call('usage')]); if(ticket!==epoch)return; body.replaceChildren(...results.map((r,i)=>{const card=node('article',undefined,'feature-card');card.append(node('h3',i===0?'Твой баланс':'Использование'),display(r.account||r.usage||r));return card;}));
      } else if (name === 'studio') await studio(body);
      else if (name === 'research') {
        const capability=await call('research/status');if(ticket!==epoch)return;
        const researchState=capability.research || {};
        const intro=node('div',undefined,'research-intro');intro.append(node('small','VELIA RESEARCH'),node('h2','Разберись в теме. Опирайся на источники.'),node('p','Собери материалы, изучи противоречия и сохрани выводы в одном исследовании.'));
        body.replaceChildren(intro,form([['goal','Что исследовать','textarea']], 'Создать исследование', async d=>{await call('research/missions',{method:'POST',data:d,key:crypto.randomUUID()}); await refresh();}));
        body.classList.add('research-body');
        const list=node('div',undefined,'research-list');body.append(node('h2','Мои исследования'),list);
        const detail=async (m,card)=>{
          const r=await call('research/missions/'+m.id);card.classList.add('research-mission');card.replaceChildren(node('h3',m.goal),node('span',statuses[r.mission.status] || r.mission.status,'research-badge'));const technical=node('details',undefined,'research-details');technical.append(node('summary','Параметры исследования'),display(r.mission));card.append(technical);
          const out=node('div');card.append(out);
          const progress=node('div',undefined,'feature-card');card.append(progress);
          const refreshRuns=async()=>{
            const data=await call(`research/missions/${m.id}/runs`);if(ticket!==epoch)return;
            progress.replaceChildren(node('h3','Ход исследования'));
            for(const run of data.runs || []){
              const row=node('div',undefined,'research-run');const meter=node('progress');meter.max=Math.max(1,Number(run.max_iterations)||1);meter.value=Math.min(meter.max,Math.max(0,Number(run.completed_iterations)||0));meter.setAttribute('aria-label','Завершённые итерации исследования');row.append(meter);row.append(node('p',(statuses[run.status] || run.status)+' · '+(run.completed_iterations || 0)+' / '+run.max_iterations+' этапов'));
              if(run.stop_reason){const reasons={user_cancelled:'Отменено пользователем',attempt_limit:'Исчерпан лимит повторных попыток',mission_not_active:'Исследование закрыто',evidence_sufficient:'Собрано достаточно доказательств',no_open_questions:'Открытых вопросов больше нет',no_new_evidence:'Новые доказательства не найдены',iteration_limit:'Достигнут лимит итераций',stage_failed:'Этап завершился ошибкой',internal_error:'Ошибка исполнения'};row.append(node('p','Причина остановки: '+(reasons[run.stop_reason] || 'Исполнение остановлено')));}
              if(['queued','running'].includes(run.status))actions(row,[['Остановить исследование',async()=>{await call(`research/runs/${run.id}/cancel`,{method:'POST',data:{}});await refreshRuns();}]]);
              progress.append(row);
            }
            start.disabled=researchState.director?.enabled!==true || ['blocked','cancelled','completed'].includes(r.mission.status) || (data.runs || []).some(item=>['queued','running'].includes(item.status));
            if(!(data.runs || []).length)progress.append(node('p','Исследование ещё не запущено.'));
          };
          actions(card,[['Обновить прогресс',refreshRuns]]);
          const depth=node('select');depth.setAttribute('aria-label','Глубина исследования');for(const [value,title] of [[1,'Краткое · 1 итерация'],[2,'Стандартное · 2 итерации'],[3,'Углублённое · 3 итерации']]){if(value>(researchState.director?.max_iterations || 2))continue;const option=node('option',title);option.value=String(value);depth.append(option);}depth.value=String(Math.min(2,researchState.director?.max_iterations || 2));card.append(depth);
          const start=button('Начать исследование',async()=>{await run(start,async()=>{await call(`research/missions/${m.id}/runs`,{method:'POST',data:{max_iterations:Number(depth.value)}});});await refreshRuns().catch(e=>{progress.replaceChildren(node('p',e.message));start.disabled=true;});});
          start.classList.add('feature-primary');start.disabled=researchState.director?.enabled!==true;card.append(start);
          card.append(node('p','Запуск включает поиск источников и анализ с выбранным лимитом итераций. Больше итераций может увеличить время и стоимость, но не гарантирует качество. Настроенный внешний AI-провайдер может списать оплату. Автоматический отчёт зависит от конфигурации; сохранённые отчёты доступны ниже.'));
          if(researchState.director?.enabled!==true)card.append(node('p','Автоматическое исследование отключено. Доступные ручные действия — ниже.'));
          await refreshRuns().catch(e=>{progress.replaceChildren(node('p',e.message));start.disabled=true;});
          const advanced=node('details',undefined,'research-details');advanced.append(node('summary','Источники и ручное управление'));card.append(advanced);
          actions(advanced,[['Найти литературу',async()=>{const r=await call(`research/missions/${m.id}/literature`,{method:'POST',data:{query:m.goal,max_results:12}});out.replaceChildren(display(r.literature));}],['Синтез',async()=>{const r=await call(`research/missions/${m.id}/synthesize`,{method:'POST',data:{max_sources:12}});out.replaceChildren(display(r.synthesis));}],['Сформировать отчёт',async()=>{const r=await call(`research/missions/${m.id}/reports`,{method:'POST'});out.replaceChildren(renderResearchReport(r.report));}],...['sources','evidence-claims','claims','reports','scientific-alerts'].map((k,i)=>[['Источники','Доказательства','Утверждения','Отчёты','Уведомления'][i],async()=>{const r=await call(`research/missions/${m.id}/${k}`);if(k==='reports')out.replaceChildren(...(r.reports || []).map(renderResearchReport));else out.replaceChildren(display(r[k==='scientific-alerts'?'alerts':k.replaceAll('-','_')]));}])]);
          const saved=await call(`research/missions/${m.id}/reports`).catch(()=>null);if(ticket!==epoch)return;if(saved?.reports?.length)out.replaceChildren(...saved.reports.map(renderResearchReport));else out.append(node('p','Готовый отчёт появится здесь. Сохранённые отчёты доступны при повторном открытии.','feature-help'));
        };
        const refresh=()=>collection('research/missions','missions',list,detail); await refresh();
      } else if (name === 'projects') {
        const fields=['title','goal','audience','style','constraints'].map(k=>[k,labels[k],k==='title'?'text':'textarea']);
        body.replaceChildren(form(fields,'Создать проект',async d=>{await call('projects',{method:'POST',data:{passport:d},key:crypto.randomUUID()});await refresh();}));
        const list=node('div');body.append(list);
        const detail=async(p,card)=>{const r=await call('projects/'+p.id);card.replaceChildren(form(fields,'Сохранить изменения',async d=>{await call('projects/'+p.id,{method:'PATCH',data:{passport:d,expected_revision:r.project.revision}});await detail(p,card);},r.project.passport));const resources=node('div');card.append(form([['kind','Тип','select',[['chat','Диалог'],['deepalpha','DeepAlpha'],['image','Изображения'],['video','Видео'],['music','Музыка']]],['title','Название'],['query','Запрос','textarea']],'Добавить ресурс',async d=>{await call('project-resources',{method:'POST',data:{...d,project_id:p.id},key:crypto.randomUUID()});await collection('project-resources?project_id='+encodeURIComponent(p.id),'resources',resources);}));card.append(resources);await collection('project-resources?project_id='+encodeURIComponent(p.id),'resources',resources);};
        const refresh=()=>collection('projects','projects',list,detail);await refresh();
      } else if (name === 'work') {
        body.replaceChildren(); await mountWork(body,{signal:controller.signal,toast});
      } else if (name === 'agents') {
        const capabilities = await call('agents/capabilities'); if(ticket!==epoch)return;
        body.replaceChildren(form([['name','Название'],['description','Описание','textarea'],['instructions','Инструкции','textarea'],['capability_ids','Возможности агента','multi',(capabilities.capabilities||[]).map(c=>[c.id,c.name])]], 'Создать агента',async d=>{await call('agents',{method:'POST',data:{...d,can_create_chats:true}});await refresh();}));
        const list=node('div');body.append(list);
        const refresh=()=>collection('agents','agents',list,async(a,card)=>{card.append(display(a));actions(card,[['Новый диалог',async()=>{const r=await call(`agents/${a.id}/conversations`,{method:'POST',data:{title:a.name}});close();await openConversation(r.conversation);}],['Удалить',async()=>{if(!confirm('Удалить этого агента?'))return;await call('agents/'+a.id,{method:'DELETE'});await refresh();}]]);});await refresh();
      } else if (name === 'medical') {
        const medicalCapability=await call('medical/status');if(ticket!==epoch)return;const medicalReady=medicalCapability.medical?.radar?.available===true;
        body.classList.add('medical-body');const intro=node('section',undefined,'medical-intro');intro.append(node('small','VELIA MEDICAL'),node('h2','Исследования здоровья в одном месте'),node('p','Сейчас доступен анализ КТ брюшной полости с контрастом. МРТ, УЗИ, фотографии и анализы крови этим инструментом не поддерживаются.'),node('p',medicalReady?'Анализатор настроен. Готовность GPU проверяется при обращении.':'Анализатор сейчас недоступен. Загружать КТ пока нельзя.','feature-notice'));body.replaceChildren(intro);
        const documents=node('section',undefined,'feature-card');documents.append(node('h3','Заключения и подготовка к врачу'),node('p','Открой отдельный диалог VELIA Flash и прикрепи PDF или текст заключения. До 4 файлов, по 15 МБ. Для сканов извлечение текста может быть недоступно. Файлы отправляются только после твоего нажатия «Отправить».'));
        if(prepareMedicalChat)actions(documents,[['Объяснить заключение',async()=>{await prepareMedicalChat(medicalDocumentPrompt('summary'));close();}],['Сравнить по датам',async()=>{await prepareMedicalChat(medicalDocumentPrompt('compare'));close();}],['Подготовиться к врачу',async()=>{await prepareMedicalChat(medicalDocumentPrompt('visit'));close();}]]);body.append(documents);
        body.append(form([['title','Название исследования'],['contrast_enhanced_confirmed','Подтверждаю наличие контраста','checkbox'],['abdomen_confirmed','Подтверждаю КТ брюшной полости','checkbox']], 'Создать исследование',async d=>{await call('medical/cases',{method:'POST',data:{...d,modality:'ct',study_kind:'contrast_abdomen'}});await refresh();}));
        const list=node('div');body.append(list);
        const detail=async(c,card)=>{const r=await call('medical/cases/'+c.id);card.classList.add('medical-case');card.replaceChildren(node('h3',c.title));const stateNames={draft:'Ожидает загрузки',uploading:'Загрузка файла',queued:'В очереди на анализ',running:'Анализ выполняется',completed:'Анализ завершён',failed:'Обработка не удалась'};card.append(node('p',stateNames[r.case.status] || statuses[r.case.status] || 'Статус неизвестен','research-badge'),renderMedicalResult(r.case));if(r.case.error)card.append(node('p','Ошибка обработки. Обнови статус; если ошибка сохраняется, повторную загрузку согласуй после проверки доступности сервиса.','feature-notice'));
          const uploadForm=form([['study','DICOM ZIP или NIfTI','file']],'Загрузить КТ',async d=>{const file=d.study;const format=medicalUploadFormat(file);const start=await call(`medical/cases/${c.id}/study/begin`,{method:'POST',data:{format,total_bytes:file.size}});let offset=start.upload.received_bytes,index=start.upload.next_chunk;const progress=node('p');progress.setAttribute('role','status');card.append(progress);progress.textContent='Загрузка: '+Math.round(100*offset/file.size)+'%';while(offset<file.size){const chunk=file.slice(offset,offset+8*1024*1024);const bytes=await chunk.arrayBuffer();const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');await call(`medical/cases/${c.id}/study/chunks/${index}`,{method:'PUT',file:chunk,digest:hash});offset+=chunk.size;index++;progress.textContent='Загрузка: '+Math.round(100*offset/file.size)+'%';}await call(`medical/cases/${c.id}/study/complete`,{method:'POST'});await detail(c,card);});uploadForm.querySelector('button').disabled=!medicalReady || ['queued','running','completed'].includes(r.case.status);card.append(uploadForm);
          actions(card,[['Обновить результат',()=>detail(c,card)],['Создать исследование по результату',async()=>{const v=await call(`medical/cases/${c.id}/research`,{method:'POST'});card.append(display(v.case));}]]);
        };
        const refresh=()=>collection('medical/cases','cases',list,detail);await refresh();
      } else if (name === 'autopilot') {
        await autopilot(body,draft);
      }
    } catch(e) {if(e.name!=='AbortError'&&ticket===epoch)body.replaceChildren(node('p',e.message),button('Повторить',()=>open(name)));}
  }
  async function development(body) {
    const ticket=epoch;
    const readiness=await call('developer/autopilot/status');
    body.replaceChildren(node('p',readiness.worker_ready ? 'Исполнитель разработки готов. Миссии создаются на паузе; результат — черновик PR.' : 'Исполнитель разработки отключён или не готов. Активация миссии не гарантирует запуск.', 'feature-notice'));
        const projects=await call('developer/projects'); if(ticket!==epoch)return;
        body.append(form([['project_id','Проект','select',(projects.projects||[]).map(p=>[p.id,p.name||p.title||p.repository_full_name||p.id])],['name','Название'],['allowed_paths','Разрешённые пути, по одному на строку','textarea'],['max_steps','Максимум шагов','number'],['max_files','Максимум файлов','number']],'Создать миссию',async d=>{await call('developer/autopilot/missions',{method:'POST',data:{...d,allowed_paths:d.allowed_paths.split('\n').map(x=>x.trim()).filter(Boolean),blocked_paths:[]}});await refresh();},{max_steps:5,max_files:5}));
        const list=node('div');body.append(list);
        const refresh=()=>collection('developer/autopilot/missions','missions',list,async(m,card)=>{card.append(display(m));actions(card,[['Активировать',async()=>{if(!readiness.worker_ready){toast('Исполнитель разработки не готов.');return;}await call(`developer/autopilot/missions/${m.id}/activate`,{method:'POST'});await refresh();}],['Приостановить',async()=>{await call(`developer/autopilot/missions/${m.id}/pause`,{method:'POST'});await refresh();}]]);card.append(form([['instruction','Задача','textarea']],'Добавить задачу',async d=>{await call(`developer/autopilot/missions/${m.id}/tasks`,{method:'POST',data:{...d,priority:0,client_request_id:crypto.randomUUID()}});}));const tasks=node('div'),runs=node('div');card.append(tasks,runs);actions(card,[['История запусков',()=>collection(`developer/autopilot/missions/${m.id}/runs`,'runs',runs,async(r,rc)=>{const out=node('div');rc.append(out);actions(rc,['ci','reviews','merge-policy'].map((k,i)=>[['Проверки CI','Ревью','Условия мержа'][i],async()=>{const v=await call(`developer/autopilot/runs/${r.id}/${k}`);out.replaceChildren(display(v));}]));})]]);await collection(`developer/autopilot/missions/${m.id}/tasks`,'tasks',tasks,async(t,tc)=>{tc.append(display(t));actions(tc,[['Отменить задачу',async()=>{await call(`developer/autopilot/tasks/${t.id}/cancel`,{method:'POST'});tc.append(node('p','Задача отменена'));}]]);});});await refresh();
    if(!readiness.worker_ready)body.querySelectorAll('button').forEach(b=>{if(b.textContent==='Активировать')b.disabled=true;});
  }
  async function autopilot(body,draft) {
    body.replaceChildren();const tabs=node('div',undefined,'feature-tabs'),panel=node('div');body.append(tabs,panel);
    let version=0;
    async function select(key) {
      const current=++version,ticket=epoch;tabs.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.tab===key)));
      panel.replaceChildren(node('p','Загрузка…','feature-empty'));
      // Each tab has its own detached mount, so late responses cannot overwrite another tab.
      const mount=node('div');
      try {if(key==='development')await development(mount);else await schedules(mount,draft);}
      catch(e){if(e.name==='AbortError')return;mount.replaceChildren(node('p',e.message,'feature-notice'),button('Повторить',()=>select(key)));}
      if(current===version&&ticket===epoch)panel.replaceChildren(mount);
    }
    for(const [key,title] of [['schedules','Задачи и расписания'],['development','Разработка']]){const b=button(title,()=>select(key));b.dataset.tab=key;tabs.append(b);}
    await select('schedules');
  }
  async function schedules(body,draft) {
    const [status,core]=await Promise.all([call('agent/schedules/status'),call('agent/status')]);
    const ready=!!status.enabled&&!!core.enabled;
    body.append(node('p',ready ? 'Планировщик включён. Новые расписания создаются на паузе — включи их, когда всё проверишь.' : 'Фоновые задачи сейчас отключены на сервере. Расписание нельзя запустить, пока исполнитель не включён.', 'feature-notice'));
    body.append(node('p','Выбери конкретное действие. Этот раздел пока не запускает произвольные браузерные поручения и не отправляет уведомления.', 'feature-help'));
    if(!ready)return;
    const available=new Set((core.tools||[]).filter(t=>t.enabled!==false).map(t=>t.name));
    const templates=[['velia.tasks.list','Обзор моих задач'],['velia.tasks.create_draft','Создать черновик задачи']].filter(([key])=>available.has(key));
    if(available.has('google.calendar.events.list')){const calendar=await call('agent/connectors/google-calendar/status');if(calendar.connected)templates.push(['google.calendar.events.list','События календаря на ближайшие 7 дней']);}
    const list=node('div');
    if(templates.length) {
      const f=form([['instruction','Название задачи'],['template','Что выполнять','select',templates],['notes','Описание черновика','textarea'],['kind','Повторять','select',[['daily','Ежедневно'],['weekly','Еженедельно'],['interval_hours','Через интервал']]],['time','Время','time'],['weekday','День недели','select',['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'].map((t,i)=>[String(i),t])],['hours','Интервал, часов','number'],['timezone','Часовой пояс']], 'Создать расписание',async d=>{
        const schedule=d.kind==='interval_hours'?{kind:d.kind,hours:d.hours}:{kind:d.kind,time:d.time,...(d.kind==='weekly'?{weekdays:[Number(d.weekday)]}:{})};
        const argumentsFor=d.template==='velia.tasks.create_draft'?{title:d.instruction,notes:d.notes}:d.template==='velia.tasks.list'?{limit:50}:{max_results:20};
        await call('agent/schedules',{method:'POST',data:{instruction:d.instruction,timezone:d.timezone,schedule,actions:[{tool_name:d.template,arguments:argumentsFor}]}});await refresh();
      },{instruction:draft.instruction||'',template:draft.instruction?'velia.tasks.create_draft':templates[0][0],time:'09:00',hours:24,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC'});
      const kind=f.elements.kind,template=f.elements.template;
      const update=()=>{for(const [key,visible] of [['time',kind.value!=='interval_hours'],['weekday',kind.value==='weekly'],['hours',kind.value==='interval_hours'],['notes',template.value==='velia.tasks.create_draft']]){const input=f.elements[key];input.closest('label').hidden=!visible;input.disabled=!visible;} };
      f.elements.hours.min=1;f.elements.hours.max=168;kind.onchange=update;template.onchange=update;update();body.append(f);
    } else body.append(node('p','Нет доступных действий для расписания.','feature-empty'));
    body.append(list);
    const when=value=>{if(!value)return '—';const raw=String(value);const date=new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(raw)?raw:raw+'Z');return Number.isNaN(date.valueOf())?'—':date.toLocaleString();};
    const detail=async(s,card)=>{
      const r=await call('agent/schedules/'+s.schedule_id),item=r.schedule;card.replaceChildren(node('h3',item.instruction),node('small',item.enabled?'Включено':'На паузе'),node('p',(item.schedule.kind==='interval_hours'?'Каждые '+item.schedule.hours+' ч.':item.schedule.kind==='weekly'?'Еженедельно в '+item.schedule.time:'Ежедневно в '+item.schedule.time)+' · '+item.timezone),node('p','Следующий запуск: '+when(item.next_run_at)),node('p','Последний запуск: '+when(item.last_run_at)));
      if(item.error_code)card.append(node('p','Последний запуск завершился ошибкой: '+item.error_code,'feature-notice'));
      actions(card,[[item.enabled?'Приостановить':'Включить',async()=>{await call(`agent/schedules/${s.schedule_id}/${item.enabled?'disable':'enable'}`,{method:'POST'});await refresh();}],['Обновить',()=>detail(s,card)],['Удалить расписание',async()=>{if(!confirm('Удалить расписание?'))return;await call('agent/schedules/'+s.schedule_id,{method:'DELETE'});await refresh();}]]);
      if(item.last_job_id){const out=node('div');card.append(out);await jobView(item.last_job_id,out);}
    };
    async function jobView(id,out){const r=await call('agent/jobs/'+id),job=r.job;out.replaceChildren(node('h4','Последний результат'),node('p',statuses[job.status]||job.status));
      for(const a of job.actions||[]){const item=node('div',undefined,'feature-card');item.append(display(a.result||a.arguments));if(a.error_code)item.append(node('p',a.error_code,'feature-notice'));if(a.status==='awaiting_approval')actions(item,[['Подтвердить действие',async()=>{await call(`agent/jobs/${id}/actions/${a.action_id}/approve`,{method:'POST'});await jobView(id,out);}],['Отклонить',async()=>{await call(`agent/jobs/${id}/actions/${a.action_id}/reject`,{method:'POST'});await jobView(id,out);}]]);out.append(item);}
      if(job.status==='planned')actions(out,[['Выполнить подтверждённое',async()=>{await call(`agent/jobs/${id}/run`,{method:'POST'});await jobView(id,out);}]]);
    }
    async function refresh(){const r=await call('agent/schedules');list.replaceChildren();if(!r.schedules?.length)list.append(node('div','Расписаний пока нет. Создай первое с помощью формы выше.','feature-empty'));for(const item of r.schedules||[]){const card=node('article',undefined,'feature-card');list.append(card);await detail(item,card);}}
    await refresh();
  }
  async function studio(body) {
    const ticket=epoch,status=await call('studio/status');if(ticket!==epoch)return;if(status.enabled===false)throw new Error('Studio сейчас отключена.');
    body.replaceChildren();let list=node('div');const options=node('div'),messages=node('div');let currentSession=null,mode='image',references=[],generationKey=null;
    const modes=node('div',undefined,'feature-tabs'); modes.setAttribute('aria-label','Тип материала');body.append(modes,options,list,messages);
    const refresh=()=>collection('studio/sessions?mode='+mode,'sessions',list,select);
    async function select(s){currentSession=s;references=[];generationKey=null;await turns();}
    async function turns(){if(!currentSession)return;const selectedId=currentSession.id;const r=await call(`studio/sessions/${selectedId}/messages`);if(ticket!==epoch||currentSession?.id!==selectedId)return;messages.replaceChildren(node('h2',currentSession.title||'Studio'));for(const m of r.messages||[]){const card=node('article',undefined,'feature-card');card.append(node('p',m.content||m.status||''));const media=m.generation?.media,path=mediaPath(media?.content_url);if(path){const el=node(m.generation.type==='image'?'img':m.generation.type==='video'?'video':'audio');el.src=path;el.controls=true;el.alt=media.prompt||'Результат Studio';card.append(el);if(m.generation.type==='image'){const view=button('Открыть изображение',()=>openImage(path,media.prompt||'Изображение Studio'));card.append(view);}if(m.generation.type==='image'&&media.id)actions(card,[['Оживить изображение',async()=>{mode='video';configure();const r=await call('studio/sessions',{method:'POST',data:{mode:'video',title:'Видео из изображения'}});currentSession=r.session;references=[media.id];toast('Изображение добавлено. Опиши движение и нажми «Создать».');}]]);const a=node('a','Скачать');a.href=path;a.download='VELIA-'+media.id;card.append(a);}if(m.generation?.progress_percent)card.append(node('p',m.generation.progress_percent+'%'));messages.append(card);}}
    function configure(){modes.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===mode)));const nextList=node('div');list.replaceWith(nextList);list=nextList;options.replaceChildren();currentSession=null;references=[];messages.replaceChildren();
      const cap=status[mode]||{};const fields=[['prompt','Что создать','textarea']];
      if(mode==='image'){const providers=(cap.providers||[]).filter(p=>p.enabled!==false).map(p=>[p.id||p.provider,p.display_name||p.label||p.name||p.id]);fields.push(['image_provider','Модель изображения','select',providers.length?providers:[['velia_image','Velia Image']]],['transparent_background','Прозрачный фон','checkbox']);}
      if(mode==='video'||mode==='music')fields.push(['duration_seconds','Длительность','select',(cap.duration_options_seconds||[mode==='video'?5:30]).map(v=>[String(v),v+' сек.'])]);
      if(mode==='music')fields.push(['lyrics_mode','Режим музыки','select',[['auto','Авто'],['custom','Свой текст'],['instrumental','Без вокала']]],['lyrics','Текст песни','textarea']);
      options.append(form(fields,'Создать',async d=>{if(!currentSession){const r=await call('studio/sessions',{method:'POST',data:{mode,title:d.prompt.slice(0,80)}});currentSession=r.session;}generationKey ||= crypto.randomUUID();await call(`studio/sessions/${currentSession.id}/generate`,{method:'POST',data:{...d,duration_seconds:Number(d.duration_seconds||5),reference_asset_ids:references,idempotency_key:generationKey},key:generationKey});generationKey=null;await turns();await refresh();}));
      if(mode!=='music'){const l=node('label','Добавить референсы JPEG / PNG / WebP','feature-upload'),upload=node('input');upload.type='file';upload.accept='image/jpeg,image/png,image/webp';upload.multiple=mode==='image';l.append(upload);options.append(l);upload.onchange=()=>run(upload,async()=>{if(!currentSession){const r=await call('studio/sessions',{method:'POST',data:{mode,title:'Studio'}});currentSession=r.session;}for(const f of upload.files){const r=await call(`studio/sessions/${currentSession.id}/assets`,{method:'POST',file:f});references.push(r.asset.id);}toast('Референсов: '+references.length);});}
      actions(options,[['Новая сессия',async()=>{currentSession=null;references=[];generationKey=null;messages.replaceChildren();}],['Обновить результат',turns],['Обновить историю',refresh]]);
    }
    for(const [key,title] of [['image','Изображения'],['video','Видео'],['music','Музыка']]){const b=button(title,()=>run(b,async()=>{mode=key;configure();await refresh();}));b.dataset.mode=key;if(status[key]?.enabled===false)b.disabled=true;modes.append(b);}
    configure();await refresh();
  }
  const symbols=['<path d="m12 3 9 9-9 9-9-9z"/>','<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 10h18M8 5V3"/>','<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>','<path d="M12 4v16M4 12h16"/>','<rect x="4" y="7" width="16" height="14" rx="4"/><path d="M12 3v4M8 12h1m6 0h1M8 17h8"/>','<path d="m12 3 8 4v6c0 4-8 8-8 8s-8-4-8-8V7zM9 12l2 2 4-4"/>','<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>','<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 12h8m-4-4v8"/>','<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>','<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11v1a7 7 0 0 0 14 0v-1M12 19v3"/>'];
  for (const [index,[key,title]] of SECTIONS.entries()) {if(index===0||index===6)nav.append(node('span',index===0?'РАБОЧЕЕ ПРОСТРАНСТВО':'НАСТРОЙКИ','feature-nav-heading')); const b=button(title,()=>open(key)); b.dataset.section=key;const icon=node('span',undefined,'feature-nav-icon');icon.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'+symbols[index]+'</svg>';icon.setAttribute('aria-hidden','true');b.prepend(icon);nav.append(b);}
  return {close,open};
}
