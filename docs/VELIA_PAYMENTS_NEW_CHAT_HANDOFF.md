# VELIA Payments — полный handoff для нового чата

Скопируй всё содержимое после разделителя и отправь первым сообщением в новом чате.

---

Продолжаем разработку платежной инфраструктуры **VELIA**.

Общайся со мной **по-русски, прямо, практично и как ведущий инженер проекта**. Не ограничивайся советами: работай с GitHub через подключённый GitHub connector, проверяй реальный код, PR, exact-head CI и Railway commit statuses.

Во время долгой работы регулярно пиши короткий статус:

- что проверено;
- что найдено;
- что изменено;
- какие проверки выполняются;
- что осталось;
- есть ли блокеры.

Не утверждай, что функция работает, пока это не подтверждено кодом, exact-head CI, deployment и реальным acceptance-тестом. Не придумывай результаты CI, merge, Railway или on-chain операций.

## 1. Репозитории и production

Backend:

```text
SergeyTo95/deepalpha-bot
```

Backend production/deploy branch:

```text
feature/turbo-short-term-btc
```

Android:

```text
SergeyTo95/deepalpha-android
```

Android integration branch:

```text
develop
```

Public backend:

```text
https://deepalpha-ai.com
```

Railway project:

```text
melodious-radiance
```

Production services на момент handoff:

```text
deepalpha-bot
velyon-memory
velia-repowise
velia-payment-worker
```

Последний фактически проверенный backend production head на момент handoff:

```text
d718dfa74d8202d8d59780397148a18e06b12989
```

Это merge PR #448 `Add safe Gram Treasury diagnostics for failed setup`.

На этом SHA подтвержден Railway production:

```text
deepalpha-bot          success
velyon-memory          success
velia-repowise         success
velia-payment-worker   success
```

В новой сессии всё равно сначала повторно проверь текущий head production branch и combined status. Не считай этот SHA вечной константой.

## 2. Публичные названия и продуктовая модель

Пользовательские названия:

- приложение: **VELIA**;
- помощник: **Velia / Велия**;
- нейроинтеллект: **Velyon Core**;
- premium reasoning: **Velyon Core Deep**;
- платная compute-единица: **VELIA Credits**;
- сеть TON на пользовательской стороне называется **Gram**.

Важно:

- пользователю не показывать `TON` как бренд сети — писать **Gram**;
- технические identifiers/env/API/DB (`TON_*`, Toncenter, Jetton, `network="ton"`) внутри backend не переименовывать, если это реальные protocol identifiers;
- upstream provider/model names не показывать в публичных планах/checkout.

## 3. Economy v0.2

Планы:

```text
Free
Store: $0
100 Premium Credits / month
+50 welcome Credits
Velyon Core: 5 requests/day draft fair-use

Plus
Store: $14.99/month
USDT: $10.49/month
1,200 Credits/month
Velyon Core included / generous fair use

Pro
Store: $29.99/month
USDT: $20.99/month
3,000 Credits/month
Velyon Core included / high fair use
```

Top-ups:

```text
100 Credits    $2.49 Store / 1.74 USDT
250 Credits    $4.99 / 3.49
800 Credits    $12.99 / 9.09
2,000 Credits  $27.99 / 19.59
5,000 Credits  $59.99 / 41.99
10,000 Credits $109.99 / 76.99
```

Коммерческие правила:

- crypto discount: 30%;
- Store fee planning assumption: 15%;
- crypto operational reserve: 1%;
- provider-cost ceiling: $0.0024 per consumed Premium Credit;
- subscription rollover: максимум один дополнительный monthly allowance;
- purchased Credits не истекают;
- subscription Credits тратятся раньше purchased Credits;
- crypto −30% не складывается с generic discount.

Не утверждать, что recurring Free grant engine и отдельные subscription/purchased buckets уже полностью реализованы: runtime всё ещё имеет compatibility surface через aggregate `users.token_balance`.

## 4. Google Play billing — backend

Backend PR #444 уже merged в production.

Реализовано:

```text
GET  /mobile-api/v1/economy/catalog
GET  /mobile-api/v1/economy/me
POST /mobile-api/v1/economy/google-play/verify
```

Product IDs:

Subscriptions:

```text
velia_plus_monthly
velia_pro_monthly
```

One-time:

```text
velia_credits_100
velia_credits_250
velia_credits_800
velia_credits_2000
velia_credits_5000
velia_credits_10000
```

Backend trust boundary:

- purchase verified server-side через Android Publisher API;
- pending/unknown/mismatched purchase не выдаёт Credits;
- raw purchase token не хранится, хранится hash;
- same token нельзя claim другим VELIA user;
- one-time product fulfillment idempotent + server-side consume;
- subscriptions server-verified + acknowledge when required;
- Android не является entitlement source of truth.

Но реальные Google Play покупки **ещё не активированы**.

Нужно вне GitHub:

1. создать продукты/subscriptions в Play Console;
2. настроить Android Publisher service account;
3. положить service-account JSON в Railway напрямую, не в чат;
4. настроить package `ai.deepalpha.android`;
5. включить backend billing flag только после готовности внешней конфигурации;
6. провести licensed/internal Play test purchase end-to-end.

## 5. Android Google Play billing

Android PR:

```text
SergeyTo95/deepalpha-android#35
feature/velia-commercial-plans-v1 -> develop
```

На момент handoff:

```text
state: open
Draft: true
merged: false
mergeable: true
head: ac22e0a2a22a249b6c2db139a238f863ecdc27b5
```

Exact-head Android CI был green: lint, unit tests, `assembleDebug`, exact SHA proof.

Android использует Google Play Billing Library 9.1.0 и не выдаёт Credits локально.

Не утверждать, что Android billing уже в `develop` или production, пока PR #35 реально не merged и post-merge CI не проверен.

## 6. USDT checkout v1

Backend PR #445 уже merged.

Direct/web checkout:

```text
GET  /velia/pay
POST /velia/pay
GET  /velia/pay/status/{reference}
```

Authenticated direct/mobile API:

```text
GET  /mobile-api/v1/economy/usdt/catalog
POST /mobile-api/v1/economy/usdt/intents
GET  /mobile-api/v1/economy/usdt/intents/{reference}
```

Public pricing показывает **USDT −30%** и Store reference price.

Phase-1 сети:

### TRON / TRC20 USDT

Canonical contract:

```text
TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t
```

### Solana USDT

Canonical mint:

```text
Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB
```

### Gram / USDT Jetton

Технический network id внутри backend: `ton`.

Canonical USDT Jetton master:

```text
EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs
```

USDT decimals:

```text
6
```

BNB и Polygon остаются fail-closed.

## 7. velia-payment-worker

Отдельный Railway service уже создан:

```text
velia-payment-worker
```

Start command:

```text
python run_payment_worker.py
```

Он использует тот же production PostgreSQL через internal/reference `DATABASE_URL`.

Worker watch-only:

- seed/private key не читает;
- транзакции не подписывает;
- send/sweep API не содержит;
- только наблюдает chain transfer и выполняет reviewed fulfillment boundary.

Основные flags:

```env
VELIA_PAYMENT_WORKER_ENABLED=false
VELIA_USDT_CHECKOUT_ENABLED=false

VELIA_PAYMENT_TRON_ENABLED=false
VELIA_PAYMENT_SOLANA_ENABLED=false
VELIA_PAYMENT_TON_ENABLED=false
VELIA_PAYMENT_BNB_ENABLED=false
VELIA_PAYMENT_POLYGON_ENABLED=false

VELIA_PAYMENT_POLL_INTERVAL_SECONDS=30
```

RPC variables:

```text
TRON_RPC_URL
SOLANA_RPC_URL
TON_RPC_URL
```

Optional API keys:

```text
TRON_RPC_API_KEY
SOLANA_RPC_API_KEY
TON_RPC_API_KEY
```

Deposit addresses:

