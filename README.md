title: DeepAlpha
emoji: 📈
colorFrom: blue
colorTo: purple
sdk: docker

# VELIA / DeepAlpha backend

> Внутренний инженерный README. Источник фактического статуса backend, Android-клиента, VELIA Developer, Coding Agent, Coding Autopilot и VELIA Payments. Не считать функцию работающей только потому, что код написан или смержен: нужны exact-head CI, deployment и реальный acceptance.

**Последнее обновление:** 2026-08-09  
**Backend:** `SergeyTo95/deepalpha-bot`  
**Android:** `SergeyTo95/deepalpha-android`  
**Backend production branch:** `feature/turbo-short-term-btc`  
**Android integration branch:** `develop`  
**Public backend:** `https://deepalpha-ai.com`  
**Railway project:** `melodious-radiance`  
**Railway services:** `deepalpha-bot`, `velyon-memory`, `velia-repowise`, `velia-payment-worker`

## 1. Актуальный подтверждённый baseline

### Backend production

Последний проверенный production commit на момент обновления:

```text
d718dfa74d8202d8d59780397148a18e06b12989
```

Это merge PR `#448` — safe Gram Treasury diagnostics для расследования production blocker, при котором `Set admin wallet as watch-only Treasury` не создаёт активный Treasury.

Для commit подтверждено:

- Railway `deepalpha-bot` — `success`;
- Railway `velyon-memory` — `success`;
- Railway `velia-repowise` — `success`;
- Railway `velia-payment-worker` — `success`;
- production Gram admin содержит read-only `🧾 Treasury diagnostics`;
- money/withdraw/payment flags этим PR не включались.

Старый Autopilot path-filter fix `8497c3709a825c4f3cf14d63dc74db5dc5905ca8` остаётся частью production history, но уже не является текущим head.

В новой сессии всё равно сначала повторно проверить текущий head branch и combined status: SHA выше является baseline этого README, а не вечной константой.

### Android

Последний подтверждённый Android Autopilot Center merge commit:

```text
fdc7c9e151df0accf56c25d0655a584ad5ea7cdb
```

Подтверждено:

- merge в `develop`;
- post-merge Android CI — `success`;
- lint, unit tests и `assembleDebug` прошли;
- APK установлена на реальное устройство;
- экран `Coding Autopilot` подключился к production backend;
- на устройстве показано `Worker готов`;
- CI Watch, CI Repair, Actions logs fallback, Review Loop и merge dry-run отображаются как включённые;
- UI явно показывает `draft_pr_only`, `auto_merge=false`, `deployment=false`.

Google Play billing Android PR `#35` на момент обновления остаётся **Draft / not merged**, head `ac22e0a2a22a249b6c2db139a238f863ecdc27b5`, mergeable=true.

## 2. Что такое VELIA

VELIA — персональный ИИ-помощник и AI-среда, а не только чат.

Публичные названия:

- продукт и приложение: **VELIA**;
- помощник: **Velia / Велия**;
- интеллектуальное ядро: **Velyon Core**;
- premium reasoning: **Velyon Core Deep**;
- генерация изображений: **Velia Images / Velyon Core Images**;
- платная compute-единица: **VELIA Credits**;
- prediction-market модуль: **DeepAlpha**;
- TON-сеть на пользовательской стороне: **Gram**.

Backend исторически вырос из `deepalpha-bot`, поэтому старые DeepAlpha-названия в инфраструктуре и коде допустимы. Новые пользовательские AI-возможности развиваются как VELIA.

Технические `TON_*`, Toncenter, Jetton и `network="ton"` внутри backend не переименовывать, если это реальные protocol identifiers. На пользовательской стороне писать **Gram**.

## 3. Высокоуровневая архитектура

