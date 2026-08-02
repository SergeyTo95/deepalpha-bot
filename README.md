---
title: DeepAlpha
emoji: 📈
colorFrom: blue
colorTo: purple
sdk: docker
---

# VELIA / Velyon Core / DeepAlpha Markets Backend

Этот репозиторий содержит production-backend экосистемы **VELIA** и **DeepAlpha Markets**: мобильный API, Telegram-бот, WebApp, AI-оркестрацию, плагины живых данных, генерацию изображений, Developer API, фоновые workers, учёт стоимости, историю разговоров и пилот долговременной памяти.

README является главной технической точкой входа для разработчика или нового AI-сеанса. Перед изменениями необходимо прочитать его полностью, проверить фактическое состояние целевой ветки и не считать описанную feature-работу уже развёрнутой, пока это не подтверждено Railway production-логами.

---

## 1. Публичная продуктовая идентичность

| Объект | Публичное название |
|---|---|
| Приложение и продукт | **VELIA** |
| Персональный помощник | **Velia / Велия** |
| Интеллектуальное ядро | **Velyon Core** |
| Генерация изображений | **Velia Images / Velyon Core Images** |
| Prediction-market модуль | **DeepAlpha Markets** |
| Основной публичный домен | `https://deepalpha-ai.com` |

Публичные Android-контракты, API-ответы, документация, пользовательские ошибки и интерфейс не должны раскрывать:

- внешних поставщиков моделей;
- внутренние названия моделей;
- маршрутизацию между поставщиками;
- API-ключи и секреты;
- скрытые промпты;
- chain-of-thought;
- инфраструктурные адреса;
- названия внутренних баз и служебных контейнеров.

Допустимая формулировка идентичности:

> Я Велия — твой персональный ИИ-помощник на базе Velyon Core.

---

## 2. Назначение проекта

VELIA развивается не как один экран чата, а как персональная AI-среда. Backend должен обеспечивать:

- защищённый текстовый чат;
- естественное голосовое взаимодействие через мобильный клиент;
- синхронизированную историю;
- персональный профиль;
- управляемую пользователем память;
- актуальную погоду;
- web/news retrieval с источниками;
- research;
- анализ файлов, PDF, документов и таблиц;
- генерацию и редактирование изображений;
- DeepAlpha Markets;
- тарифы, токены, квоты и защиту бюджета;
- Developer API и webhooks;
- Telegram-бот и WebApp;
- Android-клиент;
- безопасную деградацию при сбоях отдельных подсистем.

Главный архитектурный принцип: Android и другие клиенты работают с устойчивым provider-neutral контрактом Velyon Core. Внешние реализации могут меняться без миграции публичного продукта.

---

## 3. Репозиторий и правила веток

```text
SergeyTo95/deepalpha-bot
```

### Production/deploy branch

```text
feature/turbo-short-term-btc
```

### Критические правила

1. **Не использовать backend `main` для текущей production-разработки.**
2. Feature/fix ветки создаются от `feature/turbo-short-term-btc`.
3. PR направляется обратно в `feature/turbo-short-term-btc`.
4. Перед merge фиксируется exact head SHA.
5. Проверяется CI именно для этого SHA.
6. Проверяются review threads и конфликты.
7. Merge выполняется squash-методом с expected head SHA.
8. Merge не равен deploy: после merge отдельно проверяется Railway production.
9. Нельзя объявлять функцию работающей до подтверждения runtime-логами и реальным клиентом.
10. Секреты никогда не добавляются в Git, README, issue, PR body или публичные логи.

---

## 4. Связанный Android-репозиторий

```text
SergeyTo95/deepalpha-android
```

Ветки Android:

- `main` — стабильные релизы;
- `develop` — интеграционная ветка;
- `feature/*` — функциональная работа;
- `fix/*` — исправления.

Android PR обычно должен целиться в `develop`. Backend и Android изменения для одного контракта разрабатываются в отдельных PR и проверяются end-to-end только после готовности обеих сторон.

---

## 5. Production-процессы

Контейнер backend запускается через Supervisor. Типичный набор процессов:

```text
api-worker
bot
commercial-worker
opportunity-worker
velia-memory-shadow-worker
webapp
webhook-worker
```

### Назначение процессов