```text
VELIA_PAYMENT_TRON_DEPOSIT_ADDRESS
VELIA_PAYMENT_SOLANA_DEPOSIT_ADDRESS
VELIA_PAYMENT_TON_DEPOSIT_ADDRESS
```

В текущей сессии пользователь уже добавил TRON и Solana receiving addresses и RPC variables в Railway. TON/Gram RPC использует Toncenter; существующий Toncenter API key рекомендуется передавать в worker через Railway Reference Variable, а не копировать secret в чат.

Не считать env-содержимое подтверждённым через GitHub: это user-configured external state, проверять deployment/health/logs отдельно.

## 8. Защита USDT invoices

- global checkout default OFF;
- network flags default OFF;
- max 5 active crypto invoices/user;
- quote TTL 30 min;
- каждый invoice получает exact micro-USDT fingerprint;
- match требует exact network + canonical asset + recipient + exact atomic amount + finalized + timestamp внутри invoice lifetime;
- fulfillment использует DB locks/idempotency;
- одна chain transaction не должна дважды выдавать Credits/plan.

Важно: public checkout нельзя открывать только ради теста.

Ранее найден architectural gap: обычный intent creation блокируется, когда `VELIA_USDT_CHECKOUT_ENABLED=false`. Для controlled payment acceptance нужен безопасный restricted/test-only intent path или другой reviewed механизм, чтобы не открывать public checkout до acceptance. Перед real mainnet payment test этот gap нужно повторно проверить по актуальному production-коду и закрыть, если он ещё существует.

## 9. Gram admin/Treasury — что уже сделано

PR #446 merged:

- `/admin → 💎 Gram Wallets` показывает отдельно:
  - Admin custodial Gram wallet;
  - Treasury / payments;
  - Referral payout;
  - USDT on Gram readiness;
  - technical runtime;
- полные public addresses доступны отдельными кнопками;
- пользовательская надпись — **Gram**;
- добавлен safe two-step `Set admin wallet as watch-only Treasury`;
- seed из custodial wallet не копируется;
- watch-only Treasury должен иметь `seed_encrypted = NULL`;
- другой существующий active Treasury автоматически не заменяется.

Текущий admin custodial public Gram address, показанный production UI:

```text
UQARaLE231LslLaQtra38Z8G5DQAZBXm90_lQwHNuyqGpeo0
```

Это публичный адрес, не секрет.

## 10. Gram Treasury Withdraw

PR #447 merged.

В Telegram-admin реализован flow:

```text
/admin
→ 💎 Gram Wallets
→ 💸 Treasury Withdraw
```

Поддержка:

- Gram withdrawal;
- USDT on Gram withdrawal;
- live balance snapshot;
- destination;
- exact decimal amount;
- optional memo/tag;
- immutable 10-minute preview;
- separate confirmation;
- tx hash;
- recent withdrawal journal.

Safety:

```env
VELIA_GRAM_TREASURY_WITHDRAW_ENABLED=false
VELIA_GRAM_TREASURY_USDT_WITHDRAW_ENABLED=false
```

Эти две variables пользователь уже добавил **в `deepalpha-bot`**, не в payment-worker, и оставил `false`.

Не включать их до корректно настроенного Treasury и controlled real test.

Withdraw flow не требует включать глобальный `TON_WALLET_ENABLED`, поэтому user wallet runtime можно держать OFF.

Для USDT используется canonical master, 6 decimals, TEP-74 transfer, double-confirm и DB state machine. При неоднозначном broadcast result должен быть `submission_uncertain`, без автоматического повторного send.

## 11. Текущий production blocker — самое важное

Пользователь в production нажал:

```text
Set admin wallet as watch-only Treasury
→ Confirm watch-only Treasury
```

Но после Refresh UI всё ещё показывал:

```text
Treasury / payments: — [not configured]
Mode: not configured
USDT on Gram: WAITING: set admin wallet as Treasury
```

То есть Treasury **фактически не был создан/активирован**.

Не считать это исправленным.

Историческую гипотезу `seed_encrypted NOT NULL` не принимать за факт: текущий и historical schema-код показывает `seed_encrypted TEXT` nullable. Нельзя продолжать угадывать причину без runtime evidence.

