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

export function mountSubscriptionPlans(root) {
  const section = element('section', undefined, 'feature-card subscription-plans');
  section.append(element('h3','Подписки VELIA'),
    element('p','Ежемесячные тарифы с токенами для платных возможностей VELIA.'),
    element('p','Подписки готовятся к запуску. Оплата станет доступна после подключения начислений.','subscription-notice'));
  const grid = element('div', undefined, 'subscription-grid');
  const money = value => new Intl.NumberFormat('ru-RU',{style:'currency',currency:'USD'}).format(value);
  for (const plan of CREEM_SUBSCRIPTIONS) {
    const card = element('article', undefined, `subscription-plan subscription-plan-${plan.code}`);
    card.append(element('h4',plan.name),element('p',`${money(plan.price)} / месяц`,'subscription-price'),
      element('p',`${plan.monthlyCredits.toLocaleString('ru-RU')} токенов за оплаченный месяц`,'subscription-credits'));
    const button = element('button','Подписка — скоро','feature-primary');
    button.type = 'button'; button.disabled = true;
    button.setAttribute('aria-label',`Подписка ${plan.name} пока недоступна`);
    // Do not attach productUrl to href, data attributes or a click handler.
    // A live public payment link cannot bind the purchase to this account.
    card.append(button);
    grid.append(card);
  }
  section.append(grid,
    element('p','После запуска: автоматическое продление каждый месяц с возможностью отменить следующие списания. Итоговая сумма с применимыми налогами будет показана перед оплатой.','field-hint'),
    element('p','Разовое пополнение токенов не меняет тариф подписки.','field-hint'));
  root.append(section);
  return section;
}