- `webapp` — aiohttp WebApp, Mobile API, Developer API, portal и служебные HTTP routes;
- `bot` — Telegram polling;
- `api-worker` — обработка фоновых Developer API jobs;
- `opportunity-worker` — Opportunity Scan;
- `commercial-worker` — коммерческие фоновые процессы;
- `webhook-worker` — доставка webhook events;
- `velia-memory-shadow-worker` — асинхронная доставка завершённых диалоговых turn в отдельный memory runtime.

Основной HTTP listener:

```text
0.0.0.0:3000
```

Предупреждение Supervisor о запуске от root само по себе не означает падение сервиса. Оно должно рассматриваться отдельно от функциональных ошибок.

Telegram polling может кратковременно перезапускаться, пока одна реплика получает PostgreSQL lock. Успешный финальный признак:

```text
polling_guard ... lock_acquired=True
Starting polling
```

Предупреждения `Unclosed client session` во время проигравших polling-процессов являются отдельным техническим долгом и не должны ошибочно приниматься за проблему VELIA memory или mobile chat.

---

## 6. Mobile API

Базовый публичный prefix:

```text
/mobile-api/v1
```

Основные контракты:

```text
POST   /auth/exchange
POST   /auth/refresh
POST   /auth/logout
GET    /me
GET    /profile
PATCH  /profile
GET    /plugins
PATCH  /plugins
GET    /conversations
POST   /conversations
GET    /conversations/{conversation_id}
PATCH  /conversations/{conversation_id}
DELETE /conversations/{conversation_id}
GET    /conversations/{conversation_id}/messages
POST   /conversations/{conversation_id}/messages
GET    /usage
```

Новый streaming-контракт разрабатывается отдельно:

```text
POST /mobile-api/v1/conversations/{conversation_id}/messages/stream
Content-Type: application/json
Accept: text/event-stream
Idempotency-Key: <uuid>
```

Публичные ответы не должны включать внутреннего provider/model branding.

---

## 7. Мобильная авторизация

Android не использует browser cookies и не получает Telegram-сессию напрямую.

### Pairing flow

1. Пользователь авторизуется через официальный Telegram/WebApp flow.
2. Backend создаёт одноразовый pairing code.
3. Код связан с конкретным Telegram user ID.
4. Код имеет короткий TTL.
5. Код одноразовый.
6. Новый код инвалидирует старый.
7. Android обменивает код на mobile session.
8. Backend выдаёт короткоживущий access token и rotating refresh token.

### Гарантии сессии

- device/session binding;
- server-side revocation;
- refresh rotation;
- защита от повторного использования refresh token;
- безопасный logout;
- временный network/server сбой не должен удалять валидную сессию;
- terminal security failure должен завершать сессию.

Ожидаемое продуктовое поведение: после успешной авторизации пользователь остаётся вошедшим, пока сам не выйдет или сервер не признает сессию окончательно недействительной.

---

## 8. Хранилище разговоров

Основные PostgreSQL таблицы:

```text
velia_conversations
velia_messages
```

### `velia_conversations`

Хранит:

- `conversation_id`;
- `user_id`;
- title и источник title;
- pinned/archive flags;
- timestamps;
- soft delete.

### `velia_messages`

Хранит:

- `message_id`;
- `conversation_id`;
- `user_id`;
- роль;
- content;
- status `pending/completed/error`;
- idempotency key;
- связь assistant → user message;
- request ID;
- внутренние provider/model metadata;
- usage tokens;
- cached tokens;
- reasoning tokens;
- estimated cost;
- latency;
- finish reason;
- error code;
- timestamps.

### Гарантии отправки

- idempotency key обязателен;
- повторный запрос возвращает существующий результат;
- одновременно для пользователя допускается только одна pending generation;
- user и pending assistant создаются до внешней генерации;
- завершённый assistant атомарно обновляет pending row;
- ошибка переводит row в `error`;
- conversation title может автоматически создаваться из первого сообщения;
- результат не должен попасть в другой разговор или другую сессию.

---

## 9. Обычный non-streaming chat pipeline

```text
Android POST /messages
  → mobile auth
  → validation
  → per-user/global budget checks
  → idempotency check
  → user message INSERT
  → pending assistant INSERT
  → prompt assembly
  → profile/plugins/images/quality wrappers
  → Velyon Core generation
  → completed/error assistant UPDATE
  → memory shadow enqueue
  → JSON response
```

