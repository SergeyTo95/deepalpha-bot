import {mountBusinessBuilder} from './agentix-business-builder.mjs';
/**
 * Agentix: informational preview only. No project provisioning, API tokens,
 * remote calls, billable operations or integration activation are exposed here.
 */
export const AGENTIX_MODES = Object.freeze([
  {title:'Agentix API',tag:'Разработчикам',summary:'Единая точка интеграции интеллектуальных возможностей в серверные продукты.',
   detail:'Диалоги, работа с данными, мультимодальные сценарии, выполнение разрешённых задач и асинхронные операции через версионированные контракты.',
   points:['REST API и потоковые ответы','Изолированные проекты и ключи','Контроль расхода и журнал событий']},
  {title:'Agentix Embed',tag:'Сайтам и платформам',summary:'Готовый ассистент внутри интерфейса сайта, SaaS или пользовательского кабинета.',
   detail:'Визуальная интеграция с настройкой бренда, языков, базы знаний, поведения и доступных пользователю действий.',
   points:['Встраиваемый Web SDK','Velia или White Label','Пользовательские роли и разрешения']},
  {title:'Agentix Agents',tag:'Процессам и командам',summary:'Интеллектуальные агенты для реальных рабочих процессов.',
   detail:'Работа с разрешёнными инструментами компании, подтверждениями значимых действий и отслеживанием выполнения задач.',
   points:['Задачи, расписания и webhooks','Интеграции с бизнес-системами','Политики действий и аудит']}
]);
export const AGENTIX_LAYERS = Object.freeze([
  ['01','Интеграция','API · SDK · Embed · Webhooks'],
  ['02','Доступ','Организации · проекты · роли · права'],
  ['03','Оркестрация','Диалог · память · маршрутизация · агенты'],
  ['04','Velyon Core','Flash · Quantum · PRO · Voice · Vision'],
  ['05','Исполнение','Инструменты · знания · задачи · подтверждения'],
  ['06','Контроль','Квоты · аудит · стоимость · наблюдаемость']
]);
export const AGENTIX_DELIVERY = Object.freeze([
  ['01','Основа','Контракты доступа, изоляция организаций и проектов, план управления и безопасность.'],
  ['02','Подключение','API для диалогов и агентов, SDK, изоляция данных и тестовая среда.'],
  ['03','Инструменты','Базы знаний, действия в SaaS, разрешения, webhooks и управление сессиями.'],
  ['04','Коммерческий запуск','Учёт использования, биллинг, кабинеты, метрики, SLA и массовые интеграции.']
]);
const el=(tag,text,cls)=>{
  const n=document.createElement(tag);
  if(text!==undefined&&text!==null)n.textContent=String(text);
  if(cls)n.className=cls;
  return n;
};
function card(title,description,items=[],tag) {
  const section=el('article',null,'agentix-card');
  if(tag)section.append(el('small',tag,'agentix-kicker'));
  section.append(el('h3',title),el('p',description));
  if(items.length){const list=el('ul');for(const item of items)list.append(el('li',item));section.append(list);}
  return section;
}
export function mountAgentix(root,{onHandoff,onSave,onList,onStatus,isAuthenticated}={}) {
  root.replaceChildren();
  root.classList.add('agentix-page');
  const hero=el('section',null,'agentix-hero');
  hero.append(el('span','VELIA PLATFORM · VELYON CORE','agentix-overline'),el('h2','Один интеллект. Тысячи продуктов.'),el('p','Agentix — платформа для встраивания возможностей Velia в SaaS, бизнес-системы, приложения, сайты и цифровые устройства.'));
  const state=el('span','Архитектура · обзор возможностей','agentix-status');
  hero.append(state);root.append(hero);
  mountBusinessBuilder(root,{onHandoff,onSave,onList,onStatus,isAuthenticated});

  const intro=el('section',null,'agentix-block');
  intro.append(el('h2','Что такое Agentix'),el('p','Agentix превращает Velyon Core в технологический слой для разработчиков и компаний. Пользователи получают интеллектуального помощника непосредственно в привычном продукте, а его владелец управляет доступом, функциональностью, данными и расходами.'));
  const metrics=el('div',null,'agentix-benefits');
  for(const [title,body] of [['Для SaaS','Встраиваемый интеллект с доступом к разрешённым функциям продукта.'],['Для бизнеса','Агенты, которые помогают сотрудникам и выполняют согласованные процессы.'],['Для разработчиков','Версионированные API, SDK и предсказуемые контракты.']]){
    metrics.append(card(title,body));
  }
  intro.append(metrics);root.append(intro);

  const modes=el('section',null,'agentix-block');
  modes.append(el('h2','Три способа подключения'),el('p','Выберите формат интеграции под архитектуру вашего продукта.','agentix-muted'));
  const modeGrid=el('div',null,'agentix-grid');
  for(const mode of AGENTIX_MODES)modeGrid.append(card(mode.title,mode.summary+' '+mode.detail,mode.points,mode.tag));
  modes.append(modeGrid);root.append(modes);

  const architecture=el('section',null,'agentix-block');
  architecture.append(el('h2','Архитектура платформы'),el('p','Шесть логических слоёв отделяют пользовательские продукты от выполнения задач и управления инфраструктурой.','agentix-muted'));
  const layers=el('ol',null,'agentix-layers');
  for(const [num,title,details] of AGENTIX_LAYERS){
    const row=el('li',null,'agentix-layer');
    row.append(el('span',num,'agentix-layer-index'));
    const copy=el('div');copy.append(el('strong',title),el('span',details));row.append(copy);layers.append(row);
  }
  architecture.append(layers);root.append(architecture);

  const useCases=el('section',null,'agentix-block');
  useCases.append(el('h2','Где используется Agentix'));
  const cases=el('div',null,'agentix-grid');
  for(const [title,body] of [
    ['CRM и продажи','Подготовка сводок, анализ сделок, работа с клиентскими карточками при наличии разрешений.'],
    ['Электронная коммерция','Подбор товаров, консультации, помощь с заказами и поддержкой.'],
    ['Образование','Персональные объяснения, сценарии обучения и сопровождение учеников.'],
    ['Корпоративные системы','Поиск по знаниям, составление документов, автоматизация внутренних операций.'],
    ['Сервисы разработчиков','Помощник в интерфейсе продукта, интеграции с репозиториями, документацией и задачами.'],
    ['Устройства и робототехника','Интерфейсы взаимодействия и управляющие сценарии с независимыми ограничителями безопасности.']
  ])cases.append(card(title,body));
  useCases.append(cases);root.append(useCases);

  const safeguards=el('section',null,'agentix-block');
  safeguards.append(el('h2','Контроль, доверие, безопасность'));
  safeguards.append(card('Организация контролирует свой контур','Проектные границы и права определяют, какие данные и действия доступны каждому подключению.',[
    'Раздельные организации, проекты, среды и API-ключи',
    'Минимальные разрешения, роли, авторизация и серверная проверка каждого действия',
    'Подтверждения значимых операций и ограничение внешних эффектов',
    'Раздельный учёт запросов, квот, стоимости и аудита',
    'Политики хранения данных и настройки конфиденциальности'
  ]));
  root.append(safeguards);

  const roadmap=el('section',null,'agentix-block');
  roadmap.append(el('h2','Этапы развития'));
  const timeline=el('ol',null,'agentix-roadmap');
  for(const [num,title,description] of AGENTIX_DELIVERY){
    const item=el('li');item.append(el('span',num,'agentix-layer-index'));
    const copy=el('div');copy.append(el('strong',title),el('p',description));item.append(copy);timeline.append(item);
  }
  roadmap.append(timeline);root.append(roadmap);

  const note=el('section',null,'agentix-note');
  note.append(el('strong','Информационный раздел'),el('p','Сейчас представлен проект архитектуры Agentix. Выдача ключей, регистрация интеграций, выполнение агентских операций и коммерческие подключения пока не открыты. Раздел не отправляет внешние запросы и не списывает средства.'));
  root.append(note);
}