## 12. Диагностика blocker — PR #448 уже production

PR #448 merged в production head:

```text
d718dfa74d8202d8d59780397148a18e06b12989
```

Он добавил read-only кнопку:

```text
🧾 Treasury diagnostics
```

Диагностика должна показывать только безопасные данные:

- cashier row id;
- masked public address;
- network;
- status;
- `watch-only` или `managed`;
- совпадает ли row с admin custodial address;
- sanitized failure code;
- для unexpected DB failure — только exception class + SQLSTATE.

Никаких seed/mnemonic/private key/raw exception params.

### Первое действие нового чата

1. Проверить, что production head всё ещё содержит PR #448 и Railway 4/4 green.
2. Попросить пользователя открыть:

```text
/admin → 💎 Gram Wallets → 🧾 Treasury diagnostics
```

3. Получить скрин безопасной диагностики.
4. Если после нового Confirm появляется `Treasury not changed: <code>`, получить exact code.
5. По этим runtime evidence найти root cause в коде/DB compatibility path.
6. Сделать отдельный PR с regression test.
7. После exact-head CI + mergeability + review threads 0 + Railway preview green — можно merge без повторного вопроса: владелец дал явное разрешение `если все, то мерж, даже не спрашивай у меня`.
8. После production deployment снова реально нажать Confirm и проверить UI.

Не выполнять произвольные SQL mutations над production users/wallets. Исправление должно быть воспроизводимым и idempotent.

## 13. Что делать после успешного Treasury setup

Когда UI реально покажет active watch-only Treasury с тем же admin custodial address:

1. В Railway `velia-payment-worker` поставить:

```env
VELIA_PAYMENT_TON_DEPOSIT_ADDRESS=UQARaLE231LslLaQtra38Z8G5DQAZBXm90_lQwHNuyqGpeo0
```

2. Проверить `TON_RPC_URL` и `TON_RPC_API_KEY` reference.
3. Оставить public checkout OFF.
4. Сначала запустить только Gram watcher:

```env
VELIA_PAYMENT_WORKER_ENABLED=true
VELIA_PAYMENT_TON_ENABLED=true
VELIA_PAYMENT_TRON_ENABLED=false
VELIA_PAYMENT_SOLANA_ENABLED=false
VELIA_USDT_CHECKOUT_ENABLED=false
```

5. Проверить `/health`, `/ready` и logs. Нужен реальный successful poll, а не только Railway deploy success.
6. После Gram watcher отдельно включать TRON и Solana по одной сети, а не всё одновременно.

## 14. Controlled withdrawal acceptance

После Treasury setup:

### Gram

1. Включить только:

```env
VELIA_GRAM_TREASURY_WITHDRAW_ENABLED=true
VELIA_GRAM_TREASURY_USDT_WITHDRAW_ENABLED=false
```

2. Сделать минимальный реальный Gram withdrawal на свой контролируемый address.
3. Проверить on-chain tx hash и фактическое получение.
4. Проверить journal и отсутствие double-send.

### USDT on Gram

Только после успешного Gram test:

```env
VELIA_GRAM_TREASURY_USDT_WITHDRAW_ENABLED=true
```

Сделать минимальный USDT test, проверить canonical asset, tx hash, recipient и фактическое получение.

Не считать USDT withdrawal production-accepted только по offline BoC/CI.

## 15. Controlled incoming USDT acceptance

После исправления test-intent gap и готового watcher:

Нужно отдельно для одной сети провести:

```text
restricted test intent
→ exact payment amount/fingerprint
→ chain observation
→ finalized match
→ exactly one fulfillment
→ exactly one Credits/plan mutation
→ repeated poll does not duplicate fulfillment
```

Только после этого можно обсуждать:

```env
VELIA_USDT_CHECKOUT_ENABLED=true
```

Public checkout нельзя включать раньше.

## 16. TRON / Solana

TRON и Solana receiving addresses уже были добавлены пользователем в Railway в текущей сессии.