Этот путь остаётся обязательным fallback даже после внедрения streaming.

---

## 10. Ускорение ответов: production-состояние

### Phase 1: безопасное уменьшение latency

Смержено в production/deploy branch:

```text
PR #364
merge commit: ba8c56deb550bc27215f34445659398aa6ab8819
```

Изменения:

- переиспользование HTTP/TLS соединений;
- conversation-scoped prompt cache affinity;
- intent-first plugin routing;
- metadata-only timing logs;
- conservative adaptive reasoning;
- сложные запросы остаются на high-quality route.

Основные внутренние логи:

```text
VELIA_PROMPT_TIMING
VELIA_CORE_TIMING
VELIA_GENERATION_TIMING
VELIA_CHAT_TOTAL_TIMING
```

### Phase 1.1: instant casual responses

Смержено в production/deploy branch:

```text
PR #365
merge commit: 7fe4d8673c11468242307a8c0a5c0084b4abd953
```

Контекстно-независимые приветствия, благодарности и прощания отвечают без тяжёлой генерации, но проходят через обычные history/memory механизмы.

Подтверждённый real-device результат:

```text
«Привет, как дела?» → около 0.1 s, cost 0
```

Нельзя расширять deterministic route на сообщения, зависящие от контекста. Например, короткие `да`/`нет`, сложные вопросы, актуальные данные, изображения, команды памяти и анализ должны оставаться на основном pipeline.

Rollback switch:

```text
VELIA_CHAT_INSTANT_CASUAL_ENABLED=false
```

---

## 11. Streaming Phase 2 — текущее незавершённое состояние

Цель: сложный ответ должен начинать отображаться в Android сразу после первого содержательного delta, а не после полной генерации.

### Ветки

Backend:

```text
feature/velia-chat-streaming-sse
base: feature/turbo-short-term-btc
```

Android:

```text
feature/velia-chat-streaming-0.8.2
base: develop
```

### Backend уже создано в feature-ветке

- внутренний provider streaming gateway;
- parser SSE `data:` frames;
- извлечение только публичного `delta.content`;
- скрытые reasoning-поля не передаются клиенту;
- usage/cost/finalization для streaming attempts;
- retry с `reset` частичного клиентского текста;
- runtime wrapper, подключающий streaming только к подходящим текстовым запросам;
- authenticated SSE mobile endpoint;
- batching маленьких delta;
- keepalive comments;
- сохранение полного ответа существующим `send_message` pipeline;
- fallback на non-streaming path;
- unit tests для parser, usage, retry/reset, routing и SSE envelope;
- подключение тестов в workflow;
- установка streaming runtime и route в `run_web_process.py`.

### Streaming eligibility

Streaming предназначен для содержательных текстовых ответов.

Не должен использоваться для:

- instant casual response;
- image-generation intent;
- специальных memory acknowledgement flows;
- provider route, не поддерживающего текущий streaming adapter;
- запросов, которые существующие runtime wrappers должны завершить самостоятельно.

### Публичный SSE envelope

```json
{"type":"ready"}
{"type":"delta","text":"часть ответа"}
{"type":"reset"}
{"type":"complete","result":{"ok":true}}
{"type":"error","error":"stable_public_error"}
```

События передаются как:

```text
data: <json>\n\n
```

### Требуемое поведение при обрыве клиента

- backend generation не должна отменяться только из-за закрытия экрана или потери сети;
- полный ответ должен быть сохранён в PostgreSQL;
- Android после reconnect должен получить его из history;
- partial UI text не должен записываться как окончательный server message;
- нельзя повторно вызывать платную генерацию после того, как stream уже начался;
- idempotency key обязан сохраняться между попытками клиента.

### Что ещё не завершено

Backend feature-ветка ещё должна пройти:

1. полный exact-head CI;
2. review;
3. проверку route order и security wrappers;
4. PR в `feature/turbo-short-term-btc`;
5. squash merge;
6. Railway production deploy;
7. проверку логов first-delta и complete;
8. реальный тест Android 0.8.2.

Не считать streaming готовым до выполнения всего списка.

---

## 12. Долговременная память: Phase 1 Shadow

