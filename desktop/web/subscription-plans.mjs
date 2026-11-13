// Owner-supplied live product references, not account-bound checkout sessions.
// Publishing these references must not enable payment before server fulfillment.
export const CREEM_SUBSCRIPTIONS = Object.freeze([
  Object.freeze({code:'plus', name:'Plus', price:14.99, monthlyCredits:1200,
    productId:'prod_3YUMifrIso3U0JEvAJEl3o',
    productUrl:'https://www.creem.io/payment/prod_3YUMifrIso3U0JEvAJEl3o'}),
  Object.freeze({code:'pro', name:'Pro', price:29.99, monthlyCredits:3000,
    productId:'prod_1E2KmHCJDzxC3nMraYc1iB',
    productUrl:'https://www.creem.io/payment/prod_1E2KmHCJDzxC3nMraYc1iB'}),
  Object.freeze({code:'max', name:'Max', price:59.99, monthlyCredits:6500,
    productId:'prod_QM6adegRzqFXCFL6lyO6a',
    productUrl:'https://www.creem.io/payment/prod_QM6adegRzqFXCFL6lyO6a'}),
]);

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export function paymentDestination(value, portal=false) {
  if (typeof value !== 'string' || value.length >= 2048 || /[\x00-\x20\\]/.test(value)) throw Error('Некорректный адрес оплаты.');
  const url = new URL(value);
  const allowed = portal
    ? ['www.creem.io','creem.io'].includes(url.hostname) && url.pathname.startsWith('/portal/')
    : (['www.creem.io','creem.io'].includes(url.hostname) && /^\/(?:checkout|payment)\/[A-Za-z0-9_/-]+$/.test(url.pathname)) ||
      (url.hostname==='checkout.creem.io' && /^\/ch_[A-Za-z0-9_-]+\/?$/.test(url.pathname));
  if (url.protocol !== 'https:' || url.hash || url.username || url.password || url.port && url.port!=='443' || !allowed)
    throw Error('Некорректный адрес оплаты.');
  return url.href;
}

const benefits = {
  plus: ['1 200 токенов каждый оплаченный месяц','Для личных задач, текстов и документов','Удобный старт с платными возможностями'],
  pro: ['3 000 токенов каждый оплаченный месяц','Для регулярной работы и исследований','На 20% выгоднее за токен, чем Plus'],
  max: ['6 500 токенов каждый оплаченный месяц','Для больших проектов и активной работы','На 26% выгоднее за токен, чем Plus'],
};
const errors = {
  subscription_exists:'У тебя уже есть подписка. Открой управление подпиской, чтобы проверить её или отменить продление.',
  checkout_already_pending:'Страница оплаты ещё подготавливается. Подожди немного и снова выбери тот же тариф.',
  billing_provider_timeout:'Страница оплаты создаётся дольше обычного. Повтори тот же тариф — сохранённый заказ будет продолжен.',
  billing_not_configured:'Оплата ещё подключается. Попробуй позже.',
  billing_product_mismatch:'Параметры тарифа изменились. Обнови раздел перед оплатой.',
};