```text
Android VELIA
  ├─ ordinary chat / SSE
  ├─ Agent cards
  ├─ Coding Agent cards
  └─ Coding Autopilot Center
        ├─ missions
        ├─ queue
        ├─ runs
        ├─ CI attempts
        ├─ review actions
        └─ merge-policy dry-run

Authenticated mobile API
  └─ run_web_process.py
       ├─ ordinary VELIA chat
       ├─ VELIA Agent Core
       ├─ VELIA Developer read-only Fast Path
       ├─ guarded Coding Agent
       └─ Coding Autopilot workers
            ├─ task worker
            ├─ exact-head CI watch
            ├─ bounded CI repair
            ├─ bounded Actions logs
            ├─ Review Loop
            └─ merge recommendation dry-run

Payments
  ├─ Google Play server verification in deepalpha-bot
  ├─ direct/web USDT checkout in deepalpha-bot
  ├─ velia-payment-worker
  │    ├─ watch-only TRON
  │    ├─ watch-only Solana
  │    └─ watch-only Gram/USDT
  └─ Telegram admin Gram Treasury
       ├─ watch-only payment routing
       └─ guarded Gram/USDT withdrawal path

GitHub
  ├─ GitHub App installation tokens
  ├─ read-only repository retrieval
  ├─ isolated velia/... branches
  ├─ atomic commits
  └─ draft PR only
```

## 4. Реально принятые функции

### 4.1 VELIA Developer read-only

На реальном Android подтверждён путь:

```text
ordinary chat
→ mobile SSE
→ Developer router
→ connected GitHub repository
→ deterministic Fast Path retrieval
→ Kimi final answer
→ verified file:Lx-Ly citations
```

Реальный замер:

```text
Стоимость: $0.03721
Время: 47.1 s
```

Основные файлы:

```text
services/velia_developer_chat_runtime_patch.py
services/velia_developer_fast_path_service.py
services/velia_developer_github_service.py
services/velia_mobile_streaming_service.py
```

### 4.2 VELIA Agent Core

На реальном Android подтверждено:

```text
естественная команда
→ structured plan
→ approval
→ tool execution
→ persistence
→ subsequent read
```

Проверены инструменты:

```text
velia.tasks.create_draft
velia.tasks.list
```

Проверены:

- write ждёт подтверждения;
- read-plan тоже выполняется через защищённый Agent Core;
- повторное выполнение не создаёт дубль;
- данные сохраняются между планами;
- Android показывает нативные карточки плана и результата.

### 4.3 VELIA Coding Agent

На реальном Android подтверждено:

```text
repository change request
→ Coding Agent plan card
→ explicit approval
→ velia/... branch
→ sequential commits
→ draft PR
→ structured result card
```

Проверено:

- план ничего не меняет до approval;
- создаётся отдельная ветка;
- commits атомарны по шагам;
- PR создаётся как draft;
- Android показывает repository, base/work branch, steps, files, commits и ссылку на draft PR;
- merge и deployment не выполняются.

### 4.4 Android Autopilot Center connectivity

На реальном устройстве подтверждено:

- production API доступен;
- список проектов загружается;
- форма создания paused-миссии отображается;
- worker сообщает готовность;
- все автономные capability flags читаются с backend;
- merge/deploy actions в UI отсутствуют.

Это подтверждает transport и конфигурацию, но не заменяет полный autonomous run acceptance.

## 5. VELIA Coding Autopilot

### 5.1 Цель

Autopilot должен работать без постоянного ручного подтверждения каждого шага внутри заранее утверждённой миссии:

```text
paused mission
→ queue
→ activation
→ autonomous planning
→ isolated branch
→ commits
→ draft PR
→ exact-head CI
→ bounded repair
→ review repair
→ merge recommendation dry-run
```

### 5.2 Foundation

Реализовано:

- missions создаются в статусе `paused`;
- обязательный `allowed_paths`;
- protected paths;
- очередь с приоритетом и idempotency;
- один active run на repository;
- PostgreSQL advisory locks;
- `FOR UPDATE SKIP LOCKED`;
- DB leases и heartbeat/recovery;
- worker вызывает существующий Coding Agent, а не второй write-engine;
- результат первого write-этапа — только draft PR.