Память работает в **shadow-only** режиме.

Это означает:

- завершённые user/assistant turns ставятся в PostgreSQL outbox;
- отдельный worker доставляет их в private memory runtime;
- recall пока не добавляется в prompt;
- пользовательский ответ пока не должен зависеть от memory runtime;
- сбой памяти fail-open и не ломает чат.

### Таблица

```text
velia_memory_shadow_outbox
```

Статусы:

```text
pending
retrying
delivering
succeeded
failed
```

### Изоляция

Каждая запись должна быть dimensioned минимум по:

- `team_id`;
- `agent_id`;
- authenticated `user_id`;
- conversation/session ID.

### Production-подтверждение

Подтверждена успешная доставка:

```text
VELIA_MEMORY_SHADOW_ENQUEUED ...
VELIA_MEMORY_SHADOW_DELIVERY ... success=True status=200
```

Memory service работает отдельно в Railway, private-only, с persistent volume. Точный private domain нужно брать из Railway Networking UI; нельзя угадывать его по service display name.

### Связанные merge commits

```text
profile: 40db26c09d0508ae7427e35acc054db94b180cde
memory outbox: ec5111c25d861d7470f42c344316db705d795918
wrapper: 395b2ef7a0802502b1805fa3e1cff7892e74dadd
volume/network hotfix: 814a3cc0327e5c00225904bd14e46512f15c0d06
BM25/startup gate: 352bb1f5746852f8f435d31b8172551d9e96e27a
worker INFO logging: 091d8b64efd09e84f19e1e40ecc98f9e90efbc3c
```

### Важное ограничение

Текущий ответ Велии может помнить данные из обычного conversation context/profile, но успешная shadow delivery не означает, что recall уже подключён. Следующий memory phase должен проектироваться отдельно с privacy controls, edit/delete и opt-out.

---

## 13. Profile и персонализация

Профиль пользователя хранится на сервере и может включать:

- preferred name;
- about me;
- дополнительные provider-neutral preference fields.

Profile injection должен быть bounded, безопасным и не позволять пользователю подменять system policy. Профиль не должен раскрывать внутреннюю маршрутизацию и не должен автоматически превращаться в долговременную память без явной политики.

---

## 14. Live plugins

Активные направления:

- Weather;
- Web search / news.

Требования:

- intent detection происходит до чтения preference tables для обычного чата;
- актуальные данные нельзя выдумывать;
- external content считается untrusted;
- запросы bounded;
- timeout обязателен;
- источники sanitized;
- результаты и ошибки provider-neutral;
- quota и cost protection обязательны.

Слова вроде `сейчас`, `today`, `current` сами по себе не должны запускать web search без реального live-data intent.

---

## 15. Изображения

VELIA image generation использует отдельные runtime wrappers и queues.

Публичное название:

```text
Velia Images / Velyon Core Images
```

Требования:

- image intent должен быть распознан до text streaming;
- длинная image generation не должна блокировать обычный text route;
- Android получает provider-neutral image metadata;
- public URL должен быть безопасным;
- credentials не передаются в клиент;
- размер, формат и стоимость ограничены;
- retries не должны создавать дубли без idempotency.

---

## 16. Бюджет и usage

Перед generation выполняются:

- per-user daily message limit;
- per-user daily cost limit;
- global daily cost limit;
- request reserve;
- provider attempt reservation;
- attempt finalization с usage/cost.

Debug usage доступен только разрешённым пользователям и не должен раскрывать внешнего поставщика.

Instant deterministic responses имеют нулевые generation tokens и нулевую стоимость.

Streaming обязан финализировать тот же attempt/accounting, что и обычный path. Нельзя считать streaming бесплатным только потому, что текст отдаётся частями.

---

## 17. Security invariants

