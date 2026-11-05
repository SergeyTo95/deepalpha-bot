/**
 * Agentix Business Builder: a local, no-network project-intake preview.
 * It prepares a user-reviewed specification for the existing Velia chat.
 * No API keys, hosting, GitHub writes, deployment or image generation.
 */
export const BUILDER_FEATURES = Object.freeze([
  Object.freeze({id:'catalog', title:'Каталог товаров и услуг', detail:'Карточки, категории, поиск'}),
  Object.freeze({id:'orders', title:'Заказы и клиенты', detail:'Заявки, статусы, история'}),
  Object.freeze({id:'inventory', title:'Склад и остатки', detail:'Наличие и предупреждения'}),
  Object.freeze({id:'reports', title:'Финансы и отчёты', detail:'Выручка, расходы и проверяемые сводки'}),
  Object.freeze({id:'assistant', title:'ИИ-помощник', detail:'Консультации и разрешённые задачи'}),
  Object.freeze({id:'delivery', title:'Доставка и расписание', detail:'Адреса, интервалы, уведомления'})
]);
export const BUILDER_STYLES = Object.freeze([
  Object.freeze({id:'auto', title:'Пусть Velia предложит'}),
  Object.freeze({id:'elegant', title:'Элегантный'}),
  Object.freeze({id:'modern', title:'Современный'}),
  Object.freeze({id:'warm', title:'Тёплый и уютный'})
]);
export const BUILDER_DELIVERY = Object.freeze([
  Object.freeze({id:'recommend', title:'Velia подскажет лучший вариант'}),
  Object.freeze({id:'github', title:'В мой GitHub'}),
  Object.freeze({id:'hosting', title:'На мой хостинг'}),
  Object.freeze({id:'zip', title:'Получить проект ZIP-архивом'})
]);
export const AGENTIX_BUSINESS_BRIEF_PREFIX='VELIA / AGENTIX BUSINESS BUILDER — запрос на проект';
export const isAgentixBusinessBrief=content=>typeof content==='string'&&content.trimStart().startsWith(AGENTIX_BUSINESS_BRIEF_PREFIX);
/**
 * Restore already-selected, enumerated Business Builder options during a
 * continuation. The model does not otherwise know the final state of an
 * interrupted assistant message; never quote uncontrolled free-form content.
 */