Основные файлы имеют префикс:

```text
services/velia_developer_coding_autopilot_*.py
services/velia_developer_autopilot_*.py
```

Перед изменением кода в новой сессии нужно найти точные актуальные имена через repository search, а не угадывать их по README.

### 5.3 CI Watch и bounded repair

Реализован lifecycle:

```text
queued
→ planning
→ executing
→ waiting_ci
→ repairing
→ waiting_ci
→ ready_for_review
```

Terminal states:

```text
blocked
failed
cancelled
budget_exhausted
```

Гарантии:

- анализируется exact-head SHA рабочей ветки;
- учитываются GitHub checks и Railway commit statuses;
- максимум две repair-итерации;
- repair только внутри файлов исходного approved plan;
- второй branch/PR не создаётся;
- branch-head drift блокирует run;
- cancelled, timeout, network, permission и infrastructure failures не исправляются изменением product-кода;
- недостаточные evidence дают `evidence_insufficient`, а не слепой patch.

### 5.4 Bounded Actions logs

Реализован opt-in fallback для failed GitHub Actions jobs:

- exact-head jobs only;
- bounded количество jobs;
- bounded размер текста;
- очистка токенов и секретов;
- используется только при недостаточных check output/annotations;
- отсутствие `Actions: Read` должно завершаться fail-closed ошибкой, а не угадыванием.

### 5.5 Review Loop

Реализовано:

- чтение review submissions и threads существующего draft PR;
- code change запускается только для явного `CHANGES_REQUESTED`;
- обычные comments/questions не меняют код;
- изменение только approved-plan files;
- один bounded review repair commit;
- после commit run возвращается в exact-head CI;
- Autopilot не resolve-ит спорный thread автоматически;
- `APPROVED` не запускает merge.

### 5.6 Merge policy dry-run

Реализована только read-only оценка:

- exact-head CI state;
- branch freshness;
- PR mergeability;
- approved file scope;
- protected paths;
- deletion/rename/binary boundaries;
- changed-lines limit;
- актуальные requested changes;
- requirement manual approval.

Результат может быть:

```text
not_ready
ready_to_mark_ready
eligible
```

Даже `eligible` не выполняет merge.

## 6. Жёсткая граница безопасности

Autopilot не должен:

- писать в production/base branch;
- force-push;
- merge-ить PR;
- deploy-ить;
- выполнять shell на Railway host;
- менять `.env`, secrets, credentials или private keys;
- менять auth, billing, financial code, migrations или infrastructure без отдельной новой политики;
- менять собственные safety rules;
- расширять `allowed_paths` самостоятельно;
- делать бесконечные CI repairs;
- создавать второй PR для того же run;
- считать обычный review comment разрешением менять код.

Текущий режим:

```text
draft_pr_only=true
auto_merge=false
deployment=false
merge_policy=dry_run
```

## 7. Feature flags

Основные production flags:

```env
VELIA_DEVELOPER_ENABLED=true
VELIA_DEVELOPER_CODING_ENABLED=true
VELIA_DEVELOPER_WRITE_ENABLED=true

VELIA_DEVELOPER_AUTOPILOT_ENABLED=true
VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED=true
VELIA_DEVELOPER_AUTOPILOT_CI_ENABLED=true
VELIA_DEVELOPER_AUTOPILOT_CI_REPAIR_ENABLED=true
VELIA_DEVELOPER_AUTOPILOT_CI_LOGS_ENABLED=true
VELIA_DEVELOPER_AUTOPILOT_REVIEW_ENABLED=true
VELIA_DEVELOPER_AUTOPILOT_MERGE_POLICY_ENABLED=true
```

Владелец включил эти возможности, а Android Center показал их как активные. Не выводить реальные secrets или installation tokens.

В новой сессии проверять status endpoints и deployment, а не полагаться только на это утверждение.