1. Никаких production credentials в Android.
2. Никаких provider credentials в публичных ответах.
3. Никаких raw hidden reasoning в SSE.
4. Никакого chain-of-thought в пользовательских ответах.
5. Все mobile routes требуют корректной mobile auth, кроме явно публичных health/connect flows.
6. Все conversation operations scoped по authenticated user.
7. Все message results scoped по conversation и session generation.
8. Idempotency обязателен для generation.
9. Temporary auth/network failures не должны стирать валидную сессию.
10. Terminal security failures должны закрывать сессию.
11. Memory fail-open.
12. Plugin failure fail-safe без фабрикации.
13. Image failure не ломает text chat.
14. Streaming failure имеет non-streaming recovery.
15. Public docs/logs не раскрывают внешнего provider/model.
16. Логи не содержат user prompt, полный answer, токены авторизации или secrets.
17. Метаданные latency допустимы.
18. Destructive actions требуют явного подтверждения в клиенте.
19. Нельзя полагаться на display name Railway service как на private DNS.
20. Нельзя объявлять deploy без runtime-проверки.

---

## 18. Environment variables

Ниже перечислены имена переменных без секретных значений.

### Mobile/chat

```text
VELIA_MOBILE_API_ENABLED
VELIA_CHAT_ENABLED
VELIA_CHAT_BETA_USER_IDS
VELIA_CHAT_MAX_INPUT_CHARS
VELIA_CHAT_CONTEXT_MESSAGES
VELIA_CHAT_CONTEXT_CHARS
VELIA_CHAT_MAX_OUTPUT_TOKENS
VELIA_CHAT_MAX_MESSAGES_PER_USER_DAY
VELIA_CHAT_PER_USER_DAILY_COST_USD_LIMIT
VELIA_CHAT_DAILY_COST_USD_LIMIT
VELIA_CHAT_REQUEST_COST_RESERVE_USD
VELIA_MOBILE_DEBUG_USAGE
VELIA_MOBILE_DEBUG_USER_IDS
```

### Latency

```text
VELIA_CHAT_PROMPT_CACHE_KEY_ENABLED
VELIA_CHAT_ADAPTIVE_REASONING_ENABLED
VELIA_CHAT_INSTANT_CASUAL_ENABLED
VELIA_CORE_HTTP_POOL_SIZE
```

### Streaming

```text
VELIA_CHAT_STREAMING_ENABLED
```

### Memory shadow

```text
VELIA_MEMORY_SHADOW_ENABLED
VELIA_MEMORY_SHADOW_WORKER_ENABLED
VELIA_MEMORY_SHADOW_USER_IDS
VELIA_MEMORY_SHADOW_ALLOW_ALL
VELIA_MEMORY_ENDPOINT
VELIA_MEMORY_API_KEY
VELIA_MEMORY_SERVICE_ID
VELIA_MEMORY_TEAM_ID
VELIA_MEMORY_AGENT_ID
VELIA_MEMORY_CONNECT_TIMEOUT_SECONDS
VELIA_MEMORY_READ_TIMEOUT_SECONDS
VELIA_MEMORY_SHADOW_MAX_ATTEMPTS
VELIA_MEMORY_SHADOW_POLL_SECONDS
VELIA_MEMORY_MAX_MESSAGE_CHARS
```

Значения секретов должны задаваться Railway references или secret variables, а не копироваться в Git.

---

## 19. Логи и диагностика

### Chat latency

```text
VELIA_PROMPT_TIMING
VELIA_CORE_TIMING
VELIA_GENERATION_TIMING
VELIA_CHAT_TOTAL_TIMING
VELIA_FAST_RESPONSE
```

### Streaming

Ожидаемые внутренние события:

```text
KIMI_STREAM_START
KIMI_STREAM_SUCCESS
KIMI_STREAM_FAILED
VELIA_STREAM_GENERATION_COMPLETED
VELIA_STREAM_FALLBACK
VELIA_MOBILE_STREAMING_ROUTE_INSTALLED
```

В публичных логах/дашбордах provider-specific названия должны быть нормализованы. Внутренние private production logs могут использовать существующие технические identifiers.

### Memory

```text
VELIA_MEMORY_SHADOW_WORKER_STARTED
VELIA_MEMORY_SHADOW_QUEUE
VELIA_MEMORY_SHADOW_ENQUEUED
VELIA_MEMORY_SHADOW_DELIVERY
```

### Полезные streaming метрики

- time to first delta;
- total generation duration;
- emitted chars;
- reset count;
- fallback count;
- client disconnect count;
- completion success rate;
- cost per completed response;
- cache hit tokens;
- retry count.

---

## 20. Testing

Focused workflow:

```text
.github/workflows/velia-mobile-chat.yml
```

