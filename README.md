title: DeepAlpha
emoji: 📈
colorFrom: blue
colorTo: purple
sdk: docker

# VELIA / DeepAlpha backend

> Внутренний инженерный README. Он описывает фактическую архитектуру, production-путь, безопасность, текущий статус Android/backend и обязательные acceptance-проверки. Не считать функцию готовой только потому, что код смержен: нужны exact-head CI, Railway deployment и реальный тест.

**Последнее обновление:** 2026-08-05  
**Backend repository:** `SergeyTo95/deepalpha-bot`  
**Android repository:** `SergeyTo95/deepalpha-android`  
**Production branch:** `feature/turbo-short-term-btc`  
**Public domain:** `https://deepalpha-ai.com`  
**Railway project:** `melodious-radiance`  
**Railway services:** `deepalpha-bot`, `velyon-memory`

## 1. Текущий подтверждённый статус

### Backend production

Текущий подтверждённый application baseline после VELIA Design Taste:

```text
4bcf1f3426fdc9ca244536c782b41a0ea64cc245
```

Для этого commit подтверждено:

- PR `#393` (`Add VELIA Design Taste layer`) закрыт как merged;
- Railway `deepalpha-bot` — `success`;
- Railway `velyon-memory` — `success`;
- Design Taste интегрирован без дополнительного Kimi-вызова;
- Coding Agent v1 уже находится в production из PR `#392`;
- read-only VELIA Developer продолжает работать через обычный чат.

### Реальный Android acceptance read-only Developer

На реальном устройстве подтверждён полный путь:

```text
Android ordinary chat
→ mobile SSE
→ VELIA Developer router
→ GitHub read-only retrieval
→ Kimi final answer
→ verified file:Lx-Ly citations
```

Последний успешный production-замер:

```text
Стоимость: $0.03721
Время: 47.1 s
```

Ответ корректно нашёл `services/velia_developer_chat_runtime_patch.py`, описал маршрутизацию, выбор проекта, fallback и `_developer_result`, привёл подтверждённые диапазоны строк и не выдумывал недоступный код.

### Coding Agent v1

Код, CI и Railway deployment подтверждены. GitHub App permissions и Railway flags, по сообщению владельца проекта, настроены. **Реальный write-smoke на пользовательском аккаунте ещё не выполнен. Это главная задача следующей сессии.**

До успешного smoke нельзя утверждать, что production-цепочка GitHub App write permissions → branch → commit → draft PR работает end-to-end.

### Design Taste layer

Код, tests, merge и Railway подтверждены. **Реальный frontend/UI план с активированным Taste Layer ещё нужно проверить.**

## 2. Что такое VELIA

VELIA — персональный ИИ-помощник и полноценная AI-среда, а не только экран чата.

Публичные названия:

- приложение и продукт: **VELIA**;
- помощник: **Velia / Велия**;
- публичное интеллектуальное ядро: **Velyon Core**;
- генерация изображений: **Velia Images / Velyon Core Images**;
- prediction-market модуль: **DeepAlpha**.

Исторически backend вырос из `deepalpha-bot`, поэтому в коде и инфраструктуре остаются DeepAlpha-названия. Новые пользовательские поверхности и AI-функции развиваются под брендом VELIA.

## 3. Репозитории и ветки

### Backend

```text
SergeyTo95/deepalpha-bot
```

Production/deploy branch:

```text
feature/turbo-short-term-btc
```

Нельзя считать `main` или другую ветку production без фактической проверки Railway source branch.

### Android

```text
SergeyTo95/deepalpha-android
```

Актуальные stacked PR на момент обновления:

- PR `#18` — `Add VELIA File Analyst attachments and secure image previews`;
  - base: `develop`;
  - head: `feature/velia-file-analyst`;
  - open, mergeable, не draft;
  - Android `0.9.1` (`versionCode 14`);
  - backend и CI подтверждены;
  - финальный real-device acceptance перед merge остаётся обязательным.
- PR `#22` — `Add VELIA Developer read-only Android mode`;
  - base: `feature/velia-file-analyst`;
  - head: `feature/velia-developer-readonly`;
  - open, mergeable, draft;
  - Android `0.10.1` (`versionCode 16`);
  - exact-head: `e1ad1e97bbe42ef13a43024673fd7c1e6f18214b`;
  - lint, unit tests, assembleDebug и APK artifact ранее прошли;
  - PR stacked поверх File Analyst и не должен бездумно merge-иться раньше базовой интеграции.