export function businessContinuationReminder(messages=[]) {
  if (!Array.isArray(messages)) return '';
  const original=[...messages].reverse().find(m=>m?.role==='user'&&isAgentixBusinessBrief(m.content))?.content;
  if (!original) return '';
  const field=(label)=>String(original.match(new RegExp('^- '+label+': (.+)'Хочу цветочный интернет-магазин с красивым дизайном, доставкой, приёмом заказов, учётом товаров, расходов и выручки. ИИ должен помогать общаться с клиентами и готовить отчёты.';

const element = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
};
const makeButton = (title, action, className='agentix-action') => {
  const button=element('button',title,className);
  button.type='button';
  button.addEventListener('click',action);
  return button;
};
const findTitle = (list,id) => list.find(x=>x.id===id)?.title || list[0].title;
const safeIdea = value => String(value || '').trim().slice(0,3000);

export function buildBusinessBrief({idea='',style='auto',delivery='recommend',features=[]}={}) {
  const title=safeIdea(idea);
  if (title.length<12) throw new Error('Опиши идею хотя бы одним предложением.');
  const selected=BUILDER_FEATURES.filter(x=>features.includes(x.id));
  const styleName=findTitle(BUILDER_STYLES,style);
  const deliveryName=findTitle(BUILDER_DELIVERY,delivery);
  return [
    AGENTIX_BUSINESS_BRIEF_PREFIX,
    '',
    'Идея владельца:',
    title,
    '',
    'Предпочтения:',
    '- Дизайн: '+styleName,
    '- Передача проекта: '+deliveryName,
    '- Желаемые функции: '+(selected.length?selected.map(x=>x.title).join('; '):'Определи вместе со мной'),
    '',
    'Построй проект по шагам для человека без знаний программирования. Не проси техническое задание или выбор технологий.',
    'Выбранный дизайн и отмеченные функции уже известны. Не задавай вопросы о них повторно.',
    'Далее уточняй важное: география работы, оплата, доставка, необходимые подключения. РОВНО ОДИН вопрос за сообщение, без списка вопросов.',
    'После уточнений покажи краткий план и отдельно согласуй внешний вид, функции, стоимость инфраструктуры и ограничения. Не выдумывай фиксированные цены и не требуй бюджета разработки заранее.',
    'После явного разрешения используй доступные инструменты разработки, тестирования и GitHub. Не выдавай план за готовый сайт и не обещай недоступные инструменты.',
    'Никогда не проси прислать API-ключи, пароли или токены в чат. Подключение Agentix — только через безопасную авторизацию или серверные секреты.',
    'Способ получения — только пожелание владельца. Готовый ZIP, работающий GitHub-репозиторий, развёртывание и ключи Agentix нельзя обещать как выполненные или гарантированно доступные. Сначала проверь реально подключённые инструменты, после разработки проверь результат. Не создавай платные ресурсы и не публикуй проект без разрешения.',
    'Заказы, финансовые записи и действия ИИ должны проверяться; значимые операции требуют разрешения владельца.',
    'Генерация изображений сейчас недоступна. Используй CSS, SVG и загруженные изображения; не запускай GPU-серверы.',
    'Изменения опубликованного сайта и выпуск новых версий требуют согласия.',
    '',
    'Пока мы согласуем требования, не обещай будущую выдачу ZIP или подключение сервисов как уже обеспеченные; прямо различай проектирование и фактическое создание. Не начинай внешние действия до утверждения.',
    'СЕЙЧАС СТРОГО ПЕРВЫЙ ШАГ: ответь НЕ БОЛЕЕ 500 символов обычным языком, без заголовков, списка функций, стоимости и этапов. Подтверди понимание идеи и задай РОВНО ОДИН вопрос: в каком городе и стране будет работать бизнес? Не перечисляй другие вопросы.',
  ].join('\n');
}


export const BUSINESS_PROJECT_MARKER='Agentix Business Builder';
export function businessPassportFromDraft({idea='',style='auto',delivery='recommend',features=[]}={}) {
  const goal=safeIdea(idea);
  if(goal.length<12)throw new Error('Расскажи подробнее об идее.');
  if(goal.length>1200)throw new Error('Для сохранения сократи описание до 1200 символов.');
  const title='Agentix · '+goal.split(/[\n.!?]/,1)[0].trim().slice(0,100);
  const selected=BUILDER_FEATURES.filter(x=>features.includes(x.id)).map(x=>x.title).join(' | ');
  return {title:title.slice(0,120),goal,audience:BUSINESS_PROJECT_MARKER,
    style:findTitle(BUILDER_STYLES,style),
    constraints:'Функции: '+selected+'; Получение: '+findTitle(BUILDER_DELIVERY,delivery)};
}
export function businessDraftFromProject(project){
  const passport=project?.passport;
  if(!passport||passport.audience!==BUSINESS_PROJECT_MARKER||typeof passport.goal!=='string')return null;
  const constraints=String(passport.constraints||'');
  const features=BUILDER_FEATURES.filter(x=>constraints.includes(x.title)).map(x=>x.id);
  const style=BUILDER_STYLES.find(x=>x.title===passport.style)?.id||'auto';
  const delivery=BUILDER_DELIVERY.find(x=>constraints.includes('; Получение: '+x.title))?.id||'recommend';
  return {idea:passport.goal,style,delivery,features};
}

export function mountBusinessBuilder(root,{onHandoff,onSave,onList,onStatus,isAuthenticated=()=>false}={}) {
  const draft={
    idea:'',
    style:'auto',
    delivery:'recommend',
    features:new Set(['catalog','orders','reports','assistant']),
    project:null,
    savedSignature:''
  };
  let step=0;
  const shell=element('section',null,'agentix-builder');
  shell.id='agentix-builder';
  root.append(shell);
  const savedProjects=element('section',null,'agentix-saved-projects');
  root.append(savedProjects);
  const statusBlock=element('div',null,'agentix-builder-runtime');
  root.append(statusBlock);
  const message=element('p',null,'agentix-builder-feedback');
  message.setAttribute('role','status');
  message.setAttribute('aria-live','polite');
  const steps=['Идея','Настройки','План'];
  const error=txt=>{message.textContent=txt;};
  const group=(title,description)=>{
    const block=element('div',null,'agentix-builder-group');
    block.append(element('h4',title),element('p',description,'agentix-muted'));
    return block;
  };
  const actions=()=>{
    const row=element('div',null,'agentix-builder-actions');
    return row;
  };
  const addStylePreview=()=>{
    const preview=element('div',null,'agentix-shop-preview');
    preview.dataset.style=draft.style;
    const nav=element('div',null,'agentix-shop-nav');
    nav.append(element('strong','FLEUR'),element('span','Каталог    Доставка    Контакты'));
    const hero=element('div',null,'agentix-shop-hero');
    const text=element('div');
    text.append(element('small','FLOWERS & MOMENTS'),element('h4','Цветы для особенных моментов'),element('p','Собери букет и подари немного счастья.'));
    const flower=element('div','✿','agentix-shop-flower');
    flower.setAttribute('aria-hidden','true');
    hero.append(text,flower);preview.append(nav,hero);
    preview.append(element('small','Иллюстрация оформления на CSS — не готовый сайт и не сгенерированное изображение.','agentix-muted'));
    return preview;
  };

  async function refreshProjects(){
    savedProjects.replaceChildren();
    if(!isAuthenticated()||typeof onList!=='function'){
      savedProjects.append(element('p','Войди в Velia, чтобы сохранять идеи и возвращаться к ним. Без входа можно подготовить проект и перейти в чат.','agentix-muted'));
      return;
    }
    savedProjects.append(element('h3','Мои проекты'),element('p','Сохранённые идеи доступны только в твоём аккаунте Velia.','agentix-muted'));
    const state=element('p','Загружаю…','agentix-muted');savedProjects.append(state);
    try{
      const raw=await onList();
      const projects=(Array.isArray(raw)?raw:[]).filter(p=>businessDraftFromProject(p)).slice(0,30);
      state.remove?.();
      if(!projects.length){
        savedProjects.append(element('p','Сохранённых проектов пока нет. Создай первый выше.','agentix-muted'));
        return;
      }
      const list=element('div',null,'agentix-saved-grid');
      for(const project of projects){
        const item=element('article',null,'agentix-saved-item');
        item.append(element('strong',project.passport.title),element('p',project.passport.goal,'agentix-muted'));
        const resume=makeButton('Продолжить проект',()=>{
          const restored=businessDraftFromProject(project);
          if(!restored)return;
          draft.idea=restored.idea;draft.style=restored.style;draft.delivery=restored.delivery;
          draft.features=new Set(restored.features);draft.project=project;
          draft.savedSignature=JSON.stringify(businessPassportFromDraft({...draft,features:[...draft.features]}));
          step=1;render();
          shell.scrollIntoView?.({behavior:'smooth',block:'start'});
        },'agentix-action agentix-action-secondary');
        item.append(resume);list.append(item);
      }
      savedProjects.append(list);
    }catch(e){
      state.textContent='Не удалось загрузить проекты. Можно продолжить без сохранения.';
    }
  }
  let saveInFlight=null;
  function saveDraft(){
    if(saveInFlight)return saveInFlight;
    if(!isAuthenticated()||typeof onSave!=='function')return Promise.resolve(null);
    saveInFlight=(async()=>{
      const passport=businessPassportFromDraft({...draft,features:[...draft.features]});
      const signature=JSON.stringify(passport);
      if(draft.project&&draft.savedSignature===signature)return draft.project;
      const project=await onSave(passport,draft.project);
      draft.project=project;draft.savedSignature=signature;
      await refreshProjects();
      return project;
    })().finally(()=>{saveInFlight=null;});
    return saveInFlight;
  }
  async function checkDeveloperReadiness(){
    if(!isAuthenticated()||typeof onStatus!=='function')return;
    try{
      const result=await onStatus();
      if(result?.ok===true){
        const available=Boolean(result.enabled&&result.developer_enabled&&result.team_enabled);
        statusBlock.textContent=available
          ? 'Инструменты разработки обнаружены. Перед запуском Velia проверит подключение GitHub и необходимые разрешения.'
          : 'Для автоматической разработки потребуется включить соответствующие возможности Velia и подключить GitHub.';
      }
    }catch{statusBlock.textContent='Статус разработки пока недоступен. Твой проект можно подготовить и сохранить.';}
  }
  const render=()=>{
    shell.replaceChildren();
    const heading=element('div',null,'agentix-builder-heading');
    heading.append(element('span','НОВОЕ · BUSINESS BUILDER','agentix-overline'),element('h2','Опиши бизнес — Velia подготовит проект'),element('p','Без программирования. Ты выбираешь результат, Velia занимается технической частью.','agentix-muted'));
    shell.append(heading);
    const progress=element('ol',null,'agentix-builder-progress');
    for(let i=0;i<steps.length;i++){
      const item=element('li',(i+1)+'. '+steps[i]);
      item.className=i===step?'current':i<step?'done':'';
      if(i===step)item.setAttribute('aria-current','step');
      progress.append(item);
    }
    shell.append(progress);
    const content=element('div',null,'agentix-builder-content');
    if(step===0){
      content.append(element('h3','Что ты хочешь создать?'),element('p','Напиши как другу — техническое задание не требуется. Можно описать магазин, сервис, приложение или другую идею.','agentix-muted'));
      const label=element('label','Идея проекта','agentix-builder-label');
      label.htmlFor='agentix-business-idea';
      const field=element('textarea',null,'agentix-builder-idea');
      field.id='agentix-business-idea';field.maxLength=1200;field.rows=5;
      field.placeholder='Например: хочу интернет-магазин цветов, который принимает заказы и помогает вести учёт…';
      field.value=draft.idea;
      field.addEventListener('input',()=>{draft.idea=field.value;message.textContent='';});
      content.append(label,field);
      content.append(makeButton('Попробовать пример с цветочным магазином',()=>{draft.idea=BUILDER_EXAMPLE;field.value=draft.idea;field.focus();message.textContent='';},'agentix-builder-example'));
      const row=actions();
      row.append(makeButton('Продолжить',()=>{
        if(safeIdea(draft.idea).length<12){error('Расскажи чуть подробнее: что именно ты хочешь создать?');field.focus();return;}
        step=1;render();
      },'agentix-action agentix-action-primary'));
      content.append(row);
    } else if(step===1){
      content.append(element('h3','Каким будет твой проект?'),element('p','Выбери то, что уже знаешь. Всё остальное Velia уточнит по ходу разговора.','agentix-muted'));
      const design=group('Внешний вид','Можно доверить выбор дизайна Velia.');
      const styles=element('div',null,'agentix-builder-chips');
      for(const option of BUILDER_STYLES){
        const b=makeButton(option.title,()=>{draft.style=option.id;render();},'agentix-builder-chip');
        b.setAttribute('aria-pressed',String(draft.style===option.id));
        styles.append(b);
      }
      design.append(styles,addStylePreview());content.append(design);
      const features=group('Что должен уметь продукт','Это пожелания, а не обязательные требования. Velia предложит минимальный набор.');
      const checklist=element('div',null,'agentix-builder-features');
      for(const item of BUILDER_FEATURES){
        const label=element('label',null,'agentix-builder-feature');
        const checkbox=element('input');checkbox.type='checkbox';checkbox.checked=draft.features.has(item.id);
        checkbox.addEventListener('change',()=>{if(checkbox.checked)draft.features.add(item.id);else draft.features.delete(item.id);});
        const copy=element('span');
        copy.append(element('strong',item.title),element('small',item.detail));
        label.append(checkbox,copy);checklist.append(label);
      }
      features.append(checklist);content.append(features);
      const delivery=group('Как получить готовый проект','Не обязательно разбираться в GitHub или серверах.');
      const sel=element('select',null,'agentix-builder-select');
      sel.setAttribute('aria-label','Способ передачи готового проекта');
      for(const item of BUILDER_DELIVERY){
        const o=element('option',item.title);o.value=item.id;sel.append(o);
      }
      sel.value=draft.delivery;
      sel.addEventListener('change',()=>{draft.delivery=sel.value;});
      delivery.append(sel);content.append(delivery);
      const row=actions();
      row.append(makeButton('Назад',()=>{step=0;render();},'agentix-action agentix-action-secondary'));
      row.append(makeButton('Посмотреть план',()=>{step=2;render();},'agentix-action agentix-action-primary'));
      content.append(row);
    } else {
      const brief=buildBusinessBrief({...draft,features:[...draft.features]});
      content.append(element('h3','Твой запрос для Velia готов'),element('p','Это предварительное описание. Velia уточнит важные детали и согласует план перед разработкой.','agentix-muted'));
      const outline=element('div',null,'agentix-builder-summary');
      outline.append(element('strong','Идея'),element('p',safeIdea(draft.idea)));
      outline.append(element('strong','Дизайн'),element('p',findTitle(BUILDER_STYLES,draft.style)));
      outline.append(element('strong','Функции'),element('p',BUILDER_FEATURES.filter(x=>draft.features.has(x.id)).map(x=>x.title).join(' · ')||'Velia предложит'));
      outline.append(element('strong','Получение'),element('p',findTitle(BUILDER_DELIVERY,draft.delivery)));
      content.append(outline);
      const details=element('details',null,'agentix-builder-details');
      details.append(element('summary','Посмотреть полный запрос'));
      const pre=element('pre',brief,'agentix-builder-brief');details.append(pre);content.append(details);
      const row=actions();
      row.append(makeButton('Изменить',()=>{step=1;render();},'agentix-action agentix-action-secondary'));
      row.append(makeButton('Скопировать план',async()=>{
        try{
          await navigator.clipboard.writeText(brief);
          message.textContent='Запрос скопирован. Его можно вставить в чат Velia.';
        }catch{
          details.open=true;message.textContent='Копирование недоступно. Открой полный запрос и выдели текст вручную.';
        }
      },'agentix-action agentix-action-secondary'));
      if(isAuthenticated()&&typeof onSave==='function')row.append(makeButton('Сохранить на потом',async()=>{
        message.textContent='Сохраняю проект…';
        try{await saveDraft();message.textContent='Проект сохранён в твоём аккаунте Velia.';}
        catch(ex){message.textContent=ex?.message||'Не удалось сохранить проект. Попробуй ещё раз.';}
      },'agentix-action agentix-action-secondary'));
      const handoff=makeButton('Продолжить с Velia',()=>{
        if(typeof onHandoff!=='function'){error('Диалог сейчас недоступен. Скопируй запрос и открой чат Velia.');return;}
        const openChat=(project)=>{
          const suffix=project?.id?'\n\nИдентификатор сохранённого проекта Velia: '+project.id:'';
          return onHandoff(brief+suffix);
        };
        if(!isAuthenticated()||typeof onSave!=='function'){
          try{openChat(null);}catch(ex){error(ex?.message||'Не удалось открыть диалог.');}
          return;
        }
        handoff.disabled=true;message.textContent='Сохраняю проект и открываю диалог…';
        saveDraft().then(project=>openChat(project)).catch(ex=>{
          error(ex?.message||'Не удалось сохранить проект. Можно скопировать запрос.');
        }).finally(()=>{handoff.disabled=false;});
      },'agentix-action agentix-action-primary');
      row.append(handoff);
      content.append(row);
      content.append(element('p','Кнопка откроет черновик сообщения в чате: отправку, создание репозитория, платежи и публикацию ты подтверждаешь отдельно.','agentix-muted'));
    }
    content.append(message);shell.append(content);
    shell.append(element('p','Генерация изображений и видеосерверы не используются. Ключи Agentix, хостинг и публикация здесь не активируются.','agentix-builder-footnote'));
  };
  render();
  void refreshProjects();
  void checkDeveloperReadiness();
  return {getStep:()=>step};
}
,'m'))?.[1]||'').trim();
  const style=BUILDER_STYLES.find(x=>x.title===field('Дизайн'));
  const delivery=BUILDER_DELIVERY.find(x=>x.title===field('Передача проекта'));
  const options=field('Желаемые функции');
  const selected=BUILDER_FEATURES.filter(x=>options.split('; ').includes(x.title));
  const lines=[
    'КОНТЕКСТ AGENTIX BUSINESS BUILDER — уже известные решения владельца:',
    ...(style?['- Стиль: '+style.title+' (уже выбран; не спрашивай заново).']:[]),
    ...(selected.length?['- Функции: '+selected.map(x=>x.title).join(', ')+' (уже выбраны).']:[]),
    ...(delivery?['- Пожелание о выдаче: '+delivery.title+' (не обещание доступности).']:[]),
    'Не возвращайся к уже выбранному дизайну или функциям. Следующий недостающий вопрос — один за сообщение.',
    'Проект пока обсуждается. Не утверждай, что полный ZIP создан, GitHub подключён, магазин опубликован или Agentix API работает без фактического результата соответствующего инструмента.',
    'Называй стоимость инфраструктуры только при проверенных тарифах; не придумывай фиксированную цену.'
  ];
  return '\n\n'+lines.join('\n');
}
export const BUILDER_EXAMPLE = 'Хочу цветочный интернет-магазин с красивым дизайном, доставкой, приёмом заказов, учётом товаров, расходов и выручки. ИИ должен помогать общаться с клиентами и готовить отчёты.';