Он должен компилировать mobile/chat modules и запускать focused pytest suite, включая:

- mobile auth;
- chat storage;
- LLM abstraction;
- latency runtime;
- streaming gateway;
- streaming runtime routing;
- mobile SSE envelope;
- conversation quality;
- image intent;
- images runtime;
- memory shadow;
- mobile hardening;
- plugins;
- weather;
- pairing;
- profile.

Дополнительно workflow проверяет отсутствие credential-like literals и внешнего provider branding в публичных файлах.

Перед merge нужно проверить не только итоговый зелёный badge, но и exact head SHA каждого workflow run.

---

## 21. Railway deployment checklist

1. Убедиться, что сервис смотрит на правильный repository.
2. Проверить branch `feature/turbo-short-term-btc` для production backend.
3. Проверить root directory и start command.
4. Проверить environment `production`.
5. Проверить нужный commit SHA в deployment.
6. Проверить запуск всех Supervisor processes.
7. Проверить `webapp entered RUNNING state`.
8. Проверить mobile health.
9. Проверить реальный Android auth refresh.
10. Проверить обычный message send.
11. Проверить instant casual response.
12. После merge streaming проверить SSE headers и первый delta.
13. Проверить completed message в PostgreSQL/history.
14. Проверить memory shadow enqueue/delivery.
15. Проверить usage/cost.
16. Проверить, что нет secrets в logs.

---

## 22. Известные проблемы и технический долг

- проигравшие Telegram polling процессы могут оставлять unclosed aiohttp sessions;
- часть runtime функциональности установлена patch-модулями и требует постепенной консолидации;
- public/internal naming необходимо продолжать разделять;
- memory recall ещё не подключён;
- streaming ещё не смержен и не подтверждён production;
- Android streaming integration ещё не завершена;
- голосовой режим требует device matrix testing;
- release signing и Play Store pipeline требуют отдельной работы;
- cost observability нужно довести до per-feature dashboards;
- необходимо тестирование network handover, app background и process death во время streaming.

---

## 23. Definition of done

Функция считается завершённой только когда:

- backend и client contracts совпадают;
- auth boundary сохранён;
- idempotency работает;
- loading/error/retry состояния реализованы;
- lifecycle races обработаны;
- история корректна;
- usage/cost корректны;
- privacy и branding invariants соблюдены;
- unit/integration tests добавлены;
- exact-head CI зелёный;
- PR review закрыт;
- merge выполнен в правильную ветку;
- Railway развернул нужный commit;
- runtime проверен реальным клиентом;
- документация соответствует факту;
- сбой новой функции не ломает базовый чат.

Экран, route или feature flag без end-to-end проверки не являются завершённой функцией.

---

## 24. Ближайшая последовательность работ

### Streaming 0.8.2

1. Завершить Android SSE integration.
2. Подключить stream transport к network clients.
3. Добавить repository streaming method.
4. Обновлять pending assistant bubble по delta.
5. Поддержать `reset`.
6. На `complete` заменить local pending данными server response/history.
7. При обрыве после начала потока не запускать новую платную generation.
8. Reconcile через history по тому же idempotency/request.
9. Сохранить non-streaming fallback для route unavailable до начала generation.
10. Добавить unit tests.
11. Обновить Android CI.
12. Открыть backend и Android PR.
13. Проверить оба exact-head CI.
14. Merge backend → production branch.
15. Проверить Railway deploy.
16. Merge Android → develop.
17. Собрать APK только из подтверждённого head.
18. Проверить time-to-first-text на реальном Samsung устройстве.

### После streaming

- visible memory controls;
- controlled recall pilot;
- sources/research UI;
- file analysis;
- image editing;
- DeepAlpha Markets integration;
- proprietary VELIA voice;
- subscriptions;
- release signing;
- staged public beta.

---

## 25. Основной принцип

VELIA должна становиться быстрее и умнее, не становясь менее честной, приватной или надёжной.

Не выдумывать актуальные данные. Не раскрывать инфраструктуру. Не путать shadow memory с active recall. Не повторять платную генерацию после начала streaming. Не терять валидную сессию из-за временного сбоя. Не смешивать разговоры. Не считать зелёный старый workflow подтверждением нового commit. Не считать merge подтверждением production deploy.