Backend-функции Fast Path, Coding Agent и Taste Layer серверные; отдельное APK-обновление для их базового ordinary-chat UX не требуется.

## 4. Высокоуровневая архитектура

```text
Android app
  └─ authenticated mobile API / SSE
       └─ run_web_process.py
            ├─ ordinary VELIA chat layers
            ├─ mobile streaming layer
            ├─ outer VELIA Developer chat router
            │    ├─ read-only Fast Path
            │    └─ guarded Coding Agent
            │          └─ optional Design Taste context
            ├─ Developer HTTP routes
            └─ schema bootstrap

VELIA Developer
  ├─ project / GitHub installation context
  ├─ GitHub App installation tokens
  ├─ read-only repository retrieval
  ├─ Kimi gateway
  ├─ verified citations
  └─ guarded write service for Coding Agent only
```

## 5. Bootstrap и ordinary-chat integration

Главная точка интеграции — `run_web_process.py`.

Критический порядок:

```text
ordinary chat patches
→ install_velia_chat_streaming(...)
→ install_velia_developer_chat(...)
→ setup_velia_mobile_streaming_route(...)
```

Developer router должен оставаться внешним generation layer. Он обязан перехватить repository-scoped запрос до того, как обычный streaming/generation путь сможет обойти GitHub tools.

Основные файлы:

```text
run_web_process.py
services/velia_developer_chat_runtime_patch.py
services/velia_mobile_streaming_service.py
services/velia_developer_routes.py
services/velia_developer_project_service.py
```

`ensure_velia_developer_chat_tables()` и coding schema bootstrap должны вызываться ровно один раз в web-process schema initialization.

## 6. VELIA Developer: read-only режим

### 6.1 Назначение

Read-only Developer отвечает на вопросы по подключённым приватным и публичным GitHub-репозиториям:

- найти функцию, route, service или bug;
- объяснить поток запроса;
- проверить архитектуру;
- назвать подтверждённые файлы и строки;
- выполнить безопасный code review без записи.

### 6.2 Ordinary-chat router

Основной файл:

```text
services/velia_developer_chat_runtime_patch.py
```

Router:

1. сохраняет исходный `generate_velia_chat_result`;
2. подменяет его outer wrapper;
3. читает последнее user message по `request_id`;
4. определяет repository scope;
5. загружает доступные Developer projects;
6. выбирает явно названный или привязанный к conversation project;
7. для обычного вопроса вызывает read-only Fast Path;
8. для явной команды изменения передаёт управление Coding Agent;
9. для нерепозиторного запроса вызывает исходный ordinary chat;
10. при сбое не должен придумывать ответ без кода.

Conversation binding хранится в:

```text
velia_developer_chat_contexts
```

### 6.3 Read-only Fast Path

Основной файл:

```text
services/velia_developer_fast_path_service.py
```

Fast Path заменил дорогой многошаговый model-driven tool loop.

Текущий подход:

1. сервер детерминированно извлекает terms/symbols из вопроса;
2. получает repository tree;
3. ранжирует пути без Kimi;
4. читает релевантные symbol-aware windows;
5. справедливо распределяет evidence budget между файлами;
6. делает один финальный Kimi-вызов;
7. второй вызов допускается только как bounded repair;
8. citations валидируются только внутри реально переданных строк;
9. одинаковый вопрос на том же repository state может использовать краткоживущий cache.

Ключевые ограничения:

```env
VELIA_DEVELOPER_MAX_MODEL_CALLS=2
VELIA_DEVELOPER_MAX_COST_USD=0.08
VELIA_DEVELOPER_EVIDENCE_CHARS=24000
VELIA_DEVELOPER_RESULT_CACHE_TTL_SECONDS=300
VELIA_DEVELOPER_FAST_REPAIR_RESERVE_USD=0.025
VELIA_DEVELOPER_FAST_REPAIR_OUTPUT_TOKENS=1024
```

Значения могут быть переопределены environment variables, поэтому перед расследованием стоимости проверять runtime config.

### 6.4 Реальная оптимизация стоимости

До Fast Path один архитектурный вопрос стоил примерно:

```text
$0.39672 / 225.1 s
```

После Fast Path и retrieval fixes:

```text
$0.04403 / 47.3 s
$0.03721 / 47.1 s
```

Это реальные Android measurements, а не synthetic benchmark.

### 6.5 Исправленные production-дефекты

В ходе acceptance были последовательно обнаружены и исправлены:

- `stream_network_error` — SSE worker закрывал socket без структурированного server error;
- `empty_200` — Kimi возвращал HTTP 200 без пригодного visible payload;
- `developer_tool_limit_reached` — legacy agent расходовал весь tool budget без финализации;
- неполные evidence windows — читалось начало файла вместо определения символа;
- evidence starvation — первый большой файл забирал весь context budget;
- `developer_cost_limit_reached` — repair оценивался как второй полный 2048-token вызов.

Нельзя возвращать старую архитектуру без соответствующих regression tests.

## 7. Mobile SSE resilience

Основной файл:

```text
services/velia_mobile_streaming_service.py
```

Требования:

- keepalive во время долгой repository операции;
- progress deltas до финального ответа;
- worker exception должен превращаться в SSE `error` с устойчивым code;
- socket нельзя молча закрывать, если клиент ещё подключён;
- progress text не должен сохраняться как финальный assistant message;
- перед финальным answer/error transient progress сбрасывается.

Android transport раньше отображал generic `stream_network_error`; после backend fixes конкретные server codes стали видимыми и пригодными для диагностики.

## 8. VELIA Coding Agent v1

### 8.1 Назначение

Coding Agent расширяет Developer от чтения до безопасного выполнения изменений:

```text
user change request
→ ordered plan
→ one explicit approval
→ isolated velia/... branch
→ sequential small tasks
→ one atomic commit per task
→ draft pull request
→ CI snapshot
→ suggestions
```

Основные файлы:

```text
services/velia_developer_coding_service.py
services/velia_developer_github_write_service.py
services/velia_developer_chat_runtime_patch.py
tests/test_velia_developer_coding_service.py
tests/test_velia_developer_coding_chat_gate.py
tests/test_velia_developer_coding_intent_classifier.py
tests/test_velia_developer_github_write_service.py
```

### 8.2 UX-команды

Явная команда изменения, например:

```text
В репозитории deepalpha-bot создай файл docs/example.md...
Исправь bug в ...
Добавь test для ...
Реализуй ...
```

должна вернуть план, но ничего не менять.

Подтверждение:

```text
Выполняй план
```

Статус:

```text
Статус
```

Отмена planned job:

```text
Отмени план
```

Read-only вопросы `где создаётся`, `найди`, `проверь`, `объясни` не должны ошибочно запускать write-контур.

### 8.3 Планирование

Plan stage:

- использует repository tree и несколько релевантных windows;
- делает один low-reasoning Kimi call;
- возвращает compact JSON;
- допускает от 1 до 6 small ordered steps;
- каждая задача должна быть отдельно committable;
- tests должны идти в том же или следующем шаге;
- план записывается в `velia_developer_coding_jobs`;
- пользователь видит summary, files, checks и suggestions;
- выполнение не начинается без явной команды подтверждения.

### 8.4 Execution

На каждом шаге Coding Agent:

1. публикует progress `Задача N/M`;
2. читает только разрешённые планом файлы;
3. формирует compact source context;
4. просит модель вернуть JSON operations;
5. сервер валидирует `replace/create/delete`;
6. `replace.old` обязан существовать ровно один раз;
7. операции преобразуются в атомарный Git tree commit;
8. commit записывается в рабочую ветку;
9. результат и стоимость сохраняются в job;
10. agent переходит к следующему шагу только после успешного commit.

После всех задач создаётся draft PR и считывается snapshot check-runs последнего commit.

### 8.5 Write safety boundary

Запись изолирована в:

```text
services/velia_developer_github_write_service.py
```

Обязательные гарантии:

- `VELIA_DEVELOPER_WRITE_ENABLED=true`;
- GitHub App `Contents: Read and write`;
- GitHub App `Pull requests: Read and write`;
- `Workflows` не требуется и должен оставаться без write access;
- запись только в branch prefix `velia/`;
- base/selected branch блокируется;
- ref update выполняется с `force=False`;
- secrets, `.env`, private keys, credentials и secret-like paths блокируются;
- `.github/workflows/*` блокируется по умолчанию;
- ограничены files и bytes на step;
- PR создаётся только с `draft=true`;
- merge и deploy functions в write-layer отсутствуют.