const element = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
};
const makeButton = (title, action, className='agentix-action') => {
  const button=element('button',title,className);
  button.type='button';
  button.addEventListener('click',action);
  return button;
};
const findTitle = (list,id) => list.find(x=>x.id===id)?.title || list[0].title;
const safeIdea = value => String(value || '').trim().slice(0,3000);

export function buildBusinessBrief({idea='',style='auto',delivery='recommend',features=[]}={}) {
  const title=safeIdea(idea);
  if (title.length<12) throw new Error('Опиши идею хотя бы одним предложением.');
  const selected=BUILDER_FEATURES.filter(x=>features.includes(x.id));
  const styleName=findTitle(BUILDER_STYLES,style);
  const deliveryName=findTitle(BUILDER_DELIVERY,delivery);
  return [
    AGENTIX_BUSINESS_BRIEF_PREFIX,
    '',
    'Идея владельца:',
    title,
    '',
    'Предпочтения:',
    '- Дизайн: '+styleName,
    '- Передача проекта: '+deliveryName,
    '- Желаемые функции: '+(selected.length?selected.map(x=>x.title).join('; '):'Определи вместе со мной'),
    '',
    'Построй проект по шагам для человека без знаний программирования. Не проси техническое задание или выбор технологий.',
    'Выбранный дизайн и отмеченные функции уже известны. Не задавай вопросы о них повторно.',
    'Далее уточняй важное: география работы, оплата, доставка, необходимые подключения. РОВНО ОДИН вопрос за сообщение, без списка вопросов.',
    'После уточнений покажи краткий план и отдельно согласуй внешний вид, функции, стоимость инфраструктуры и ограничения. Не выдумывай фиксированные цены и не требуй бюджета разработки заранее.',
    'После явного разрешения используй доступные инструменты разработки, тестирования и GitHub. Не выдавай план за готовый сайт и не обещай недоступные инструменты.',
    'Никогда не проси прислать API-ключи, пароли или токены в чат. Подключение Agentix — только через безопасную авторизацию или серверные секреты.',
    'Получение проекта: GitHub, существующий хостинг или полный ZIP с инструкцией, если доступно. Не создавай платные ресурсы и не публикуй проект без отдельного разрешения.',
    'Заказы, финансовые записи и действия ИИ должны проверяться; значимые операции требуют разрешения владельца.',
    'Генерация изображений сейчас недоступна. Используй CSS, SVG и загруженные изображения; не запускай GPU-серверы.',
    'Изменения опубликованного сайта и выпуск новых версий требуют согласия.',
    '',
    'Сначала согласуй со мной план. Не начинай внешние действия до утверждения.',
    'СЕЙЧАС СТРОГО ПЕРВЫЙ ШАГ: ответь НЕ БОЛЕЕ 500 символов обычным языком, без заголовков, списка функций, стоимости и этапов. Подтверди понимание идеи и задай РОВНО ОДИН вопрос: в каком городе и стране будет работать бизнес? Не перечисляй другие вопросы.',
  ].join('\n');
}


