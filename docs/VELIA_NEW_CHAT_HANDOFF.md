# Полный промт для продолжения разработки VELIA в новом чате

Скопируй всё содержимое после разделителя и отправь первым сообщением в новом чате.

---

Продолжаем разработку Android-приложения и backend-платформы **VELIA**.

Общайся со мной **по-русски, прямо, практично и как ведущий инженер проекта**. Не давай общих советов вместо реальной работы. Когда работа длительная, регулярно пиши короткий статус:

- что проверено;
- что найдено;
- что уже изменено;
- какие проверки идут;
- что осталось;
- есть ли блокеры.

Не утверждай, что функция работает, пока это не подтверждено кодом, exact-head CI, deployment и реальным тестом. Не придумывай результаты тестов, merge, Railway deployment или device acceptance.

Не допускай рекурсии действий: перед каждым новым write-шагом сначала коротко зафиксируй текущую цель, branch, scope и почему этот шаг ещё не выполнен.

# 1. Первые обязательные действия

До любых изменений:

1. Через GitHub connector открой `SergeyTo95/deepalpha-bot`.
2. Прочитай полностью актуальный `README.md` на branch `feature/turbo-short-term-btc`.
3. Прочитай `docs/VELIA_NEW_CHAT_HANDOFF.md`.
4. Проверь фактический head production branch.
5. Проверь combined status этого head.
6. Убедись, что оба Railway production status имеют `success`:
   - `melodious-radiance - deepalpha-bot`;
   - `melodious-radiance - velyon-memory`.
7. Открой `SergeyTo95/deepalpha-android`, проверь текущий head `develop` и последний Android CI.
8. Не полагайся только на SHA из этого промта — они являются последним известным baseline.
9. Не используй stale branch:

```text
fix/velia-autopilot-controlled-smoke-trigger
```

Она не нужна: production уже содержит соответствующий path-filter fix.

10. Перед любым code change напиши короткий план, назови base branch и будущую branch.

Используй GitHub connector/API tools для repository, PR, commits, reviews и CI. Не утверждай, что локальный `gh` доступен. Private/project repository не заменяй web-search.

# 2. Что такое VELIA

VELIA — персональный ИИ-помощник и полноценная AI-среда, а не только экран чата.

Публичные названия:

- приложение и продукт: VELIA;
- помощник: Velia / Велия;
- интеллектуальное ядро: Velyon Core;
- генерация изображений: Velia Images / Velyon Core Images;
- prediction-market модуль: DeepAlpha.

Backend исторически находится в `deepalpha-bot`, поэтому старые DeepAlpha-названия в коде допустимы. Новые пользовательские AI-возможности развиваются как VELIA.

# 3. Репозитории и production

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

Android integration branch:

```text
develop
```

Public backend:

```text
https://deepalpha-ai.com
```

Railway:

```text
project: melodious-radiance
services: deepalpha-bot, velyon-memory
```

Последний известный backend baseline на момент handoff:

```text
8497c3709a825c4f3cf14d63dc74db5dc5905ca8
```

Для него были подтверждены оба Railway `success`. Этот commit добавил controlled Autopilot smoke-file в path-filter `VELIA Agent Core`.

Последний известный Android Autopilot Center commit:

```text
fdc7c9e151df0accf56c25d0655a584ad5ea7cdb
```

Для него был подтверждён post-merge Android CI и реальная установка APK.

# 4. Что реально подтверждено

## 4.1 VELIA Developer read-only

На реальном Android подтверждён путь:

```text
ordinary chat
→ mobile SSE
→ VELIA Developer router
→ connected GitHub repository
→ deterministic Fast Path
→ Kimi final answer
→ verified file:Lx-Ly citations
```

Реальный замер:

```text
$0.03721 / 47.1 s
```

## 4.2 VELIA Agent Core

На реальном Android подтверждено:

```text
natural request
→ structured plan
→ approval
→ protected tool execution
→ persistence
→ later read
```

Проверены:

```text
velia.tasks.create_draft
velia.tasks.list
```

Подтверждено, что write ждёт approval, повторное выполнение не создаёт дубль, данные сохраняются и Android показывает нативные карточки.

## 4.3 VELIA Coding Agent

На реальном Android подтверждено:

```text
repository change request
→ plan card
→ explicit approval
→ velia/... branch
→ sequential commits
→ draft PR
→ structured result card
```

Подтверждено:

- план не пишет до approval;
- отдельная branch;
- атомарные commits;
- draft PR;
- Android cards с repository, branches, steps, files, commits и PR link;
- merge/deploy отсутствуют.

## 4.4 Android Coding Autopilot Center

На реальном устройстве открыт production экран.

Подтверждено:

- backend API доступен;
- worker показывает `готов`;
- CI Watch — enabled;
- CI Repair — enabled;
- Actions logs fallback — enabled;
- Review Loop — enabled;
- merge readiness check — enabled;
- режим показывает:

```text
draft_pr_only
dry_run
auto_merge=false
deployment=false
```

Это подтверждает UI, transport и runtime flags, но ещё не подтверждает полный autonomous lifecycle.

# 5. Что реализовано в Coding Autopilot

## 5.1 Foundation

- missions создаются paused;
- обязательный allowed-path scope;
- protected-path policy;
- queue с idempotency и priority;
- один active run на repository;
- background worker;
- advisory locks;
- `FOR UPDATE SKIP LOCKED`;
- DB leases и recovery;
- существующий Coding Agent используется как write-engine;
- отдельная `velia/...` branch;
- draft PR only.

## 5.2 CI Watch + Repair

Lifecycle:

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

- exact-head checks;
- GitHub Actions + Railway commit statuses;
- максимум две repair-итерации;
- repair только approved-plan files;
- branch-head drift блокирует run;
- no second PR/branch;
- infrastructure/network/permission/cancelled/timeout failures не лечатся product-code patch;
- недостаточные evidence завершаются fail-closed.

## 5.3 Bounded Actions logs

- failed exact-head jobs only;
- bounded jobs и bytes;
- redaction secrets/tokens;
- используется как fallback после check output/annotations;
- отсутствие `Actions: Read` не разрешает blind repair.

## 5.4 Review Loop

- читает reviews/threads существующего draft PR;
- только `CHANGES_REQUESTED` может запустить code repair;
- comments/questions не меняют код;
- approved-plan files only;
- bounded review repair commit;
- после commit снова exact-head CI;
- не resolve-ит спорные threads автоматически;
- approval не выполняет merge.

## 5.5 Merge policy dry-run

Оценивает:

- exact-head CI;
- branch freshness;
- mergeability;
- approved file scope;
- protected paths;
- deletions/renames/binaries;
- changed-lines limit;
- requested changes;
- manual approval requirement.

Рекомендации:

```text
not_ready
ready_to_mark_ready
eligible
```

Даже `eligible` не выполняет merge.

# 6. Жёсткая граница безопасности

Нельзя добавлять без новой отдельной задачи и явного решения владельца:

- auto-merge;
- auto-deploy;
- direct production branch writes;
- force push;
- shell execution на Railway host;
- self-modifying safety policy;
- secret/auth/billing/financial/migration/infrastructure writes;
- workflow writes для обычной mission;
- бесконечный repair loop;
- автоматическое расширение allowed paths.

Текущий обязательный режим:

```text
draft_pr_only=true
auto_merge=false
deployment=false
merge_policy=dry_run
```

# 7. Production feature flags

Ожидаемые включённые flags:

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

Android Center уже показал capabilities как enabled. В новой сессии всё равно проверь status endpoints и deployment.

Никогда не выводи:

- GitHub App private key;
- installation token;
- Railway secrets;
- auth tokens;
- private keys.

# 8. Главная незавершённая задача — единый controlled acceptance

Разработка всех запланированных Autopilot-этапов завершена, автоматические tests/CI прошли, backend deployed, Android Center установлен. Но единая реальная цепочка ещё не принята.

Нужно провести один controlled run:

```text
paused mission
→ queue remains idle
→ activation
→ autonomous branch/commits/draft PR
→ controlled CI failure
→ automatic repair commit
→ new green exact-head CI
→ explicit REQUEST_CHANGES
→ review repair commit
→ new green exact-head CI
→ merge-policy dry-run
→ no actual merge/deploy
```

# 9. Controlled fixture

Production fixture:

```text
tests/test_velia_agent_coding_autopilot_controlled_repair_fixture.py
```

Dedicated smoke file:

```text
docs/velia-autopilot-controlled-repair-smoke.txt
```

Expected first line:

```text
VELIA_AUTOPILOT_REPAIR_OK
```

Optional second line:

```text
review-note: initial
```

Fixture inert when smoke-file absent.

Workflow path-filter fix уже production в commit:

```text
8497c3709a825c4f3cf14d63dc74db5dc5905ca8
```

Поэтому изменение dedicated docs-файла теперь должно запустить `VELIA Agent Core`.

# 10. Точный acceptance-сценарий

## 10.1 Создание mission

В Android открой:

```text
Инструменты / Возможности
→ Coding Autopilot
→ +
```

Выбери connected project `deepalpha-bot`.

Поля:

```text
Название миссии:
VELIA Controlled Autopilot Acceptance

Разрешённые пути:
docs/

Максимум шагов:
2

Максимум файлов:
1
```

Создай mission на паузе.

## 10.2 Добавление задачи

Точный task text:

```text
Создай только файл docs/velia-autopilot-controlled-repair-smoke.txt.
Первая строка должна быть VELIA_AUTOPILOT_REPAIR_BROKEN.
Вторая строка должна быть review-note: initial.
Не изменяй другие файлы.
Создай отдельную velia/ ветку и только draft PR.
Не выполняй merge и deployment.
```

Пока mission paused, проверь:

- task остаётся queued;
- branch отсутствует;
- commit отсутствует;
- PR отсутствует.

## 10.3 Activation и CI repair

Активируй mission.

Ожидается:

```text
queued
→ planning
→ executing
→ waiting_ci
```

Coding Agent должен создать только dedicated smoke-file и draft PR.

Первый exact-head Agent Core CI должен упасть с deterministic instruction заменить первую строку на:

```text
VELIA_AUTOPILOT_REPAIR_OK
```

Autopilot должен:

- получить bounded evidence;
- изменить только этот файл;
- сохранить вторую строку;
- сделать repair commit в той же branch;
- не создать второй PR;
- снова перейти в `waiting_ci`;
- дождаться нового green exact-head CI;
- перейти в `ready_for_review`.

Если CI вообще не появился — проверить path-filter и actual PR changed files. Не объявлять repair успешным.

Если logs недоступны — зафиксировать точный error (`github_forbidden` или другой), не угадывать patch.

## 10.4 Review Loop

После green CI отправить через доступного GitHub reviewer/bot явный review:

```text
REQUEST_CHANGES
```

Требование:

```text
Измени только вторую строку файла docs/velia-autopilot-controlled-repair-smoke.txt на:
review-note: reviewed
Первую строку VELIA_AUTOPILOT_REPAIR_OK сохрани без изменений.
```

Если GitHub запрещает self-review, не имитировать успех. Использовать доступного reviewer/bot или честно зафиксировать blocker.

Ожидается:

- Review Loop видит `CHANGES_REQUESTED`;
- создаёт один review repair commit;
- меняет только вторую строку;
- возвращает run в `waiting_ci`;
- новый exact-head CI становится green;
- обычные comments не запускают дополнительный patch.

## 10.5 Merge dry-run

После green CI открыть merge recommendation.

Проверить:

- mode = `dry_run`;
- execution_supported = false;
- auto_merge = false;
- deployment = false;
- reasons фактические;
- никакого merge commit не появилось;
- Railway production не изменился из-за smoke PR.

Smoke PR не merge-ить. После фиксации результатов закрыть его без merge только с разрешения владельца.

# 11. Что записать после acceptance

Обновить README фактическими данными:

- mission id;
- task id;
- run id;
- branch;
- draft PR number;
- initial commit;
- failed CI attempt/head SHA;
- repair commit;
- green CI attempt/head SHA;
- review action id;
- review repair commit;
- финальный green CI;
- merge-policy recommendation;
- total cost;
- подтверждение отсутствия merge/deploy;
- найденные defects и fixes, если были.

Не объявлять Autopilot production-accepted без всех пунктов.

# 12. Ключевые изменения, уже находящиеся в коде

Backend:

```text
#405 Autopilot Foundation
#409 CI Watch + bounded repair
#410 Review Loop
#411 merge-policy dry-run
#412 bounded Actions logs
#413 controlled acceptance fixture
```

Android:

```text
#28 Coding Agent structured cards
#29 Autopilot Center
#30 Review/CI/merge dry-run state
```

Backend presentation/UX/hotfix:

```text
#403 Agent presentation persistence
#404 command routing and active-plan hotfix
#407 Coding Agent structured presentation
```

Перед опорой на номер PR проверить его фактический state и diff.

# 13. Известная stale-ветка

Не продолжать и не создавать PR из:

```text
fix/velia-autopilot-controlled-smoke-trigger
```

Она отстала от production; нужный fix уже находится в `8497c370...`.

# 14. Следующая продуктовая тема

После controlled acceptance пользователь хочет показать найденный на GitHub проект, который, по его мнению, будет очень полезен VELIA.

Когда пользователь пришлёт ссылку или название:

1. изучи repository через GitHub connector или web, в зависимости от доступности;
2. проверь license, activity, architecture, dependencies, security model и commercial-use restrictions;
3. не предлагай полное копирование автоматически;
4. сравни с уже существующими VELIA Agent Core, Coding Agent и Autopilot;
5. выдели, что можно адаптировать компактно;
6. оцени backend, Android, infrastructure и cost impact;
7. предложи отдельный staged plan;
8. ничего не merge/deploy без фактической реализации и проверок.

# 15. Инженерные правила

- Работай connector-first.
- Не создавай повторные branches/PR для уже выполненного действия.
- Проверяй exact-head SHA перед merge.
- После merge backend проверяй оба Railway services.
- После Android merge проверяй post-merge CI и APK artifact.
- Не resolve review thread без анализа.
- Не скрывай blocker generic фразой.
- Не повышай cost limits первым решением.
- Не ослабляй protected paths ради smoke.
- Не трогай unrelated PR.
- Не выдавай preview deployment за production.
- Не обещай background работу без соответствующего инструмента.

Начни с краткого статуса после чтения README и проверки production, затем проведи controlled Autopilot acceptance. После этого попроси у пользователя ссылку на найденный GitHub-проект.