Coding Agent не должен самостоятельно merge-ить или deploy-ить изменения.

### 8.6 Cost caps

Рекомендуемые production limits:

```env
VELIA_DEVELOPER_CODING_PLAN_MAX_COST_USD=0.04
VELIA_DEVELOPER_CODING_MAX_COST_PER_STEP_USD=0.06
VELIA_DEVELOPER_CODING_MAX_JOB_COST_USD=0.24
```

Дополнительно:

```env
VELIA_DEVELOPER_CODING_ENABLED=true
VELIA_DEVELOPER_WRITE_ENABLED=true
VELIA_DEVELOPER_CODING_MAX_STEPS=5
VELIA_DEVELOPER_CODING_PATCH_ATTEMPTS=2
VELIA_DEVELOPER_CODING_PLAN_OUTPUT_TOKENS=1400
VELIA_DEVELOPER_CODING_STEP_OUTPUT_TOKENS=2400
VELIA_DEVELOPER_CODING_REPAIR_OUTPUT_TOKENS=1200
```

Не увеличивать limits без реального анализа billing и regression coverage.

## 9. VELIA Design Taste layer

### 9.1 Upstream

Адаптация основана на:

```text
Repository: Leonxlnx/taste-skill
Reviewed commit: e988add20dab0fa97d7a76781c48961c8184288e
License: MIT
```

Attribution хранится в:

```text
third_party/taste-skill/LICENSE
third_party/taste-skill/UPSTREAM.md
```

### 9.2 Почему не скопирован весь upstream

Upstream содержит большой набор skills, images, examples и экспериментальных правил. Полное копирование:

- увеличило бы repository size;
- раздуло бы prompt context;
- повысило бы стоимость;
- навязало бы несвязанные инструкции backend-задачам;
- создало бы конфликт с существующим Android/Web stack.

В VELIA перенесён compact context-aware layer.

### 9.3 Основные файлы

```text
services/velia_developer_taste_skill_service.py
skills/velia-design-taste/SKILL.md
tests/test_velia_developer_taste_skill_service.py
tests/test_velia_developer_taste_integration.py
```

### 9.4 Активация

Taste Layer должен активироваться только для задач, связанных с:

- frontend;
- UI/UX;
- web pages;
- redesign;
- Android/iOS/mobile interface;
- dashboard/product surfaces;
- typography, spacing, motion, visual hierarchy.

Backend-only, database, infrastructure и non-UI bugfix задачи должны обходить слой и не платить за лишний context.

### 9.5 Что добавляет слой

- `Design Read` по brief, audience и existing stack;
- три contextual dials: variance, motion, density;
- audit-first для существующего UI;
- сохранение текущего framework и design system;
- отдельные web, dashboard, Android, iOS и cross-platform правила;
- anti-slop проверки generic layouts, purple-gradient defaults, weak hierarchy и placeholder copy;
- dependency verification до import;
- accessibility, focus, contrast и semantic checks;
- responsive and safe-area checks;
- loading, empty, error, active и pressed states;
- reduced-motion behavior;
- pre-flight checklist до commit.

### 9.6 Что сознательно удалено из upstream

- обязательный GSAP для любой страницы;
- simulated Python randomness;
- жёсткий запрет конкретных icon libraries независимо от проекта;
- принудительная миграция stack;
- image-generation-only instructions;
- правила, противоречащие Material/Compose или существующему design system.

### 9.7 Стоимость

Taste Layer не добавляет новый `_model_call`. Он добавляет bounded guidance в уже существующие plan/step prompts Coding Agent.

Если после изменения число Kimi-вызовов увеличивается — это regression.

## 10. GitHub App configuration

GitHub App:

```text
VELIA Developer Beta
```

Repository permissions:

```text
Contents      → Read and write
Pull requests → Read and write
Workflows     → No access
```

После изменения permissions GitHub может потребовать approval updated permissions у существующей installation. Также installation должна иметь доступ к тестируемому repository.

Никогда не логировать:

- GitHub App private key;
- client secret;
- installation token;
- authorized user token;
- Railway secrets.

