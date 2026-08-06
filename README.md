title: DeepAlpha
emoji: 📈
colorFrom: blue
colorTo: purple
sdk: docker

# VELIA / DeepAlpha backend

> Внутренний инженерный README. Источник фактического статуса backend, Android-клиента, VELIA Developer, Coding Agent и Coding Autopilot. Не считать функцию работающей только потому, что код написан или смержен: нужны exact-head CI, deployment и реальный acceptance.

**Последнее обновление:** 2026-08-06  
**Backend:** `SergeyTo95/deepalpha-bot`  
**Android:** `SergeyTo95/deepalpha-android`  
**Backend production branch:** `feature/turbo-short-term-btc`  
**Android integration branch:** `develop`  
**Public backend:** `https://deepalpha-ai.com`  
**Railway project:** `melodious-radiance`  
**Railway services:** `deepalpha-bot`, `velyon-memory`

## 1. Актуальный подтверждённый baseline

### Backend production

Последний проверенный production commit на момент обновления:

```text
8497c3709a825c4f3cf14d63dc74db5dc5905ca8
```

Commit добавляет controlled Autopilot smoke-файл в path-filter workflow `VELIA Agent Core`.

Для commit подтверждено:

- Railway `deepalpha-bot` — `success`;
- Railway `velyon-memory` — `success`;
- controlled acceptance-файл теперь реально запускает Agent Core CI;
- production runtime Autopilot не выполняет merge или deployment.

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

## 2. Что такое VELIA

VELIA — персональный ИИ-помощник и AI-среда, а не только чат.

Публичные названия:

- продукт и приложение: **VELIA**;
- помощник: **Velia / Велия**;
- интеллектуальное ядро: **Velyon Core**;
- генерация изображений: **Velia Images / Velyon Core Images**;
- prediction-market модуль: **DeepAlpha**.

Backend исторически вырос из `deepalpha-bot`, поэтому старые DeepAlpha-названия в инфраструктуре и коде допустимы. Новые пользовательские AI-возможности развиваются как VELIA.

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

Перед использованием SHA обязательно повторно проверить repository state.

## 11. Известные незавершённые ветки

Не использовать как основу:

```text
fix/velia-autopilot-controlled-smoke-trigger
```

Это stale branch без PR; production уже содержит соответствующий fix другим commit.

Также не merge-ить старые test/draft PR без отдельной проверки их актуальности и diff.

## 12. Порядок следующей сессии

1. Прочитать этот README и `docs/VELIA_NEW_CHAT_HANDOFF.md` через GitHub connector.
2. Проверить текущий backend production head и оба Railway statuses.
3. Проверить Android `develop` head и последний CI.
4. Не продолжать stale branch `fix/velia-autopilot-controlled-smoke-trigger`.
5. Провести один controlled Autopilot end-to-end acceptance по разделу 9.
6. Не merge-ить smoke PR.
7. После acceptance обновить README фактическими run id, PR, commits, CI attempts, cost и итогом.
8. Только затем обсуждать следующий найденный пользователем GitHub-проект и его интеграцию в VELIA.

## 13. Инженерный рабочий контракт

- Общаться с владельцем по-русски, прямо и практично.
- Работать как ведущий инженер, а не давать общие советы.
- Во время долгой работы регулярно писать статус: проверено, найдено, изменено, проверки, осталось, блокеры.
- Использовать GitHub connector для private/project repositories.
- Не придумывать CI, merge, deploy, Railway или device results.
- Проверять exact-head SHA.
- Backend production принимать только после обоих Railway `success`.
- Не раскрывать secrets.
- Не менять safety boundaries ради прохождения теста.
- При failure читать конкретный code/log и исправлять root cause.
- Не объявлять Autopilot принятым до полного controlled acceptance.

## 14. Новый чат

Полный handoff-промт:

```text
docs/VELIA_NEW_CHAT_HANDOFF.md
```

Первым сообщением нового чата отправить содержимое этого файла. Затем дать ссылку или название найденного GitHub-проекта, который предлагается добавить в VELIA.