## 8. Android Autopilot Center

Экран доступен из раздела инструментов/возможностей.

Он умеет:

- выбрать connected project;
- создать mission на паузе;
- указать mission name;
- задать allowed paths;
- задать max steps/files;
- активировать или приостановить mission;
- добавить task в queue;
- показать runs;
- показать CI attempts;
- показать Actions logs capability;
- показать Review Loop history;
- показать merge-policy recommendation и reasons;
- открыть draft PR.

В нём нет кнопок merge/deploy.

Последний проверенный UI commit:

```text
fdc7c9e151df0accf56c25d0655a584ad5ea7cdb
```

## 9. Controlled end-to-end acceptance

### 9.1 Fixture

В production существует dormant fixture:

```text
tests/test_velia_agent_coding_autopilot_controlled_repair_fixture.py
```

Он ничего не делает, пока в PR branch отсутствует:

```text
docs/velia-autopilot-controlled-repair-smoke.txt
```

Первая строка файла должна быть строго:

```text
VELIA_AUTOPILOT_REPAIR_OK
```

Опциональная вторая строка:

```text
review-note: initial
```

Workflow `VELIA Agent Core` теперь запускается при изменении этого exact docs-path. Fix находится в production commit:

```text
8497c3709a825c4f3cf14d63dc74db5dc5905ca8
```

### 9.2 Что ещё не принято

Полная единая цепочка пока не подтверждена реальным run:

```text
paused mission
→ queued task без выполнения
→ activation
→ autonomous draft PR
→ controlled failed CI
→ automatic repair commit
→ new green exact-head CI
→ explicit REQUEST_CHANGES
→ review repair commit
→ new green CI
→ merge-policy dry-run
→ no merge/deploy
```

До этого нельзя объявлять Autopilot полностью production-accepted.

### 9.3 Правила smoke

- repository: `SergeyTo95/deepalpha-bot`;
- base branch: фактическая production branch;
- `allowed_paths`: только `docs/`;
- задача создаёт только dedicated smoke-файл;
- первая строка намеренно неправильная;
- вторая строка `review-note: initial`;
- PR остаётся draft;
- smoke PR не merge-ить;
- после acceptance PR закрыть без merge;
- не использовать unrelated product files.

## 10. Ключевые PR и commits

| Область | PR / commit | Статус |
|---|---|---|
| Coding Agent v1 | `#392` / `a66957c1...` | production |
| Design Taste | `#393` / `4bcf1f34...` | production |
| Agent presentation backend | `#403` | production |
| Mobile command routing hotfix | `#404` | production |
| Autopilot Foundation | `#405` / `de243018...` | production |
| Coding Agent structured cards | backend `#407`, Android `#28` | production/develop |
| CI Watch + Repair | `#409` / `1df98ebe...` | production |
| Review Loop | `#410` / `c5a5f930...` | production |
| Merge-policy dry-run | `#411` / `478f53e9...` | production |
| Actions logs fallback | `#412` / `29171207...` | production |
| Controlled fixture | `#413` / `961aca34...` | production |
| Android Autopilot Center | Android `#29`, `#30` / `fdc7c9e1...` | develop, device opened |
| Smoke path-filter fix | `8497c370...` | production, Railway success |
| Economy v0.2 | `#439` | production |
| Video Standard 5s = 100 Credits | `#440` | production |
| Google Play backend billing | `#444` | production, external activation pending |
| USDT checkout v1 | `#445` | production, public checkout OFF |
| Gram admin + watch-only Treasury setup | `#446` | production |
| Gram + USDT Treasury Withdraw | `#447` | production, withdrawal flags OFF |
| Gram Treasury diagnostics | `#448` / `d718dfa7...` | production, Railway 4/4 success |
| Android Google Play billing | Android `#35` / `ac22e0a2...` | Draft, not merged |

Перед использованием SHA обязательно повторно проверить repository state.

