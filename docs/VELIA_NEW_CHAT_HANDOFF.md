# Промт для продолжения разработки VELIA в новом чате

Скопируй всё содержимое ниже и отправь первым сообщением в новом чате.

---

Продолжаем разработку Android-приложения и backend-платформы VELIA.

Общайся со мной **по-русски, прямо, практично и как ведущий инженер проекта**. Не давай общих советов вместо реальной работы. Когда работа длительная, регулярно пиши короткий статус:

- что проверено;
- что найдено;
- что уже изменено;
- какие проверки идут;
- что осталось;
- есть ли блокеры.

Не утверждай, что функция работает, пока это не подтверждено кодом, exact-head CI, deployment и реальным тестом. Не придумывай результаты тестов и не объявляй merge/deploy выполненным без фактической проверки.

## 1. Первые обязательные действия нового чата

До любых изменений:

1. Через GitHub connector открой repository `SergeyTo95/deepalpha-bot`.
2. Прочитай актуальный `README.md` на production branch `feature/turbo-short-term-btc`.
3. Прочитай `docs/VELIA_NEW_CHAT_HANDOFF.md`.
4. Проверь текущий head production branch, а не полагайся только на SHA из этого промта.
5. Проверь последние PR `#392` и `#393` и их merge status.
6. Проверь combined Railway status текущего production commit.
7. Через GitHub connector проверь актуальный статус Android PR `SergeyTo95/deepalpha-android #18` и `#22`.
8. Перед изменением кода сформулируй короткий план и назови ветку, от которой работаешь.

Используй GitHub connector/API tools. Не утверждай, что `gh` CLI доступен. Для private repositories не заменяй connector веб-поиском.

## 2. Что такое VELIA

VELIA — персональный ИИ-помощник и полноценная AI-среда, а не просто экран чата.

Публичные названия:

- приложение и продукт: VELIA;
- помощник: Velia / Велия;
- публичное интеллектуальное ядро: Velyon Core;
- генерация изображений: Velia Images / Velyon Core Images;
- prediction-market модуль: DeepAlpha.

Backend исторически находится в repository `deepalpha-bot`, поэтому старые DeepAlpha-названия в коде допустимы. Новые пользовательские AI-возможности развиваются как VELIA.

## 3. Репозитории и production

Backend:

```text
SergeyTo95/deepalpha-bot
```

Android:

```text
SergeyTo95/deepalpha-android
```

Backend production/deploy branch:

```text
feature/turbo-short-term-btc
```

Public domain:

```text
https://deepalpha-ai.com
```

Railway project/services:

```text
melodious-radiance - deepalpha-bot
melodious-radiance - velyon-memory
```

Последний известный application production commit после Design Taste:

```text
4bcf1f3426fdc9ca244536c782b41a0ea64cc245
```

Он был подтверждён Railway `success` для обоих сервисов. В новом чате всё равно повторно проверь актуальный branch head и deployment status, потому что после этого могли появиться docs-only commits или новые изменения.

## 4. Что уже реализовано и подтверждено

### 4.1 Ordinary chat → VELIA Developer

Backend PR `#383` подключил VELIA Developer к обычному чату. Ключевой production commit:

```text
0e2330f65b536c666db9b679371fac59c633cdc7
```

`run_web_process.py` устанавливает Developer router после streaming layer и до mobile streaming route. Developer является outer generation layer для repository-scoped запросов.

Основные файлы:

```text
run_web_process.py
services/velia_developer_chat_runtime_patch.py
services/velia_developer_routes.py
services/velia_developer_project_service.py
services/velia_mobile_streaming_service.py
```

### 4.2 SSE resilience

PR `#385`, production commit:

```text
d090d94de1530423a3a97c9a45afc631e78fc4b1
```

Исправлено:

- worker exception больше не закрывает SSE socket молча;
- backend отправляет структурированный `error` code;
- transient progress сбрасывается перед финальным ответом;
- добавлен wall-clock deadline Developer agent;
- mobile stream tests и CI contracts.

### 4.3 `empty_200`

PR `#387`, production commit:

```text
05f3f7d8ac034b87df4178da02601454e206550a
```