export const BUSINESS_PROJECT_MARKER='Agentix Business Builder';
export function businessPassportFromDraft({idea='',style='auto',delivery='recommend',features=[]}={}) {
  const goal=safeIdea(idea);
  if(goal.length<12)throw new Error('Расскажи подробнее об идее.');
  if(goal.length>1200)throw new Error('Для сохранения сократи описание до 1200 символов.');
  const title='Agentix · '+goal.split(/[\n.!?]/,1)[0].trim().slice(0,100);
  const selected=BUILDER_FEATURES.filter(x=>features.includes(x.id)).map(x=>x.title).join(' | ');
  return {title:title.slice(0,120),goal,audience:BUSINESS_PROJECT_MARKER,
    style:findTitle(BUILDER_STYLES,style),
    constraints:'Функции: '+selected+'; Получение: '+findTitle(BUILDER_DELIVERY,delivery)};
}
export function businessDraftFromProject(project){
  const passport=project?.passport;
  if(!passport||passport.audience!==BUSINESS_PROJECT_MARKER||typeof passport.goal!=='string')return null;
  const constraints=String(passport.constraints||'');
  const features=BUILDER_FEATURES.filter(x=>constraints.includes(x.title)).map(x=>x.id);
  const style=BUILDER_STYLES.find(x=>x.title===passport.style)?.id||'auto';
  const delivery=BUILDER_DELIVERY.find(x=>constraints.includes('; Получение: '+x.title))?.id||'recommend';
  return {idea:passport.goal,style,delivery,features};
}

