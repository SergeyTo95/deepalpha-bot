// Purchase preparation only. No client callback or redirect can grant credits.
import {mountSubscriptionPlans} from './subscription-plans.mjs';
export function tokenPackages(catalog) {
  if (catalog?.ok !== true || !Array.isArray(catalog.topups)) return [];
  const seen = new Set();
  return catalog.topups.flatMap(pack => {
    const credits = pack?.credits, price = pack?.store_price_usd;
    if (!Number.isSafeInteger(credits) || credits <= 0 || seen.has(credits) ||
        typeof price !== 'number' || !Number.isFinite(price) || price <= 0 || price > 100000) return [];
    seen.add(credits);
    return [{credits, price, currency:'USD'}];
  }).sort((a,b) => a.credits - b.credits);
}

function element(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}

export function mountTokenPurchase(root, catalog, options={}) {
  mountSubscriptionPlans(root, options);
  const section = element('section', undefined, 'feature-card token-purchase');
  const title = element('h3', 'Купить токены');
  section.append(title, element('p', 'Токены VELIA — внутренний баланс для платных возможностей сервиса. Это не криптовалюта; обмен на деньги не предусмотрен.'));
  section.append(element('p', 'Оплата картой через Creem готовится к запуску.', 'field-hint'));
  const packs = tokenPackages(catalog);
  if (packs.length) {
    const group = element('fieldset', undefined, 'token-packages');
    group.append(element('legend', 'Выбери пакет'));
    const total = element('p', undefined, 'token-total');
    total.setAttribute('aria-live', 'polite');
    const money = price => new Intl.NumberFormat('ru-RU', {style:'currency',currency:'USD'}).format(price);
    const choose = pack => {total.textContent = `${pack.credits.toLocaleString('ru-RU')} токенов · ${money(pack.price)}`;};
    packs.forEach((pack,index) => {
      const label = element('label', undefined, 'token-package');
      const input = element('input');
      input.type = 'radio'; input.name = 'velia-token-package'; input.value = String(pack.credits); input.checked = index === 0;
      input.addEventListener('change', () => choose(pack));
      label.append(input, element('strong', `${pack.credits.toLocaleString('ru-RU')} токенов`), element('span', money(pack.price)));
      group.append(label);
    });
    choose(packs[0]);
    section.append(group,total,element('p','Цены — из действующего каталога VELIA. Итоговая сумма с применимыми налогами будет показана перед оплатой.','field-hint'));
  } else {
    section.append(element('p','Пакеты сейчас недоступны. Обнови раздел немного позже.','field-hint'));
  }
  const button = element('button','Оплата картой — скоро','feature-primary');
  button.type = 'button'; button.disabled = true;
  // Deliberately ignore billing_enabled: the existing catalog flag enables
  // Google Play, not Creem. Card acceptance requires a separate server release.
  section.append(button,element('p','Платежи пока не принимаются. Выбор пакета не создаёт заказ и не списывает деньги.','field-hint'));
  const help = element('details');
  help.append(element('summary','Как будет проходить покупка'));
  const steps = element('ol');
  for (const text of ['Выбери пакет токенов.','Оплати картой на защищённой странице оплаты.','После подтверждения платежа токены появятся на балансе VELIA.']) steps.append(element('li',text));
  help.append(steps);
  section.append(help);
  root.append(section);
  return section;
}