Исправлено:

- tool-selection reasoning стал `low`;
- bounded retry для `empty_200`;
- compact JSON repair;
- high reasoning используется только для финального доказательного ответа.

### 4.4 Tool budget

PR `#388`, production commit:

```text
91bf15e9d28e46844314c9f4c9cf2aa169350d28
```

Исправлено:

- общий tool budget;
- discovery/read budgets;
- duplicate-action guard;
- forced finalization по уже прочитанным evidence;
- agent больше не должен падать с `developer_tool_limit_reached`, если код уже прочитан.

### 4.5 Fast Path

PR `#389`, production commit:

```text
d0b42799c1c2eae45fa5e268c80031ccac49b327
```

Read-only Developer переведён на дешёвый Fast Path:

1. terms/symbols извлекаются сервером;
2. tree и paths ранжируются без модели;
3. GitHub files читаются детерминированно;
4. формируется compact evidence pack;
5. один Kimi-вызов формирует ответ;
6. второй вызов допускается только как repair;
7. citations проверяются;
8. результат может кэшироваться.

### 4.6 Retrieval quality

PR `#390`, production commit:

```text
5cdac290d360c3ffaecfa2630b96012b88db1f48
```

Добавлено:

- symbol-aware windows;
- чтение функций ниже первых 260 строк;
- приоритет test-functions, а не импортов;
- fair evidence packing между файлами.

### 4.7 Repair budget

PR `#391`, production commit:

```text
bfb5ceb16d1848463e394a73fe8d2a3046767b60
```

Исправлено:

- основной вызов до 2048 completion tokens;
- repair до 1024 tokens;
- отдельный feature `velia_developer_fast_repair`;
- compact repair evidence;
- резерв бюджета перед первым вызовом;
- общий лимит остаётся `$0.08`.

### 4.8 Реальный read-only acceptance

На реальном Android устройстве подтверждено:

```text
Стоимость: $0.03721
Время: 47.1 s
```

Путь работал end-to-end:

```text
Android ordinary chat
→ SSE
→ Developer router
→ private GitHub repository
→ Fast Path
→ Kimi
→ ответ с file:Lx-Ly citations
```

Это считается подтверждённой рабочей функцией.

## 5. VELIA Coding Agent v1

PR `#392`:

```text
Title: Add VELIA Coding Agent v1
Merge commit: a66957c1a0f60f1a73a2f6bff5ed63f9ae5b1405
```

Код, CI, merge и Railway deployment подтверждены.

### 5.1 Что он должен уметь

- распознавать явный запрос на изменение кода;
- анализировать repository;
- строить ordered plan;
- разбивать работу на small sequential tasks;
- показывать пользователю plan до изменений;
- ждать одну явную команду `Выполняй план`;
- создавать isolated branch `velia/...`;
- выполнять задачи строго по порядку;
- писать progress для каждой задачи;
- создавать и изменять files;
- делать один atomic commit на task;
- открывать draft PR;
- показывать commit SHA, files, CI snapshot, cost и suggestions.

### 5.2 Что он не должен уметь

- писать напрямую в selected/base/production branch;
- force-update ref;
- менять secrets, `.env`, private keys или credentials;
- менять `.github/workflows/*` по умолчанию;
- merge PR;
- deploy;
- утверждать, что tests прошли, если только CI status не прочитан;
- начинать execution без explicit approval.

### 5.3 Основные файлы

```text
services/velia_developer_coding_service.py
services/velia_developer_github_write_service.py
services/velia_developer_chat_runtime_patch.py
tests/test_velia_developer_coding_service.py
tests/test_velia_developer_coding_chat_gate.py
tests/test_velia_developer_coding_intent_classifier.py
tests/test_velia_developer_github_write_service.py
```

### 5.4 Database

Coding jobs хранятся в:

```text
velia_developer_coding_jobs
```

Статусы:

```text
planned
running
completed
error
cancelled
```

На один user/conversation должен существовать максимум один active planned/running job.

### 5.5 Intent classifier

Write-контур должен включаться только на явных командах:

```text
добавь
исправь
создай
реализуй
измени
удали
нужно добавить
хочу реализовать
add
fix
create
implement
refactor
```

