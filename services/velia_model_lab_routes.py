"""Research pages within the existing Telegram-authenticated owner console."""
from __future__ import annotations

import asyncio
import uuid
from urllib.parse import quote

from aiohttp import web

import admin_routes as core
from services import velia_model_lab_service as lab
from services.velia_admin_security_service import is_admin_user

SECTION = "Исследования VELIA"
STATUS = {"queued": "В очереди", "running": "Выполняется", "succeeded": "Завершено",
          "failed": "Ошибка", "cancelled": "Отменено", "passed": "Верно",
          "review": "Нужна оценка", "error": "Ошибка запроса"}
ERRORS = {"model_configuration_changed": "Конфигурация Flash изменилась во время проверки. Запустите новый прогон.",
          "flash_unavailable": "Flash сейчас недоступен.", "worker_lease_expired": "Рабочий процесс не восстановил задание после двух попыток.",
          "research_providers_unavailable": "Поиск или исследовательская модель не подключены.",
          "no_primary_sources": "Поиск не вернул подходящих первичных источников.",
          "invalid_research_report": "Модель вернула отчёт с некорректной структурой или ссылками.",
          "research_step_failed": "Не удалось выполнить запрос. Проверьте доступность сервисов и повторите запуск.",
          "owner_changed": "Владелец панели изменился.", "run_cancelled": "Задание отменено."}


async def _authorize(request):
    denied = await core._guard(request)
    if denied is not None:
        return denied
    session = core._current_admin(request)
    if not session or not is_admin_user(session.get("admin_user_id", 0)):
        return web.Response(text="Доступ только владельцу", status=403)
    return None


def _owner(request):
    return int(core._current_admin(request)["admin_user_id"])


def _csrf(request):
    return f"<input type='hidden' name='_csrf' value='{core._e(core._key(request))}'>"