## 11. Railway configuration

Минимальные flags для Coding Agent:

```env
VELIA_DEVELOPER_CODING_ENABLED=true
VELIA_DEVELOPER_WRITE_ENABLED=true
```

Рекомендуемые cost variables:

```env
VELIA_DEVELOPER_CODING_PLAN_MAX_COST_USD=0.04
VELIA_DEVELOPER_CODING_MAX_COST_PER_STEP_USD=0.06
VELIA_DEVELOPER_CODING_MAX_JOB_COST_USD=0.24
```

Владелец сообщил, что permissions и variables добавлены. Это ещё не заменяет реальный smoke. Connector/README не подтверждают фактические Railway secret values.

## 12. Обязательный тест следующей сессии

### 12.1 Coding Agent safe write-smoke

Создать новый ordinary chat, выбрать `deepalpha-bot` и отправить:

```text
В репозитории deepalpha-bot создай файл docs/velia-coding-agent-smoke.md с кратким описанием VELIA Coding Agent. Сначала составь план и ничего не меняй без моего подтверждения.
```

Ожидается:

1. ответ `План VELIA Coding Agent`;
2. один небольшой step;
3. no branch / no commit / no PR до approval;
4. предложение написать `Выполняй план`.

После ручной проверки плана отправить:

```text
Выполняй план
```

Ожидается:

1. progress о создании branch;
2. branch начинается с `velia/`;
3. progress `Задача 1/1`;
4. один commit;
5. создан только `docs/velia-coding-agent-smoke.md`;
6. открыт draft PR;
7. merge и deploy не выполняются;
8. final response содержит branch, commit, draft PR, CI snapshot, cost и suggestions.

После smoke:

- открыть draft PR;
- проверить diff;
- проверить, что base branch — `feature/turbo-short-term-btc`;
- проверить отсутствие лишних файлов;
- дождаться CI;
- не merge-ить тестовый PR автоматически;
- после подтверждения можно закрыть PR и удалить ветку либо оставить как acceptance artifact.

### 12.2 Taste Layer plan-smoke

Для безопасной первой проверки достаточно plan-only запроса. Лучше использовать `deepalpha-android` и конкретный UI-screen после подключения repository:

```text
В репозитории deepalpha-android предложи небольшое улучшение визуальной иерархии экрана VELIA Developer, сохрани существующий Jetpack Compose и Material-подход. Сначала составь план, ничего не меняй.
```

Ожидается:

- Taste Layer активирован;
- plan содержит design read или эквивалентный design context;
- учитываются Android safe areas, readability, states и accessibility;
- нет предложения мигрировать framework;
- backend files не попадают в UI plan без причины;
- новых model calls сверх Coding Agent plan не появляется.

Выполнение UI-плана делать только после проверки файлов и scope.

## 13. Диагностика Coding Agent

Основные codes:

```text
developer_coding_disabled
developer_write_disabled
github_contents_write_permission_required
github_pull_requests_write_permission_required
developer_unsafe_write_branch
developer_protected_path
developer_coding_plan_empty
developer_coding_plan_cost_limit
developer_coding_step_cost_limit
developer_coding_job_cost_limit
developer_coding_path_outside_plan
developer_coding_replace_not_unique
developer_coding_patch_empty
developer_coding_patch_no_change
developer_coding_plan_missing
developer_coding_project_mismatch
```

Интерпретация:

- permission errors → проверить updated GitHub App installation approval;
- `developer_write_disabled` → проверить Railway flag и новый deployment;
- cost-limit → не повышать сразу; сначала измерить prompt size и actual usage;
- replace-not-unique → улучшить context/patch repair, не обходить server validation;
- protected-path → это security boundary, а не случайная ошибка;
- plan missing → approval отправлен не в conversation с активным planned job;
- project mismatch → conversation связан с другим project.

## 14. CI

Главный workflow:

```text
.github/workflows/velia-developer-readonly.yml
```

Несмотря на историческое имя, он проверяет и read-only Developer, и guarded Coding Agent, и Design Taste.

Обязательные группы проверок:

- Python compilation;
- GitHub read-only service tests;
- GitHub write service safety tests;
- Fast Path tests;
- Coding Agent planning/execution tests;
- coding intent classifier;
- ordinary-chat router gate;
- Taste Layer activation/bypass/integration;
- streaming resilience;
- secret handling;
- branch/write boundaries;
- cost contracts;
- bootstrap order;
- no generated bytecode.