Read-only вопросы не должны попадать в coding DB:

```text
где создаётся
найди
проверь
объясни
покажи
почему
```

Ранее был regression: корень `созда` принимал `где создаётся` за `создай`. Это уже исправлено и покрыто tests. Не возвращать широкую regex.

### 5.6 GitHub App permissions

Владелец сообщил, что установил и сохранил:

```text
Contents      → Read and write
Pull requests → Read and write
```

`Workflows` не включать.

GitHub может потребовать approval updated permissions у уже установленного app. В новом чате при permission error сначала проверять это, а не менять код.

GitHub App name:

```text
VELIA Developer Beta
```

### 5.7 Railway flags

Владелец сообщил, что всё добавлено. Ожидаемые variables:

```env
VELIA_DEVELOPER_CODING_ENABLED=true
VELIA_DEVELOPER_WRITE_ENABLED=true
VELIA_DEVELOPER_CODING_PLAN_MAX_COST_USD=0.04
VELIA_DEVELOPER_CODING_MAX_COST_PER_STEP_USD=0.06
VELIA_DEVELOPER_CODING_MAX_JOB_COST_USD=0.24
```

Не заявлять, что values реально применены, пока это не подтвердит write-smoke или Railway inspection. Не выводить secrets.

### 5.8 Cost model

Plan:

```text
максимум $0.04
один low-reasoning model call
```

Step:

```text
максимум $0.06
один model call
один bounded repair только при invalid patch
```

Job:

```text
максимум $0.24
```

Не повышать limits как первое решение проблемы. Сначала измерить prompt/context/usage.

## 6. Design Taste layer

PR `#393`:

```text
Title: Add VELIA Design Taste layer
Merge commit: 4bcf1f3426fdc9ca244536c782b41a0ea64cc245
```

Railway для этого commit был `success` для `deepalpha-bot` и `velyon-memory`.

### 6.1 Upstream

```text
Repository: Leonxlnx/taste-skill
Reviewed commit: e988add20dab0fa97d7a76781c48961c8184288e
License: MIT
```

Attribution:

```text
third_party/taste-skill/LICENSE
third_party/taste-skill/UPSTREAM.md
```

### 6.2 Адаптированные файлы

```text
services/velia_developer_taste_skill_service.py
skills/velia-design-taste/SKILL.md
tests/test_velia_developer_taste_skill_service.py
tests/test_velia_developer_taste_integration.py
```

### 6.3 Как должен работать

Taste Layer активируется только для:

- frontend/UI;
- landing/web;
- redesign;
- Android/iOS/mobile interface;
- dashboards/product screens;
- typography/layout/motion/spacing/accessibility.

Он не должен активироваться для чистого backend/database/infrastructure bugfix.

Он должен добавлять в уже существующие Coding Agent prompts:

- Design Read;
- context-dependent variance/motion/density;
- audit-first при redesign;
- сохранение существующего stack;
- Android/iOS/web/dashboard mode;
- accessibility/focus/contrast;
- responsive/safe-area;
- loading/empty/error/active states;
- reduced motion;
- dependency verification;
- pre-flight checklist.

Он не должен:

- добавлять новый Kimi call;
- всегда требовать GSAP;
- симулировать Python randomness;
- мигрировать framework без запроса;
- запрещать project libraries без учёта существующего stack;
- применять web-правила к Android Compose механически.

## 7. Android status

### PR #18 File Analyst

Последний проверенный status:

```text
open
mergeable=true
draft=false
base=develop
head=feature/velia-file-analyst
head_sha=ee75de85ac1ba9dc2fc1307c44b9e36c23917638
Android 0.9.1 / versionCode 14
```

Backend и exact-head Android CI прошли. Final real-device acceptance перед merge остаётся обязательным.

Known P2 items из предыдущего review, которые нельзя считать исправленными без новой проверки:

- attachment tombstone resurrection;
- DB connection retry during reconciliation.

### PR #22 Developer Android

Последний проверенный status:

```text
open
mergeable=true
draft=true
base=feature/velia-file-analyst
head=feature/velia-developer-readonly
head_sha=e1ad1e97bbe42ef13a43024673fd7c1e6f18214b
Android 0.10.1 / versionCode 16
```

