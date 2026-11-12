const PLANS = Object.freeze({free:'Free',plus:'Plus',pro:'Pro',max:'Max'});
const integer = value => Number.isSafeInteger(value) && value>=0 ? value : null;
const format = value => value===null ? '—' : new Intl.NumberFormat('ru-RU').format(value);
function expiry(value) {
  if(typeof value!=='string' || !/^\d{4}-\d{2}-\d{2}T/.test(value))return null;
  const date=new Date(value.replace(/(\.\d{3})\d+/,'$1')+(/[zZ]$|[+-]\d\d:\d\d$/.test(value)?'':'Z'));
  return Number.isFinite(date.getTime()) ? date : null;
}
export function balanceOverview(account, usage, {now=Date.now()}={}) {
  const code=Object.hasOwn(PLANS,account?.plan_code)?account.plan_code:'free';
  const until=code==='free'?null:expiry(account?.subscription_until);
  return {code,plan:PLANS[code],credits:format(integer(account?.credits)),
    status:code==='free'?'Бесплатный доступ':until?(until.getTime()>now?'Доступ до '+new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',year:'numeric'}).format(until):'Период подписки завершён'):'Данные периода уточняются',
    messages:format(integer(usage?.user_messages)),hasUsage:integer(usage?.user_messages)!==null,
    hasBalance:integer(account?.credits)!==null};
}
function node(tag,text,css) {
  const item=document.createElement(tag);
  if(text!==undefined)item.textContent=text;
  if(css)item.className=css;
  return item;
}
export function mountBalanceOverview(root,{account=null,usage=null,now=Date.now()}={}) {
  const view=balanceOverview(account,usage,{now});
  const section=node('section',undefined,'balance-overview');section.setAttribute('aria-label','Баланс и текущий тариф');
  const hero=node('article',undefined,'balance-hero');
  const amount=node('div',undefined,'balance-amount');
  amount.append(node('span','БАЛАНС VELIA','balance-eyebrow'),node('p',view.credits,'balance-number'),
    node('span','токенов доступно','balance-unit'),node('p',view.hasBalance?'Твой запас для задач с Велией.':'Не удалось загрузить баланс. Попробуй обновить раздел.','balance-caption'));
  const plan=node('div',undefined,'balance-plan');
  const badge=node('span',view.plan,'balance-plan-badge balance-plan-'+view.code);
  plan.append(node('span','ТЕКУЩИЙ ТАРИФ','balance-eyebrow'),badge,node('p',view.status,'balance-plan-status'));
  const link=node('a','Посмотреть тарифы ↓','balance-plan-link');link.href='#velia-memberships';plan.append(link);
  hero.append(amount,plan);section.append(hero);
  if(view.hasUsage) {
    const activity=node('article',undefined,'balance-activity');
    const value=node('div',undefined,'balance-activity-value');value.append(node('strong',view.messages),node('span','учтено ответов сегодня'));
    const context=node('p','Показаны ответы из доступной статистики аккаунта.','balance-caption');
    activity.append(value,context);section.append(activity);
  }
  root.append(section);return section;
}