def _page(request, body, title=SECTION, status=200):
    styles = """<style id='velia-model-lab-style'>
    .lab textarea{width:100%;box-sizing:border-box;min-height:100px;resize:vertical;background:var(--bg);color:var(--text);border:1px solid var(--line);padding:12px;border-radius:10px;font:inherit}
    .lab label{display:flex;flex-direction:column;gap:6px;margin:12px 0}.lab .confirm{flex-direction:row}
    .lab p{line-height:1.55}.lab details{margin:12px 0}.lab summary{cursor:pointer;padding:10px 0;overflow-wrap:anywhere}
    .lab pre{white-space:pre-wrap;overflow-wrap:anywhere}.lab .lab-result{padding:14px 0;border-top:1px solid var(--line)}
    .lab input:not([type=checkbox]):not([type=hidden]),.lab select{width:100%;box-sizing:border-box}
    .lab .lab-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}.lab .lab-actions form{margin:0}
    </style>"""
    return web.Response(text=core._layout(title, SECTION, core._key(request), styles + "<div class='lab'>" + body + "</div>"),
                        content_type="text/html", status=status,
                        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"})


def _redirect(message="", run_id=""):
    url = "/admin/research" + ("/" + run_id if run_id else "")
    return web.HTTPSeeOther(url + ("?notice=" + quote(message, safe="") if message else ""))


async def index(request):
    denied = await _authorize(request)
    if denied is not None:
        return denied
    try:
        data = await asyncio.to_thread(lab.snapshot, _owner(request))
    except Exception:
        return _page(request, "<div class='card full'>Не удалось прочитать результаты исследований. Проверьте подключение к базе данных.</div>", status=503)
    caps, worker = data["capabilities"], data["worker"]
    state = lambda ready: "Подключён" if ready else "Не подключён"
    notice = f"<div class='flash'>{core._e(request.query.get('notice', '')[:2000])}</div>" if request.query.get("notice") else ""
    worker_notice = "" if worker.get("alive") else "<div class='flash'>Рабочий процесс пока не отвечает. Задания сохранятся в очереди; процесс исследований запускается вместе с Core.</div>"
    csrf = _csrf(request)
    rows = "".join(f"<tr><td><a href='/admin/research/{core._e(r['id'])}'>{core._e(r['label'])}</a></td><td>{'Качество Flash' if r['kind']=='benchmark' else 'Поиск методов'}</td><td>{core._e(STATUS.get(r['status'],r['status']))} {core._e(str(r['report'].get('completed','')))}</td><td>{core._e(r['created_at'])}</td></tr>" for r in data["runs"]) or "<tr><td colspan='4'>Запусков пока нет.</td></tr>"
    examples = "".join(f"<details><summary>{'Обучение' if e['split']=='train' else 'Контроль'} · {'Проверено' if e['approved'] else 'Черновик'} · {core._e(e['messages'][0]['content'][:100])}</summary><pre>{core._e(e['messages'][0]['content'])}</pre><p>Эталон</p><pre>{core._e(e['target'])}</pre><form method='post' action='/admin/research/examples/{core._e(e['id'])}/approval'>{csrf}<input type='hidden' name='approved' value='{'0' if e['approved'] else '1'}'><button>{'Снять отметку проверки' if e['approved'] else 'Подтвердить проверку'}</button></form></details>" for e in data["examples"]) or "<p class='muted'>Примеров пока нет.</p>"
    research_disabled = "" if caps["enabled"] and caps["search"] and caps["teacher"] else " disabled"
    bench_disabled = "" if caps["enabled"] and caps["flash"] else " disabled"
    profile = caps["profile"]
    body = f"""{notice}{worker_notice}<div class='grid'>
    <div class='card full'><h2>Качество Flash и компактность Bonsai</h2><p>Исследуем обучение весов, адаптеры и дистилляцию. Проверяем понимание контекста, опечаток, инструкций, логики и достоверность ответов. Скорость, RAM и размер весов — дополнительные ограничения.</p><p class='hint'>Исследовательский отчёт предлагает гипотезы. Проверка качества измеряет текущий Flash. Обучение весов и выпуск новой модели пока не подключены; размер весов и RAM здесь ещё не измеряются.</p></div>
    <div class='card'><div class='label'>Flash</div><p>{state(caps['flash'])}</p><div class='hint'>{core._e(profile['model'])}</div></div>
    <div class='card'><div class='label'>Веб-поиск</div><p>{state(caps['search'])}</p></div>
    <div class='card'><div class='label'>Исследовательская модель</div><p>{state(caps['teacher'])}</p><div class='hint'>{core._e(caps['teacher_provider'])}</div></div>
    <div class='card'><div class='label'>Рабочий процесс</div><p>{'Работает' if worker.get('alive') else 'Ожидается'}</p><div class='hint'>{core._e(worker.get('seen_at') or 'Нет сигнала')}</div></div>
    <div class='card wide'><h2>Исследовать метод улучшения</h2><form method='post' action='/admin/research/runs'>{csrf}<input type='hidden' name='kind' value='research'><input type='hidden' name='request_id' value='{uuid.uuid4()}'>
    <label>Название<input name='label' maxlength='120' required value='Улучшение Flash через веса Bonsai'></label>
    <label>Цель<textarea name='goal' minlength='8' maxlength='2000' required>Улучшить правильность ответов, понимание опечаток и контекста Flash, сохранив компактность Bonsai. Проверить LoRA и дистилляцию; выяснить совместимость с текущим форматом весов и стоимость по RAM и размеру.</textarea></label>
    <p class='hint'>По нажатию: до 2 поисковых запросов и 1 запроса исследовательской модели. Её провайдер может списать оплату по своему тарифу. Выдержки поиска требуют проверки полных источников.</p><button class='primary'{research_disabled}>Начать исследование</button></form></div>
    <div class='card wide'><h2>Проверить интеллект Flash</h2><form method='post' action='/admin/research/runs'>{csrf}<input type='hidden' name='kind' value='benchmark'><input type='hidden' name='request_id' value='{uuid.uuid4()}'>
    <label>Название запуска<input name='label' maxlength='120' required value='Flash: исходная проверка'></label>
    <label>Что проверяем<textarea name='goal' minlength='8' maxlength='2000' required>Зафиксировать исходное качество Flash перед экспериментами с весами.</textarea></label>
    <p class='hint'>8 задач с автоматической проверкой, 2 с ручной оценкой и до 4 ваших контрольных задач. Запросы выполняются по одному и ждут свободного Flash. Один текущий запрос может занять несколько минут. Малый набор не измеряет общий интеллект модели.</p><p class='hint'>Ревизия весов: {core._e(profile['revision'] or 'Не указана в VELIA_FLASH_MODEL_REVISION')}. Название запуска — ваша метка; оно не подтверждает смену весов.</p><button class='primary'{bench_disabled}>Запустить проверку качества</button></form></div>
    <div class='card full'><h2>История запусков</h2><div class='table-wrap'><table><thead><tr><th>Название</th><th>Тип</th><th>Статус</th><th>Создано</th></tr></thead><tbody>{rows}</tbody></table></div><div class='lab-actions'><a class='button' href='/admin/research'>Обновить результаты</a></div></div>
    <div class='card wide'><h2>Добавить проверенный пример</h2><form method='post' action='/admin/research/examples'>{csrf}
    <label>Вопрос<textarea name='prompt' maxlength='6000' required placeholder='Реальная ошибка Flash или новая задача'></textarea></label><label>Правильный ответ / критерии<textarea name='target' maxlength='6000' required></textarea></label>
    <label>Назначение<select name='split'><option value='train'>Для обучения</option><option value='holdout'>Для независимой проверки</option></select></label>
    <label class='confirm'><input type='checkbox' name='approved' value='1'> Я проверил вопрос и эталон</label><button>Сохранить пример</button></form>
    <p class='hint'>Контрольные задачи попадают в следующие проверки, но не в выгрузку обучения. Не добавляйте в обучение их переформулировки. Встроенные задачи защищены от прямого копирования.</p></div>
    <div class='card wide'><h2>Данные для обучения</h2><p>JSONL содержит только подтверждённые примеры с назначением «Для обучения». У контрольных данных назначение сохраняется.</p><a class='button' href='/admin/research/dataset.jsonl'>Скачать набор JSONL</a><p class='hint'>Выгрузка не запускает обучение. Для Bonsai отдельно нужно подтвердить совместимость адаптера и измерить размер итоговой модели.</p><h2>Последние примеры</h2>{examples}</div></div>"""
    return _page(request, body)


async def detail(request):
    denied = await _authorize(request)
    if denied is not None:
        return denied
    run = await asyncio.to_thread(lab.get_run, _owner(request), request.match_info["run_id"])
    if not run:
        raise web.HTTPNotFound()
    csrf, report = _csrf(request), run["report"]
    notice = f"<div class='flash'>{core._e(request.query.get('notice', '')[:2000])}</div>" if request.query.get("notice") else ""
    body = f"{notice}<div class='grid'><div class='card full'><h2>{core._e(run['label'])}</h2><p>{core._e(run['goal'])}</p><p>Статус: {core._e(STATUS.get(run['status'],run['status']))}</p>"
    if run["error_code"]:
        body += f"<div class='flash'>{core._e(ERRORS.get(run['error_code'],'Не удалось завершить задание. Повторите запуск.'))}</div>"
    body += "<div class='lab-actions'><a class='button' href='/admin/research'>Все исследования</a><a class='button' href=''>Обновить</a>"
    if run["status"] in {"running", "queued"}:
        body += f"<form method='post' action='/admin/research/{core._e(run['id'])}/cancel'>{csrf}<button>Отменить задание</button></form>"
    body += "</div><p class='hint'>После отмены текущий сетевой запрос может ещё завершаться; его результат не сохранится.</p></div>"
    if run["kind"] == "benchmark":
        metrics = run["metrics"]
        score = f"{metrics['automatic_score']}%" if metrics["automatic_score"] is not None else "Нет результата"
        body += f"<div class='card full'><h2>Правильность: {core._e(score)}</h2><p>Автоматическая проверка: {metrics['passed']} верных, {metrics['failed']} неверных; ошибок запросов: {metrics['error']}. Нужна ручная оценка: {metrics['needs_review']}. Ручная оценка: {metrics['reviewed_passed']} верных, {metrics['reviewed_failed']} неверных.</p><p>Выполнено {len(run['answers'])} из {len(run['config']['cases'])}. Среднее время запроса: {core._e(str(round(metrics['mean_latency_ms']/1000,1))+' с' if metrics['mean_latency_ms'] is not None else 'Нет данных')}.</p><p class='hint'>Результат относится к выполненным задачам. Автоматическая оценка включает ошибки запросов на закрытых задачах; ручная оценка считается отдельно.</p></div>"
        profile = run["config"]["profile"]
        body += f"<div class='card full'><h2>Сравнить с другим запуском</h2><p class='hint'>Ревизия весов: {core._e(profile.get('revision') or 'Не подтверждена')}; commit приложения: {core._e(profile.get('application_commit') or 'Не указан')}; набор: {core._e(run['config']['suite_version'])}.</p><form method='get'><label>ID предыдущего запуска<input name='compare' value='{core._e(request.query.get('compare','')[:64])}' maxlength='64' required></label><button>Сравнить</button></form>"
        if request.query.get("compare"):
            previous = await asyncio.to_thread(lab.get_run, _owner(request), request.query["compare"])
            if previous:
                comparison = lab.compare_runs(previous, run)
                if comparison["eligible"]:
                    delta = comparison["delta"]
                    body += f"<p>Изменение автоматической оценки: {core._e(f'{delta:+.1f}' if delta is not None else 'Нет данных')} процентных пункта.</p><p class='hint'>{core._e(comparison['note'])}</p>"
                    if not comparison["revision_recorded"]:
                        body += "<p class='hint'>Ревизии весов не указаны. Связь разницы с изменением весов не подтверждена.</p>"
                else:
                    body += f"<p>{core._e(comparison['reason'])}</p>"
            else:
                body += "<p>Предыдущий запуск не найден.</p>"
        body += "</div><div class='card full'><h2>Ответы Flash</h2>"
        for answer in run["answers"]:
            body += f"<div class='lab-result'><h3>{core._e(answer['category'])} · {core._e(STATUS.get(answer['outcome'],answer['outcome']))}</h3>"
            for message in answer["messages"]:
                body += f"<p class='hint'>{'Пользователь' if message['role']=='user' else 'Контекст ответа'}</p><pre>{core._e(message['content'])}</pre>"
            body += f"<p class='hint'>Ответ Flash</p><pre>{core._e(answer['answer'] or 'Ответ не получен')}</pre><p class='hint'>Эталон / критерий</p><pre>{core._e(answer['expected'])}</pre><p class='hint'>{core._e(answer['model'] or 'Модель не сообщена')} · {round(answer['latency_ms']/1000,1)} с</p>"
            if answer["error_code"]:
                body += f"<p>Код ошибки: {core._e(answer['error_code'])}</p>"
            if answer["outcome"] == "review":
                body += f"<form method='post' action='/admin/research/{core._e(run['id'])}/review/{core._e(answer['id'])}'>{csrf}<label>Оценка<select name='verdict'><option value='passed'{' selected' if answer.get('review_verdict')=='passed' else ''}>Верно</option><option value='failed'{' selected' if answer.get('review_verdict')=='failed' else ''}>Неверно</option></select></label><label>Комментарий<textarea name='note' maxlength='2000'>{core._e(answer.get('review_note',''))}</textarea></label><button>Сохранить оценку</button></form>"
            body += "</div>"
        body += "</div>"
    elif report.get("summary"):
        body += f"<div class='card full'><h2>Гипотезы улучшения</h2><p>{core._e(report['summary'])}</p><p class='hint'>План создан по поисковым выдержкам. Совместимость методов нужно подтвердить по полным материалам; обучение не проводилось.</p>"
        for h in report.get("hypotheses", []):
            body += f"<div class='lab-result'><h3>{core._e(h['title'])}</h3><p>{core._e(h['method'])}</p><p><strong>Проверка:</strong> {core._e(h['test'])}</p><p><strong>Риск:</strong> {core._e(h['risk'])}</p><p class='hint'>Источники: {core._e(', '.join(h['source_ids']))}</p></div>"
        body += "<h3>Что ещё выяснить</h3><ul>" + "".join(f"<li>{core._e(v)}</li>" for v in report.get("unknowns", [])) + "</ul><h3>Источники</h3><ul>"
        for source in report.get("sources", []):
            if lab._primary_url(source["url"]):
                body += f"<li>{core._e(source['id'])}: <a href='{core._e(source['url'])}' rel='noopener noreferrer' target='_blank'>{core._e(source['title'] or source['url'])}</a><p class='hint'>{core._e(source['snippet'])}</p></li>"
        body += "</ul></div>"
    return _page(request, body + "</div>", run["label"])


async def create_run(request):
    denied = await _authorize(request)
    if denied is not None:
        return denied
    form = await request.post()
    try:
        run_id = await asyncio.to_thread(lab.enqueue, _owner(request), form.get("kind", ""),
            form.get("goal", ""), form.get("label", ""), form.get("request_id", ""))
    except ValueError as exc:
        return _redirect(str(exc))
    return _redirect("Задание сохранено в очереди.", run_id)


async def cancel_run(request):
    denied = await _authorize(request)
    if denied is not None:
        return denied
    await asyncio.to_thread(lab.cancel, _owner(request), request.match_info["run_id"])
    return _redirect("Отмена обработана.", request.match_info["run_id"])


async def review_answer(request):
    denied = await _authorize(request)
    if denied is not None:
        return denied
    form = await request.post()
    try:
        changed = await asyncio.to_thread(lab.review, _owner(request), request.match_info["run_id"],
            request.match_info["answer_id"], form.get("verdict", ""), form.get("note", ""))
    except ValueError as exc:
        return _redirect(str(exc), request.match_info["run_id"])
    return _redirect("Оценка сохранена." if changed else "Ответ для оценки не найден.", request.match_info["run_id"])


async def create_example(request):
    denied = await _authorize(request)
    if denied is not None:
        return denied
    form = await request.post()
    try:
        await asyncio.to_thread(lab.add_example, _owner(request), form.get("prompt", ""),
            form.get("target", ""), form.get("split", ""), form.get("approved") == "1")
    except ValueError as exc:
        return _redirect(str(exc))
    return _redirect("Пример сохранён.")


async def approve_example(request):
    denied = await _authorize(request)
    if denied is not None:
        return denied
    form = await request.post()
    await asyncio.to_thread(lab.approve_example, _owner(request), request.match_info["example_id"], form.get("approved") == "1")
    return _redirect("Статус проверки обновлён.")


async def dataset(request):
    denied = await _authorize(request)
    if denied is not None:
        return denied
    payload = await asyncio.to_thread(lab.export_dataset, _owner(request))
    return web.Response(body=payload.encode("utf-8"), content_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="velia-flash-train.jsonl"', "Cache-Control": "no-store"})


def setup_model_lab_routes(app):
    app.cleanup_ctx.append(lab.worker_context)
    app.router.add_get("/admin/research", index)
    app.router.add_post("/admin/research/runs", create_run)
    app.router.add_post("/admin/research/examples", create_example)
    app.router.add_get("/admin/research/dataset.jsonl", dataset)
    app.router.add_get("/admin/research/{run_id}", detail)
    app.router.add_post("/admin/research/{run_id}/cancel", cancel_run)
    app.router.add_post("/admin/research/{run_id}/review/{answer_id}", review_answer)
    app.router.add_post("/admin/research/examples/{example_id}/approval", approve_example)