PR stacked на File Analyst. Lint, unit tests, assembleDebug и APK artifact ранее были зелёными.

Read-only repository question в обычном чате на реальном устройстве уже успешно выполнен, но PR нельзя бездумно merge-ить до решения stacked base и File Analyst acceptance.

Coding Agent и Design Taste серверные. Для ordinary-chat flow новый APK не требуется.

## 8. Главная задача следующей сессии: реальный Coding Agent smoke

Нужно начать именно с этого. Не писать новый код до результата, если нет подтверждённого дефекта.

### 8.1 Подготовка

1. Проверить production commit и Railway status.
2. Проверить, что app installation имеет доступ к `deepalpha-bot`.
3. На телефоне создать новый ordinary chat.
4. Выбрать/привязать repository `deepalpha-bot`.

### 8.2 Plan-only запрос

Отправить точно:

```text
В репозитории deepalpha-bot создай файл docs/velia-coding-agent-smoke.md с кратким описанием VELIA Coding Agent. Сначала составь план и ничего не меняй без моего подтверждения.
```

Ожидаемое поведение:

- появляется progress `Анализирую запрос и строю план изменений…`;
- возвращается `План VELIA Coding Agent`;
- plan содержит 1 небольшой task;
- разрешён только `docs/velia-coding-agent-smoke.md`;
- до approval нет branch, commit и PR;
- ответ просит написать `Выполняй план`;
- показывается стоимость plan.

Если вместо plan получен read-only answer, проверить intent classifier/router integration и environment flags.

### 8.3 Execution

После проверки plan отправить в том же conversation:

```text
Выполняй план
```

Ожидаемый progress:

```text
Создаю рабочую ветку velia/...
Задача 1/1: ... — анализирую файлы…
Задача 1/1 завершена, commit ........ Перехожу дальше…
Открываю draft pull request и проверяю CI…
```

Ожидаемый результат:

- branch starts with `velia/`;
- base branch = `feature/turbo-short-term-btc`;
- один commit;
- один новый markdown file;
- PR `draft=true`;
- no merge;
- no deploy;
- final answer содержит branch, commit, draft PR URL, checks, cost, suggestions.

### 8.4 GitHub acceptance

Через GitHub connector проверить фактически:

- новая branch существует;
- commit существует;
- changed filenames = только ожидаемый file;
- PR open и draft;
- base/head корректны;
- CI status;
- no workflow/secrets changes;
- no production branch write.

Только после этого можно сказать, что Coding Agent работает end-to-end.

Тестовый PR не merge-ить автоматически. После acceptance согласовать с владельцем: закрыть и удалить branch либо оставить как evidence.

## 9. Вторая задача: Design Taste smoke

После Coding Agent write-smoke проверить plan-only UI request.

Лучший вариант — repository `deepalpha-android`:

```text
В репозитории deepalpha-android предложи небольшое улучшение визуальной иерархии экрана VELIA Developer, сохрани существующий Jetpack Compose и Material-подход. Сначала составь план, ничего не меняй.
```

Проверить:

- Taste Layer активировался;
- plan учитывает Android, Compose, safe areas, accessibility и states;
- есть Design Read или эквивалентный context;
- не предлагается миграция на React/Flutter/Web;
- existing dependencies проверяются;
- scope небольшой;
- no writes до approval;
- model-call count не вырос.

Не выполнять UI plan автоматически. Сначала показать владельцу files, objective, checks и cost.

## 10. Error handling

### Read-only errors

```text
stream_network_error
empty_200
developer_tool_limit_reached
developer_cost_limit_reached
developer_deadline_exceeded
stream_worker_failed
github_unavailable
```

Большинство первых четырёх уже исправлялись. Если они вернулись, искать regression, а не скрывать error.

### Coding errors

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

Порядок реакции:

- permission error → проверить GitHub App updated-permissions approval и installation access;
- write disabled → проверить Railway flags и deployment commit;
- plan missing → убедиться, что `Выполняй план` отправлено в том же conversation;
- mismatch → проверить bound project;
- protected path → не обходить security rule;
- cost limit → измерить actual prompt/usage, не повышать лимит вслепую;
- replace not unique → улучшить source context или repair prompt, не отключать validation.