Рекомендуемый rollout:

```text
1. Gram watcher
2. TRON watcher
3. Solana watcher
```

Каждую сеть активировать и acceptance-тестировать отдельно.

Не просить seed/private key биржи или кошелька. Receiving addresses публичные; secret/API keys пользователь вводит только в Railway.

## 17. Bybit warning

Не использовать биржевой address вслепую для Gram, если биржа требует memo/tag. Наш inbound matcher должен быть совместим с destination requirements; иначе VELIA может увидеть chain transfer, а биржа не зачислит его владельцу.

Для Treasury предпочтителен собственный admin custodial Gram address, который сейчас и выбран архитектурно.

## 18. Техническая безопасность

Никогда не просить и не выводить:

- seed phrase;
- mnemonic;
- private key;
- `MASTER_ENCRYPTION_KEY`;
- service account JSON;
- RPC API keys;
- Railway secret values;
- bot token;
- GitHub installation token/private key.

Public receiving addresses можно показывать.

Payment worker должен оставаться watch-only.

Outgoing signing находится только в отдельно review-нутом admin Treasury withdrawal path и должен требовать exact Treasury/admin-address match.

## 19. Merge policy для этой линии работы

Владелец дал разрешение:

```text
Если все, то мерж, даже не спрашивай у меня.
```

Это означает: для подготовленного PR можно не запрашивать ещё одно `го мерж`, **только если** одновременно выполнены:

- exact-head CI green;
- PR mergeable;
- branch не отстаёт от production;
- unresolved review threads = 0;
- Railway preview green для затронутых production services;
- нет неизвестного money/security blocker;
- change scope соответствует согласованной задаче.

После merge всё равно обязательно проверить production branch SHA и Railway production deployment.

Это разрешение **не означает** автоматически включать real-money flags, public checkout, withdrawals или внешние credentials. Money activation выполняется только отдельными controlled шагами с реальными тестами.

## 20. Ключевые PR

```text
#439 Economy v0.2                     merged
#440 Video Standard 5s = 100 Credits merged
#444 Google Play backend billing     merged
#445 USDT checkout v1                merged
#446 Gram admin/Treasury UI          merged
#447 Gram + USDT Treasury Withdraw   merged
#448 Treasury diagnostics            merged
Android #35 Google Play billing      Draft / not merged
```

Перед опорой на эти статусы всё равно перепроверить GitHub.

## 21. Что нельзя объявлять готовым

Пока нет реального acceptance, нельзя утверждать, что:

- Google Play purchases live;
- Android billing уже merged;
- USDT public checkout live;
- Gram Treasury configured;
- Gram watcher реально poll-ит chain;
- TRON/Solana watcher реально poll-ят chain;
- incoming USDT реально выдаёт Credits exactly once;
- Gram withdrawal реально прошёл mainnet;
- USDT withdrawal реально прошёл mainnet.

## 22. Точный порядок продолжения

Начни новый чат так:

1. GitHub: прочитай `README.md` и этот файл.
2. Проверь production head + Railway 4/4.
3. Проверь PR #448 merged/current.
4. Попроси скрин `🧾 Treasury diagnostics` из Telegram admin.
5. Получи exact sanitized failure/runtime state.
6. Найди root cause Treasury setup failure.
7. Сделай fix PR + exact-head tests.
8. Если gate полностью green — merge без нового вопроса.
9. Подтверди production deploy.
10. Повтори Treasury setup и реальный UI verification.
11. Привяжи Gram deposit address к payment-worker.
12. Запусти только Gram watcher и проверь live poll.
13. Проведи минимальный Gram withdrawal test.
14. Проведи минимальный USDT-on-Gram withdrawal test.
15. Закрой restricted test-intent gap.
16. Проведи incoming USDT exactly-once acceptance.
17. После этого по одной сети добавить TRON и Solana acceptance.
18. Google Play activation вести отдельным треком: Play Console + credentials + Android PR #35 + licensed purchase test.

Главный принцип: **не включать публичные деньги раньше доказанного end-to-end acceptance**.
