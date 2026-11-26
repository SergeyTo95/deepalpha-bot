const views = new WeakMap();
const MODELS = {flash:'Flash', quantum:'Quantum', pro:'PRO'};
const PROGRESS = {queued:'В очереди', running:'Велия проверяет изменения', publishing:'Публикация в GitHub'};
const MESSAGES = {
  pr_review_limit:'Лимит проверок исчерпан или одна проверка уже выполняется.',
  pr_review_not_ready:'Проверка PR ещё запускается. Попробуй обновить данные.',
  pr_review_stale:'PR изменился. Обнови список и проверь новую версию.',
  pr_review_file_limit:'В пилоте можно проверить PR до 30 файлов.',
  pr_review_no_reviewable_diff:'В PR нет доступных добавленных строк для проверки.',
  pr_review_publication_unknown:'GitHub мог принять комментарии. Проверь PR перед повторной публикацией.',
  pr_review_model_failed:'Выбранная модель не завершила проверку.',
  pr_review_report_invalid:'Модель вернула отчёт, который не удалось проверить.',
  github_forbidden:'Проверь права приложения Велии на чтение или запись Pull requests в GitHub.',
};
const SKIPPED = {
  unsupported_path:'неподдерживаемое имя',
  sensitive_file:'чувствительный файл',
  missing_or_large_diff:'отсутствует или превышает лимит изменений',
  context_limit:'исчерпан лимит контекста',
  incomplete_diff:'неполный diff',
  no_added_lines:'нет добавленных строк',
};

export async function mountPullRequestReview(host, {project, request, signal, element, button, confirmPublish}) {
  const version = Symbol();
  views.set(host,version);
  const refresh = button('Обновить список',() => mountPullRequestReview(host,{
    project,request,signal,element,button,confirmPublish,
  }));
  host.replaceChildren(element('h4','Проверка PR'),refresh);
  const status = element('p','Загружаю пул-реквесты…','feature-notice');
  status.setAttribute('role','status');
  const form = element('form', undefined, 'feature-form');
  const report = element('section');
  host.append(status,form,report);
  const base = `developer/projects/${encodeURIComponent(project.id)}`;
  const active = () => !signal.aborted && host.isConnected && views.get(host) === version;
  const call = (path, params) => request(base + path, {signal, ...params});
  const error = (value,fallback) => MESSAGES[value?.code || value] || value?.message || fallback;
  let refreshTimer;

  function track(review) {
    clearTimeout(refreshTimer);
    if (!active()) return;
    renderReview(review);
    if (PROGRESS[review.status]) refreshTimer = setTimeout(async () => {
      if (!active()) return;
      try {
        track((await call(`/pr-reviews/${encodeURIComponent(review.id)}`)).review);
      } catch (failure) {
        if (active()) status.textContent = error(failure,'Не удалось обновить отчёт.');
      }
    },2400);
  }

  function renderReview(review) {
    const prUrl = `https://github.com/${project.repository_full_name}/pull/${review.pull_number}`;
    const heading = element('h5',`PR #${review.pull_number} · ${MODELS[review.model]} · ${review.head_sha.slice(0,9)}`);
    report.replaceChildren(heading);
    status.textContent = PROGRESS[review.status] || '';
    if (review.error) status.textContent = error(review.error,'Проверку не удалось завершить.');
    if (review.status === 'stale') status.textContent = MESSAGES.pr_review_stale;
    if (!review.report) return;
    const value = review.report;
    const metrics = [`Проверено файлов: ${value.reviewed_files}`, `пропущено: ${value.skipped.length}`];
    if (value.tokens != null) metrics.push(`токенов: ${value.tokens}`);
    report.append(element('p',metrics.join(' · ')));
    const findings = element('ul');
    for (const item of value.findings) {
      const entry = element('li');
      entry.append(element('strong',`${item.path} · строка ${item.line}`),element('p',item.body));
      findings.append(entry);
    }
    report.append(value.findings.length ? findings : element('p','В проверенных строках замечаний нет.'));
    if (value.invalid_findings) report.append(element('p',`Отклонено замечаний с неверными строками: ${value.invalid_findings}.`));
    if (value.skipped.length) {
      const detail = element('details');
      const omissions = element('ul');
      for (const item of value.skipped) omissions.append(element('li',`${item.path} · ${SKIPPED[item.reason] || item.reason}`));
      detail.append(element('summary','Пропущенные файлы'),omissions);
      report.append(detail);
    }
    if (review.status === 'published') {
      const link = element('a','Открыть опубликованное ревью');
      link.href = prUrl + '#pullrequestreview-' + review.review_id;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      report.append(link);
    }
    if (review.status === 'completed' && value.findings.length) {
      const publish = button('Опубликовать замечания в GitHub',async () => {
        if (!confirmPublish(`Опубликовать замечания к PR #${review.pull_number} в GitHub?`)) return;
        publish.disabled = true;
        try {
          track((await call(`/pr-reviews/${encodeURIComponent(review.id)}/publish`,{method:'POST',data:{}})).review);
        } catch (failure) {
          if (!active()) return;
          status.textContent = error(failure,'Не удалось опубликовать.');
          publish.disabled = (failure?.code === 'pr_review_publication_unknown' || failure?.code === 'pr_review_stale');
        }
      });
      report.append(publish);
    }
  }

  try {
    const [{pulls,models},{reviews}] = await Promise.all([call('/pull-requests'),call('/pr-reviews')]);
    if (!active()) return;
    status.textContent = pulls.length ? '' : 'В GitHub пока нет открытых PR.';
    const pullsByNumber = new Map(pulls.map(pull => [String(pull.number),pull]));
    const field = (label, values, name) => {
      const wrapper = element('label',label);
      const select = element('select');
      select.name = name;
      for (const [value,text] of values) select.add(new Option(text,value));
      wrapper.append(select);
      form.append(wrapper);
      return select;
    };
    if (pulls.length && models.length) {
      const selectedPR = field('Пул-реквест',pulls.map(pull => [pull.number,`#${pull.number} ${pull.title} · ${pull.head_sha.slice(0,9)}`]),'pull');
      const selectedModel = field('Модель',models.map(model => [model,MODELS[model]]),'model');
      const start = element('button','Запустить проверку');
      start.type = 'submit';
      form.append(element('p','Выбранная модель получит diff из GitHub для анализа.'),start);
      form.onsubmit = async event => {
        event.preventDefault();
        const {number,head_sha,base_sha} = pullsByNumber.get(selectedPR.value);
        start.disabled = true;
        try {
          track((await call('/pr-reviews',{method:'POST',data:{pull_number:number,head_sha,base_sha,model:selectedModel.value}})).review);
        } catch (failure) {
          if (active()) status.textContent = error(failure,'Не удалось поставить проверку в очередь.');
        } finally { start.disabled = false; }
      };
    } else if (pulls.length) status.textContent = 'Сейчас нет доступной модели для проверки.';
    if (reviews.length) track(reviews[0]);
  } catch (failure) {
    if (active()) status.textContent = error(failure,'Не удалось открыть проверку PR.');
  }
}