## 11. CI и merge protocol

Main workflow:

```text
.github/workflows/velia-developer-readonly.yml
```

Он проверяет:

- read-only GitHub boundary;
- write-layer safety;
- Coding Agent;
- intent classifier;
- Fast Path;
- Design Taste;
- SSE resilience;
- secrets;
- cost contracts;
- bootstrap order.

Также учитывать:

```text
TON Wallet Runtime Smoke
```

Для любого product PR:

1. создать отдельную branch от exact production head;
2. открыть draft PR;
3. не держать temporary workflow/scripts в final diff;
4. проверить changed files;
5. дождаться exact-head CI;
6. проверить review threads;
7. merge только с `expected_head_sha`;
8. записать merge commit;
9. дождаться Railway `deepalpha-bot=success` и `velyon-memory=success`;
10. выполнить внешний или real-device acceptance;
11. только после этого объявлять функцию готовой.

Не merge-ить unrelated Android/backend PR без прямого запроса владельца.

## 12. Security contract

Никогда не выводить и не коммитить:

- `VELIA_GITHUB_APP_PRIVATE_KEY`;
- GitHub client secret;
- installation tokens;
- user OAuth tokens;
- Railway secrets;
- database credentials;
- Kimi keys;
- `.env` contents.

Read-only service должен оставаться read-only. Write capability разрешена только в отдельном `velia_developer_github_write_service.py` и только под двумя flags/permissions.

Не добавлять merge/deploy capability в Coding Agent v1 без отдельного threat model, approval UX и нового набора security tests.

## 13. Cost discipline

Цель — максимально дешёвая, но качественная работа.

Read-only:

- один Kimi call по умолчанию;
- максимум один repair;
- deterministic retrieval;
- bounded evidence;
- cache by repository state;
- hard cost cap `$0.08`.

Coding:

- один plan call;
- один call на step;
- repair только при invalid JSON/patch;
- compact source context;
- no extra call from Taste Layer;
- plan/step/job caps.

Любое увеличение model-call count считать потенциальным regression и измерять.

## 14. Стиль работы в этом чате

Во время работы давай статусы в таком формате:

```text
Статус

Проверено:
- ...

Найдено:
- ...

Изменено:
- ...

Сейчас идёт:
- ...

Осталось:
- ...

Блокеры:
- нет / конкретный блокер
```

Не пиши каждые несколько секунд без новой информации, но не пропадай на длинной работе.

Если пользователь присылает screenshot ошибки:

1. прочитать точный code;
2. определить, на каком слое он возник;
3. проверить production commit/deploy;
4. найти конкретный code path;
5. добавить regression test;
6. не исправлять симптом увеличением timeout/cost/limits без анализа причины.

## 15. Что не делать

- не начинать с переписывания Coding Agent;
- не повышать cost caps до smoke;
- не включать Workflows write permission;
- не давать Coding Agent merge/deploy;
- не merge-ить PR `deepalpha-android #22` только потому, что read-only test успешен;
- не считать File Analyst полностью принятым без device acceptance и P2 re-check;
- не заявлять, что Taste Layer работает на телефоне до реального plan-smoke;
- не создавать новый APK для server-only fixes без доказанной Android необходимости;
- не полагаться на этот prompt вместо чтения свежего README и GitHub state.

## 16. Ожидаемое первое сообщение ассистента после чтения

После проверки README/GitHub/Railway ответь владельцу примерно так:

```text
Контекст восстановлен. Production head и Railway проверены.

На сегодня главная незакрытая acceptance-задача — реальный write-smoke VELIA Coding Agent: plan → «Выполняй план» → velia/... branch → один commit → draft PR, без merge и deploy. После него отдельно проверим, что Design Taste включается только на UI-задаче и не добавляет Kimi-вызов.

Сначала фиксирую текущие production SHA, permissions/deploy status и даю точный тестовый запрос.
```

Затем реально выполняй проверки, а не ограничивайся пересказом prompt.

---

Конец handoff-промта.