Также используется общий runtime smoke:

```text
TON Wallet Runtime Smoke
```

Нельзя merge-ить product change только потому, что focused tests зелёные; exact-head workflow status должен быть `success`.

## 15. Deployment acceptance

После merge backend change:

1. зафиксировать merge commit SHA;
2. проверить combined status именно этого commit;
3. дождаться:
   - `melodious-radiance - deepalpha-bot = success`;
   - `melodious-radiance - velyon-memory = success`;
4. не принимать только memory-service за успешный backend deploy;
5. затем выполнить внешний/API или real-device acceptance;
6. не утверждать, что функция работает, до последнего шага.

## 16. История ключевых VELIA Developer изменений

| PR | Production commit | Результат |
|---|---|---|
| `#383` | `0e2330f65b536c666db9b679371fac59c633cdc7` | ordinary chat может использовать VELIA Developer |
| `#385` | `d090d94de1530423a3a97c9a45afc631e78fc4b1` | SSE resilience и structured worker errors |
| `#387` | `05f3f7d8ac034b87df4178da02601454e206550a` | `empty_200` recovery, low reasoning for tool choice |
| `#388` | `91bf15e9d28e46844314c9f4c9cf2aa169350d28` | forced finalization и tool-budget controls |
| `#389` | `d0b42799c1c2eae45fa5e268c80031ccac49b327` | Fast Path v1, one-call default, cache and cost cap |
| `#390` | `5cdac290d360c3ffaecfa2630b96012b88db1f48` | symbol-aware retrieval и fair evidence packing |
| `#391` | `bfb5ceb16d1848463e394a73fe8d2a3046767b60` | compact repair budget below `$0.08` |
| `#392` | `a66957c1a0f60f1a73a2f6bff5ed63f9ae5b1405` | guarded VELIA Coding Agent v1 |
| `#393` | `4bcf1f3426fdc9ca244536c782b41a0ea64cc245` | context-aware Design Taste layer |

## 17. Known open gates and risks

### Immediate gates

- real GitHub write-smoke Coding Agent;
- real draft PR creation;
- verify actual installed GitHub App permissions end-to-end;
- real Taste Layer plan on Android/Web UI task;
- decide lifecycle of test branch/PR after smoke.

### Android gates

- PR `#18` real-device File Analyst acceptance;
- resolve stacked integration before PR `#22` merge;
- do not claim current Android `develop/main` includes these features until merge is verified.

### File Analyst prior review risks

Two previously identified P2 issues must be re-verified before declaring File Analyst broadly production-ready:

- attachment tombstone resurrection;
- DB connection retry during reconciliation.

Не считать их исправленными без нового code/CI evidence.

### Coding Agent v1 limitations by design

- no merge;
- no deploy;
- no shell/command runner;
- no direct production branch writes;
- no workflow writes by default;
- CI snapshot после PR может быть `pending`;
- сложный multi-step job ограничен total cost и планом;
- server validates text patch operations, но реальный quality acceptance всё равно требует review diff и CI.

## 18. Инженерный рабочий контракт

При продолжении разработки:

- общаться с владельцем по-русски, прямо и практично;
- работать как ведущий инженер, а не выдавать общие советы;
- во время долгой работы регулярно писать короткий статус;
- фиксировать: что проверено, найдено, изменено, какие проверки идут, что осталось, блокеры;
- использовать GitHub connector для private repositories;
- не придумывать результаты CI, merge, deploy или device test;
- защищать merge exact-head SHA;
- не разрешать review threads без анализа;
- не merge-ить unrelated PR;
- не писать secrets в код, logs, README или chat;
- production status проверять по merge commit;
- после backend deploy проводить real-device acceptance;
- при ошибке расследовать конкретный code, а не скрывать его generic сообщением;
- сохранять cost caps и read-only/write security boundaries.

## 19. Новый чат

Полный handoff-промт находится здесь:

```text
docs/VELIA_NEW_CHAT_HANDOFF.md
```

В новом чате сначала отправить содержимое этого файла, затем попросить ассистента прочитать текущий README и проверить актуальное состояние GitHub/CI/Railway перед любыми изменениями.