export function mountSubscriptionPlans(root, {billing=null, account=null, billingError=null, accountError=null, onRefresh=null, request=null,
    isCurrent=()=>true, navigate=url=>globalThis.location.assign(url)}={}) {
  const section = element('section', undefined, 'feature-card subscription-plans');
  section.id='velia-memberships';
  const header = element('div',undefined,'subscription-heading');
  header.append(element('span','VELIA MEMBERSHIP','subscription-eyebrow'),
    element('h3','Больше возможностей для твоих идей'),
    element('p','Выбери запас токенов под свой ритм. Пиши, исследуй и создавай документы вместе с Велией.'));
  section.append(header);
  const enabled = billing?.ok===true && billing.enabled===true && billing.mode==='prod' && account?.ok===true && typeof request==='function';
  const canManage = account?.ok===true && typeof request==='function' && (enabled || account.can_manage===true);
  const unavailable = {
    billing_not_configured:'Оплата пока не включена. Поддержка проверяет настройки Creem.',
    billing_webhook_not_ready:'Оплата недоступна: подключение подтверждений платежей ещё не готово.',
    billing_product_mismatch:'Оплата недоступна: настройки тарифа на платёжной стороне требуют проверки.',
    billing_provider_unavailable:'Платёжный сервис временно недоступен. Попробуй обновить статус.',
    billing_provider_credentials:'Подключение к платёжному сервису требует проверки. Попробуй позже.',
    billing_upstream_invalid_response:'Не удалось подключиться к серверу оплаты. Попробуй обновить статус.',
  };
  const reason = billingError?.code || billing?.unavailable_reason;
  const unavailableText = unavailable[reason] || (accountError ?
    'Не удалось проверить подписку аккаунта. Попробуй обновить статус.' :
    'Оплата временно недоступна. Попробуй обновить статус или обратись в support@velyoncore.ai.');
  const notice = element('p', enabled ? 'Ежемесячная подписка · отмена продления в любой момент' :
    unavailableText, 'subscription-notice');
  section.append(notice);
  if (!enabled && typeof onRefresh==='function') {
    const retry = element('button','Проверить оплату ещё раз','subscription-manage'); retry.type='button';
    retry.addEventListener('click',()=>{if(isCurrent())onRefresh();}); section.append(retry);
  }
  const grid = element('div', undefined, 'subscription-grid');
  const feedback = element('p','', 'subscription-feedback');
  feedback.setAttribute('role','status'); feedback.setAttribute('aria-live','polite');
  const money = value => new Intl.NumberFormat('ru-RU',{style:'currency',currency:'USD'}).format(value);
  const controls = [], initialDisabled = new Map(), attempts = new Map(), statusSlots=[];
  let busy = false;
  const transact = async (plan, portal=false, status=feedback, trigger=null) => {
    if (!(portal?canManage:enabled) || busy || !isCurrent()) return;
    busy = true;
    const label=trigger?.textContent;
    statusSlots.forEach(slot=>slot.textContent='');
    if(trigger)trigger.textContent=portal?'Открываю кабинет…':'Открываю оплату…';
    controls.forEach(button=>button.disabled=true);
    status.textContent = portal ? 'Открываю управление подпиской…' : 'Готовлю защищённую страницу оплаты…';
    try {
      if (!portal && !attempts.has(plan)) attempts.set(plan,crypto.randomUUID());
      const result = await request('economy/creem/'+(portal?'portal':'checkout'),
        {method:'POST',data:portal?{}:{plan,attempt:attempts.get(plan)}});
      if (!isCurrent()) return;
      if (!result?.ok) throw Error(result?.error||'Не удалось открыть оплату.');
      navigate(paymentDestination(portal?result.portal_url:result.checkout_url,portal));
    } catch (error) {
      if (isCurrent() && error.name!=='AbortError') status.textContent = errors[error.code] || errors[error.message] || unavailable[error.code] ||
        'Не удалось открыть оплату. Обнови раздел или обратись в support@velyoncore.ai. Повторного списания здесь не выполняется.';
    } finally {
      busy = false;
      if (isCurrent()) {controls.forEach(button=>button.disabled=initialDisabled.get(button));if(trigger)trigger.textContent=label;}
    }
  };
  for (const plan of CREEM_SUBSCRIPTIONS) {
    const card = element('article', undefined, `subscription-plan subscription-plan-${plan.code}`);
    const art = element('div',undefined,'subscription-art'); art.setAttribute('aria-hidden','true');
    art.append(element('span','V','subscription-monogram'),element('span','', 'subscription-orbit'));
    const badge = element('span', plan.code==='pro'?'Для ежедневной работы':plan.code==='max'?'Максимум токенов':'Лёгкий старт','subscription-badge');
    const content = element('div',undefined,'subscription-content');
    content.append(badge,element('h4',plan.name),element('p',money(plan.price),'subscription-price'),
      element('span','в месяц · USD','subscription-period'));
    const verifiedPlan=enabled && Array.isArray(billing?.plans)
      ? billing.plans.find(item=>item?.code===plan.code) : null;
    const taxLabel=verifiedPlan?.tax_mode==='inclusive'?'Налог включён в цену':
      verifiedPlan?.tax_mode==='exclusive'?'Налог добавляется при оплате':'Итоговая сумма — на странице оплаты';
    content.append(element('p',taxLabel,'field-hint subscription-tax'));
    const list = element('ul',undefined,'subscription-benefits');
    benefits[plan.code].forEach(text=>list.append(element('li',text)));
    content.append(list);
    const current = account?.plan_code===plan.code;
    const button = element('button',current?'Управлять подпиской':'Выбрать '+plan.name,'feature-primary');
    const existing = account?.has_active_subscription===true;
    if (!enabled && !existing) button.textContent='Оплата недоступна';
    if (existing && !current) button.textContent='Подписка уже оформлена';
    button.type = 'button'; button.disabled = existing ? (!current || !canManage) : !enabled;
    button.setAttribute('aria-label',current?'Управлять подпиской '+plan.name:'Оформить подписку '+plan.name);
    // Only server-created, owner-bound checkouts can be opened. Public product
    // links above remain identifiers for configuration, never a payment fallback.
    const status=element('p','','subscription-feedback');status.id='velia-payment-status-'+plan.code;status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    button.setAttribute('aria-describedby',status.id);statusSlots.push(status);
    if (!button.disabled) button.addEventListener('click',()=>transact(plan.code,existing,status,button));
    controls.push(button); content.append(button,status);
    card.append(art,content); grid.append(card);
  }
  section.append(grid,feedback);
  if (account?.has_subscription) {
    const manage = element('button','Платежи и управление продлением','subscription-manage');
    manage.type='button'; manage.disabled=!canManage;
    if (canManage) manage.addEventListener('click',()=>transact(null,true));
    controls.push(manage); section.append(manage);
  }
  controls.forEach(button=>initialDisabled.set(button,button.disabled));
  section.append(element('p','Токены зачисляются после подтверждения платежа. Итоговая сумма и налоговая разбивка показываются на странице оплаты.','field-hint'),
    element('p','Free остаётся доступным. Разовое пополнение токенов не меняет тариф подписки.','field-hint'));
  root.append(section);
  return section;
}