export function mountBusinessBuilder(root,{onHandoff,onSave,onList,onStatus,isAuthenticated=()=>false}={}) {
  const draft={
    idea:'',
    style:'auto',
    delivery:'recommend',
    features:new Set(['catalog','orders','reports','assistant']),
    project:null,
    savedSignature:''
  };
  let step=0;
  const shell=element('section',null,'agentix-builder');
  shell.id='agentix-builder';
  root.append(shell);
  const savedProjects=element('section',null,'agentix-saved-projects');
  root.append(savedProjects);
  const statusBlock=element('div',null,'agentix-builder-runtime');
  root.append(statusBlock);
  const message=element('p',null,'agentix-builder-feedback');
  message.setAttribute('role','status');
  message.setAttribute('aria-live','polite');
  const steps=['Идея','Настройки','План'];
  const error=txt=>{message.textContent=txt;};
  const group=(title,description)=>{
    const block=element('div',null,'agentix-builder-group');
    block.append(element('h4',title),element('p',description,'agentix-muted'));
    return block;
  };
  const actions=()=>{
    const row=element('div',null,'agentix-builder-actions');
    return row;
  };
  const addStylePreview=()=>{
    const preview=element('div',null,'agentix-shop-preview');
    preview.dataset.style=draft.style;
    const nav=element('div',null,'agentix-shop-nav');
    nav.append(element('strong','FLEUR'),element('span','Каталог    Доставка    Контакты'));
    const hero=element('div',null,'agentix-shop-hero');
    const text=element('div');
    text.append(element('small','FLOWERS & MOMENTS'),element('h4','Цветы для особенных моментов'),element('p','Собери букет и подари немного счастья.'));
    const flower=element('div','✿','agentix-shop-flower');
    flower.setAttribute('aria-hidden','true');
    hero.append(text,flower);preview.append(nav,hero);
    preview.append(element('small','Иллюстрация оформления на CSS — не готовый сайт и не сгенерированное изображение.','agentix-muted'));
    return preview;
  };

  async function refreshProjects(){
    savedProjects.replaceChildren();
    if(!isAuthenticated()||typeof onList!=='function'){
      savedProjects.append(element('p','Войди в Velia, чтобы сохранять идеи и возвращаться к ним. Без входа можно подготовить проект и перейти в чат.','agentix-muted'));
      return;
    }
    savedProjects.append(element('h3','Мои проекты'),element('p','Сохранённые идеи доступны только в твоём аккаунте Velia.','agentix-muted'));
    const state=element('p','Загружаю…','agentix-muted');savedProjects.append(state);
    try{
      const raw=await onList();
      const projects=(Array.isArray(raw)?raw:[]).filter(p=>businessDraftFromProject(p)).slice(0,30);
      state.remove?.();
      if(!projects.length){
        savedProjects.append(element('p','Сохранённых проектов пока нет. Создай первый выше.','agentix-muted'));
        return;
      }
      const list=element('div',null,'agentix-saved-grid');
      for(const project of projects){
        const item=element('article',null,'agentix-saved-item');
        item.append(element('strong',project.passport.title),element('p',project.passport.goal,'agentix-muted'));
        const resume=makeButton('Продолжить проект',()=>{
          const restored=businessDraftFromProject(project);
          if(!restored)return;
          draft.idea=restored.idea;draft.style=restored.style;draft.delivery=restored.delivery;
          draft.features=new Set(restored.features);draft.project=project;
          draft.savedSignature=JSON.stringify(businessPassportFromDraft({...draft,features:[...draft.features]}));
          step=1;render();
          shell.scrollIntoView?.({behavior:'smooth',block:'start'});
        },'agentix-action agentix-action-secondary');
        item.append(resume);list.append(item);
      }
      savedProjects.append(list);
    }catch(e){
      state.textContent='Не удалось загрузить проекты. Можно продолжить без сохранения.';
    }
  }
  let saveInFlight=null;
  function saveDraft(){
    if(saveInFlight)return saveInFlight;
    if(!isAuthenticated()||typeof onSave!=='function')return Promise.resolve(null);
    saveInFlight=(async()=>{
      const passport=businessPassportFromDraft({...draft,features:[...draft.features]});
      const signature=JSON.stringify(passport);
      if(draft.project&&draft.savedSignature===signature)return draft.project;
      const project=await onSave(passport,draft.project);
      draft.project=project;draft.savedSignature=signature;
      await refreshProjects();
      return project;
    })().finally(()=>{saveInFlight=null;});
    return saveInFlight;
  }
  async function checkDeveloperReadiness(){
    if(!isAuthenticated()||typeof onStatus!=='function')return;
    try{
      const result=await onStatus();
      if(result?.ok===true){
        const available=Boolean(result.enabled&&result.developer_enabled&&result.team_enabled);
        statusBlock.textContent=available
          ? 'Инструменты разработки обнаружены. Перед запуском Velia проверит подключение GitHub и необходимые разрешения.'
          : 'Для автоматической разработки потребуется включить соответствующие возможности Velia и подключить GitHub.';
      }
    }catch{statusBlock.textContent='Статус разработки пока недоступен. Твой проект можно подготовить и сохранить.';}
  }
  const render=()=>{
    shell.replaceChildren();
    const heading=element('div',null,'agentix-builder-heading');
    heading.append(element('span','НОВОЕ · BUSINESS BUILDER','agentix-overline'),element('h2','Опиши бизнес — Velia подготовит проект'),element('p','Без программирования. Ты выбираешь результат, Velia занимается технической частью.','agentix-muted'));
    shell.append(heading);
    const progress=element('ol',null,'agentix-builder-progress');
    for(let i=0;i<steps.length;i++){
      const item=element('li',(i+1)+'. '+steps[i]);
      item.className=i===step?'current':i<step?'done':'';
      if(i===step)item.setAttribute('aria-current','step');
      progress.append(item);
    }
    shell.append(progress);
    const content=element('div',null,'agentix-builder-content');
    if(step===0){
      content.append(element('h3','Что ты хочешь создать?'),element('p','Напиши как другу — техническое задание не требуется. Можно описать магазин, сервис, приложение или другую идею.','agentix-muted'));
      const label=element('label','Идея проекта','agentix-builder-label');
      label.htmlFor='agentix-business-idea';
      const field=element('textarea',null,'agentix-builder-idea');
      field.id='agentix-business-idea';field.maxLength=1200;field.rows=5;
      field.placeholder='Например: хочу интернет-магазин цветов, который принимает заказы и помогает вести учёт…';
      field.value=draft.idea;
      field.addEventListener('input',()=>{draft.idea=field.value;message.textContent='';});
      content.append(label,field);
      content.append(makeButton('Попробовать пример с цветочным магазином',()=>{draft.idea=BUILDER_EXAMPLE;field.value=draft.idea;field.focus();message.textContent='';},'agentix-builder-example'));
      const row=actions();
      row.append(makeButton('Продолжить',()=>{
        if(safeIdea(draft.idea).length<12){error('Расскажи чуть подробнее: что именно ты хочешь создать?');field.focus();return;}
        step=1;render();
      },'agentix-action agentix-action-primary'));
      content.append(row);
    } else if(step===1){
      content.append(element('h3','Каким будет твой проект?'),element('p','Выбери то, что уже знаешь. Всё остальное Velia уточнит по ходу разговора.','agentix-muted'));
      const design=group('Внешний вид','Можно доверить выбор дизайна Velia.');
      const styles=element('div',null,'agentix-builder-chips');
      for(const option of BUILDER_STYLES){
        const b=makeButton(option.title,()=>{draft.style=option.id;render();},'agentix-builder-chip');
        b.setAttribute('aria-pressed',String(draft.style===option.id));
        styles.append(b);
      }
      design.append(styles,addStylePreview());content.append(design);
      const features=group('Что должен уметь продукт','Это пожелания, а не обязательные требования. Velia предложит минимальный набор.');
      const checklist=element('div',null,'agentix-builder-features');
      for(const item of BUILDER_FEATURES){
        const label=element('label',null,'agentix-builder-feature');
        const checkbox=element('input');checkbox.type='checkbox';checkbox.checked=draft.features.has(item.id);
        checkbox.addEventListener('change',()=>{if(checkbox.checked)draft.features.add(item.id);else draft.features.delete(item.id);});
        const copy=element('span');
        copy.append(element('strong',item.title),element('small',item.detail));
        label.append(checkbox,copy);checklist.append(label);
      }
      features.append(checklist);content.append(features);
      const delivery=group('Как получить готовый проект','Не обязательно разбираться в GitHub или серверах.');
      const sel=element('select',null,'agentix-builder-select');
      sel.setAttribute('aria-label','Способ передачи готового проекта');
      for(const item of BUILDER_DELIVERY){
        const o=element('option',item.title);o.value=item.id;sel.append(o);
      }
      sel.value=draft.delivery;
      sel.addEventListener('change',()=>{draft.delivery=sel.value;});
      delivery.append(sel);content.append(delivery);
      const row=actions();
      row.append(makeButton('Назад',()=>{step=0;render();},'agentix-action agentix-action-secondary'));
      row.append(makeButton('Посмотреть план',()=>{step=2;render();},'agentix-action agentix-action-primary'));
      content.append(row);
    } else {
      const brief=buildBusinessBrief({...draft,features:[...draft.features]});
      content.append(element('h3','Твой запрос для Velia готов'),element('p','Это предварительное описание. Velia уточнит важные детали и согласует план перед разработкой.','agentix-muted'));
      const outline=element('div',null,'agentix-builder-summary');
      outline.append(element('strong','Идея'),element('p',safeIdea(draft.idea)));
      outline.append(element('strong','Дизайн'),element('p',findTitle(BUILDER_STYLES,draft.style)));
      outline.append(element('strong','Функции'),element('p',BUILDER_FEATURES.filter(x=>draft.features.has(x.id)).map(x=>x.title).join(' · ')||'Velia предложит'));
      outline.append(element('strong','Получение'),element('p',findTitle(BUILDER_DELIVERY,draft.delivery)));
      content.append(outline);
      const details=element('details',null,'agentix-builder-details');
      details.append(element('summary','Посмотреть полный запрос'));
      const pre=element('pre',brief,'agentix-builder-brief');details.append(pre);content.append(details);
      const row=actions();
      row.append(makeButton('Изменить',()=>{step=1;render();},'agentix-action agentix-action-secondary'));
      row.append(makeButton('Скопировать план',async()=>{
        try{
          await navigator.clipboard.writeText(brief);
          message.textContent='Запрос скопирован. Его можно вставить в чат Velia.';
        }catch{
          details.open=true;message.textContent='Копирование недоступно. Открой полный запрос и выдели текст вручную.';
        }
      },'agentix-action agentix-action-secondary'));
      if(isAuthenticated()&&typeof onSave==='function')row.append(makeButton('Сохранить на потом',async()=>{
        message.textContent='Сохраняю проект…';
        try{await saveDraft();message.textContent='Проект сохранён в твоём аккаунте Velia.';}
        catch(ex){message.textContent=ex?.message||'Не удалось сохранить проект. Попробуй ещё раз.';}
      },'agentix-action agentix-action-secondary'));
      const handoff=makeButton('Продолжить с Velia',()=>{
        if(typeof onHandoff!=='function'){error('Диалог сейчас недоступен. Скопируй запрос и открой чат Velia.');return;}
        const openChat=(project)=>{
          const suffix=project?.id?'\n\nИдентификатор сохранённого проекта Velia: '+project.id:'';
          return onHandoff(brief+suffix);
        };
        if(!isAuthenticated()||typeof onSave!=='function'){
          try{openChat(null);}catch(ex){error(ex?.message||'Не удалось открыть диалог.');}
          return;
        }
        handoff.disabled=true;message.textContent='Сохраняю проект и открываю диалог…';
        saveDraft().then(project=>openChat(project)).catch(ex=>{
          error(ex?.message||'Не удалось сохранить проект. Можно скопировать запрос.');
        }).finally(()=>{handoff.disabled=false;});
      },'agentix-action agentix-action-primary');
      row.append(handoff);
      content.append(row);
      content.append(element('p','Кнопка откроет черновик сообщения в чате: отправку, создание репозитория, платежи и публикацию ты подтверждаешь отдельно.','agentix-muted'));
    }
    content.append(message);shell.append(content);
    shell.append(element('p','Генерация изображений и видеосерверы не используются. Ключи Agentix, хостинг и публикация здесь не активируются.','agentix-builder-footnote'));
  };
  render();
  void refreshProjects();
  void checkDeveloperReadiness();
  return {getStep:()=>step};
}