## 11. Известные незавершённые ветки

Не использовать как основу:

```text
fix/velia-autopilot-controlled-smoke-trigger
```

Это stale branch без PR; production уже содержит соответствующий fix другим commit.

Также не merge-ить старые test/draft PR без отдельной проверки их актуальности и diff.

Для payment work не продолжать старые feature branches `#444–#448`; начинать новый fix от фактического production head.

## 12. Порядок следующей сессии

Если продолжается Autopilot:

1. Прочитать этот README и `docs/VELIA_NEW_CHAT_HANDOFF.md` через GitHub connector.
2. Проверить текущий backend production head и Railway statuses.
3. Проверить Android `develop` head и последний CI.
4. Не продолжать stale branch `fix/velia-autopilot-controlled-smoke-trigger`.
5. Провести один controlled Autopilot end-to-end acceptance по разделу 9.
6. Не merge-ить smoke PR.
7. После acceptance обновить README фактическими run id, PR, commits, CI attempts, cost и итогом.

Если продолжается Payments/Gram Treasury:

1. Прочитать этот README и `docs/VELIA_PAYMENTS_NEW_CHAT_HANDOFF.md`.
2. Проверить production head + Railway 4/4.
3. Проверить, что `#448` действительно merged/current.
4. В Telegram открыть `/admin → 💎 Gram Wallets → 🧾 Treasury diagnostics` и получить runtime evidence.
5. Исправить root cause `Treasury / payments: not configured`, не угадывая причину.
6. После реального successful Treasury setup подключить Gram deposit address к `velia-payment-worker`.
7. Затем поэтапно провести watcher/withdraw/incoming-USDT acceptance; public checkout до этого не включать.

## 13. Инженерный рабочий контракт

- Общаться с владельцем по-русски, прямо и практично.
- Работать как ведущий инженер, а не давать общие советы.
- Во время долгой работы регулярно писать статус: проверено, найдено, изменено, проверки, осталось, блокеры.
- Использовать GitHub connector для private/project repositories.
- Не придумывать CI, merge, deploy, Railway или device results.
- Проверять exact-head SHA.
- Backend production принимать только после success всех фактически затронутых Railway production services; для текущей архитектуры обычно проверять 4 сервиса: `deepalpha-bot`, `velyon-memory`, `velia-repowise`, `velia-payment-worker`.
- Не раскрывать secrets.
- Не менять safety boundaries ради прохождения теста.
- При failure читать конкретный code/log/runtime diagnostics и исправлять root cause.
- Не объявлять Autopilot принятым до полного controlled acceptance.
- Не объявлять payment channel live до реального end-to-end money acceptance.
- Для подготовленного payment PR владелец разрешил merge без повторного вопроса, если exact-head CI green, mergeable, branch fresh, review threads 0, Railway preview green и нет money/security blocker. Это не разрешение автоматически включать real-money flags.

## 14. Новый чат

Общий VELIA handoff:

```text
docs/VELIA_NEW_CHAT_HANDOFF.md
```

Отдельный актуальный handoff для платежей и Gram Treasury:

```text
docs/VELIA_PAYMENTS_NEW_CHAT_HANDOFF.md
```

Если новый чат продолжает текущую payment-задачу, первым сообщением отправить содержимое `docs/VELIA_PAYMENTS_NEW_CHAT_HANDOFF.md`.

## 15. VELIA Payments / Billing — состояние на 2026-08-09

### 15.1 Economy

Принята коммерческая модель VELIA Economy v0.2:

- Free: 100 Premium Credits/month + 50 welcome;
- Plus: $14.99 Store / $10.49 USDT, 1,200 Credits/month;
- Pro: $29.99 Store / $20.99 USDT, 3,000 Credits/month;
- top-ups: 100 / 250 / 800 / 2,000 / 5,000 / 10,000 Credits;
- crypto discount 30%;
- public paid-compute unit: VELIA Credits;
- paid Velyon Core — included/fair use;
- premium expensive compute расходует Credits.

Recurring Free grants и полноценное разделение subscription/purchased Credit buckets не считать завершёнными только по draft economy: runtime compatibility surface всё ещё использует aggregate `users.token_balance`.

### 15.2 Google Play

Backend `#444` merged: server-side catalog/verification/fulfillment boundary реализован.

Android `#35` готов по exact-head CI, но остаётся Draft и не merged.

Для live Google Play ещё нужны Play Console products, service account/Android Publisher credentials, Railway config, licensed/internal test и post-merge Android verification.

### 15.3 Direct USDT

Backend `#445` merged.

Phase-1 supported canonical rails:

```text
TRON/TRC20 USDT
Solana USDT
Gram/USDT Jetton
```

`velia-payment-worker` создан и успешно деплоится как отдельный Railway service с start command:

```text
python run_payment_worker.py
```

Worker watch-only и не имеет signing/private-key/send capability.

Public checkout должен оставаться OFF до controlled end-to-end acceptance.

### 15.4 Gram Treasury

Production admin custodial public address, показанный UI:

```text
UQARaLE231LslLaQtra38Z8G5DQAZBXm90_lQwHNuyqGpeo0
```

PR `#446` добавил safe watch-only Treasury setup, но реальная попытка production setup не завершилась: после Confirm + Refresh UI всё ещё показывает:

```text
Treasury / payments: — [not configured]
Mode: not configured
USDT on Gram: WAITING: set admin wallet as Treasury
```

Это текущий blocker. Не считать Treasury configured.

PR `#448` уже production и добавляет `🧾 Treasury diagnostics`, чтобы получить безопасное runtime evidence вместо догадок. Исторический schema-код подтверждает, что `seed_encrypted` объявлен nullable; гипотезу `NOT NULL` не принимать за факт без production diagnostics.

### 15.5 Treasury Withdraw

PR `#447` merged: admin-only Gram + USDT withdrawal flow реализован, но real-money gates должны оставаться OFF:

```env
VELIA_GRAM_TREASURY_WITHDRAW_ENABLED=false
VELIA_GRAM_TREASURY_USDT_WITHDRAW_ENABLED=false
```

Эти flags относятся к `deepalpha-bot`, не к `velia-payment-worker`.

После успешного Treasury setup нужен controlled mainnet acceptance:

```text
минимальный Gram withdrawal
→ on-chain tx + receive verification
→ затем минимальный USDT-on-Gram withdrawal
```

До этого не объявлять withdraw production-accepted.

### 15.6 Следующие обязательные шаги

1. Получить `🧾 Treasury diagnostics` из production Telegram admin.
2. Зафиксировать exact safe failure code/state.
3. Исправить root cause Treasury setup отдельным PR + regression test.
4. После green exact-head gates можно merge без повторного вопроса владельцу.
5. Подтвердить production deploy и реальный UI Treasury state.
6. Поставить тот же Gram public address в `VELIA_PAYMENT_TON_DEPOSIT_ADDRESS` worker-а.
7. Запустить сначала только Gram watcher при public checkout OFF и проверить реальный successful poll.
8. Провести минимальный Gram withdrawal test.
9. Провести минимальный USDT-on-Gram withdrawal test.
10. Перепроверить/закрыть gap controlled test-intent при `VELIA_USDT_CHECKOUT_ENABLED=false`.
11. Провести incoming USDT exactly-once acceptance.
12. Затем по одной сети принять TRON и Solana.
13. Только после этого обсуждать включение public USDT checkout.
14. Google Play вести отдельным activation-треком.

### 15.7 Secret boundary

Никогда не публиковать и не просить в чат:

- seed/mnemonic/private key;
- `MASTER_ENCRYPTION_KEY`;
- RPC API keys;
- Railway secret values;
- Google service-account JSON;
- bot token;
- GitHub installation token/private key.

Public receiving addresses не являются секретами.
