import {openGitHubAuthorization} from './connections.mjs';

const FULL_NAME = /^[a-z0-9_.-]+\/[a-z0-9_.-]+$/iu;
const BRANCH = /^[a-z0-9_.\/-]{1,200}$/iu;
const ACTIVE = new Set(['dispatching','requested','waiting','pending','queued','in_progress']);
const MESSAGES = {
  project_tests_archived: 'Репозиторий архивирован.',
  project_tests_workflow_missing: 'Добавь в основную ветку .github/workflows/velia-tests.yml с событием workflow_dispatch.',
  project_tests_actions_permission: 'Для запуска из Велии приложению GitHub нужно разрешение Actions: Read and write. Можно запустить тесты в GitHub.',
  project_tests_reconnect: 'Подтверди свой аккаунт GitHub, чтобы Велия могла проверить права на запуск.',
  project_tests_write_required: 'Для запуска нужны права на запись в этот репозиторий.',
  project_tests_limit: 'Был недавний запуск или достигнут дневной лимит. Проверь статус в GitHub.',
  github_forbidden: 'GitHub отказал в доступе. Проверь разрешения GitHub Actions.',
  github_invalid_request: 'GitHub отклонил запуск. Проверь workflow_dispatch и обязательные параметры.',
};
const RESULTS = {
  success:'Тесты прошли', failure:'Тесты завершились с ошибкой', cancelled:'Запуск отменён',
  timed_out:'Истекло время выполнения', skipped:'Тесты пропущены',
  neutral:'Тесты завершены без оценки', action_required:'Требуется действие в GitHub',
  stale:'Запуск устарел', startup_failure:'GitHub не смог запустить тесты',
};

function githubRepository(fullName) {
  if (!FULL_NAME.test(fullName) || fullName.split('/').some(part => part === '.' || part === '..')) return '';
  return 'https://github.com/' + fullName;
}

export function validatedCloudShellUrl(raw, expectedRepository) {
  try {
    const url = new URL(raw);
    const params = url.searchParams;
    const keys = [...params.keys()];
    const branch = params.get('cloudshell_git_branch') || '';
    const workspace = params.get('cloudshell_workspace');
    if (url.origin !== 'https://shell.cloud.google.com' || url.pathname !== '/cloudshell/editor'
        || url.username || url.password || url.hash
        || new Set(keys).size !== keys.length
        || keys.some(key => !['cloudshell_git_repo','cloudshell_git_branch','cloudshell_workspace','show'].includes(key))
        || !githubRepository(expectedRepository) || params.get('cloudshell_git_repo') !== githubRepository(expectedRepository)
        || params.get('show') !== 'ide' || !BRANCH.test(branch) || branch.includes('..')
        || branch.startsWith('/') || branch.endsWith('/') || branch.includes('//')
        || (workspace !== null && !/^games\/velia-[a-f0-9-]{36}$/u.test(workspace))) return '';
    return url.href;
  } catch {
    return '';
  }
}

function externalLink(element, label, href) {
  const link = element('a', label, 'mission-file');
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

export async function openProjectCloudShell(host, {project, request, signal, element}) {
  const popup = window.open('about:blank', '_blank');
  if (popup) popup.opener = null;
  const status = element('p', 'Проверяю доступ к репозиторию…', 'feature-notice');
  status.setAttribute('role','status');
  host.replaceChildren(status);
  try {
    const response = await request(`developer/projects/${encodeURIComponent(project.id)}/cloud-shell`, {signal});
    const url = validatedCloudShellUrl(response.url, project.repository_full_name);
    if (!url) throw new Error('Cloud Shell доступен для публичных репозиториев.');
    if (signal.aborted || !host.isConnected) {
      popup?.close();
      return;
    }
    if (popup && !popup.closed) popup.location.replace(url);
    host.replaceChildren(externalLink(element,'Открыть редактор Cloud Shell',url));
  } catch (error) {
    popup?.close();
    if (!signal.aborted) status.textContent = error.message || 'Не удалось открыть Cloud Shell.';
  }
}

const currentPanels = new WeakMap();

export async function mountProjectTests(host, {project, request, signal, element, button}) {
  const version = Symbol();
  currentPanels.set(host, version);
  const repoUrl = githubRepository(project.repository_full_name);
  if (!repoUrl) return;
  const path = `developer/projects/${encodeURIComponent(project.id)}/test-runs`;
  const status = element('p','Загружаю тесты…','feature-notice');
  const result = element('div', undefined, 'feature-actions');
  const actions = element('div', undefined, 'feature-actions');
  status.setAttribute('role','status');
  host.replaceChildren(element('h4','Тесты в GitHub Actions'),status,actions,result);
  const active = () => !signal.aborted && host.isConnected && currentPanels.get(host) === version;
  const workflowUrl = repoUrl + '/actions/workflows/velia-tests.yml';
  let timer, branch, unavailable, lastStatus, requestId = '', busy = false;
  const refresh = button('Обновить статус', () => update(true));
  const start = button('Запустить в GitHub Actions', async () => {
    if (start.disabled || busy || unavailable || !branch) return;
    if (lastStatus === 'unknown' && !window.confirm('GitHub мог принять прошлый запрос. Запустить ещё раз?')) return;
    busy = true; start.disabled = true; refresh.disabled = true;
    status.textContent = 'Отправляю запрос в GitHub…';
    requestId ||= crypto.randomUUID();
    try {
      const data = await request(path, {signal,method:'POST',data:{request_id:requestId}});
      if (!active()) return;
      requestId = '';
      render(data.run);
    } catch (error) {
      if (!active()) return;
      if (MESSAGES[error.code]) requestId = '';
      status.textContent = MESSAGES[error.code] || error.message || 'Не удалось запустить тесты.';
      start.disabled = false;
    } finally {
      busy = false;
      refresh.disabled = false;
    }
  });
  start.disabled = true;
  actions.append(start, refresh);
  const errorMessage = (error) => MESSAGES[error.code] || error.message || 'Не удалось обновить статус.';

  function render(run) {
    clearTimeout(timer);
    lastStatus = run?.status;
    const pending = run && ACTIVE.has(run.status);
    start.disabled = Boolean(unavailable || pending);
    result.replaceChildren();
    const messages = {
      dispatching:'GitHub ещё принимает запрос', queued:'Тесты в очереди', in_progress:'Тесты выполняются',
      unknown:'GitHub мог принять запрос. Проверь список запусков перед повтором.',
      failed:'GitHub отклонил последний запрос.',
    };
    let text = '';
    if (run) {
      text = run.status === 'completed' ? RESULTS[run.conclusion] || 'Тесты завершены' :
        messages[run.status] || 'Тесты в очереди';
    }
    if (unavailable) text = [text, MESSAGES[unavailable]].filter(Boolean).join('. ');
    status.textContent = text || (branch ? `Запуск в ветке ${branch}.` : '');
    if (Number.isSafeInteger(run?.id) && run.id > 0) {
      result.append(externalLink(element,'Открыть запуск в GitHub',repoUrl + '/actions/runs/' + run.id));
    } else {
      result.append(externalLink(element,'Открыть Actions в GitHub',unavailable === 'project_tests_workflow_missing' ? repoUrl + '/actions' : workflowUrl));
    }
    if (unavailable === 'project_tests_reconnect') {
      const reconnect = button('Подтвердить аккаунт GitHub', async () => {
        reconnect.disabled = true;
        try {
          await openGitHubAuthorization(request, signal);
        } catch (error) {
          if (active()) status.textContent = errorMessage(error);
        } finally {
          reconnect.disabled = false;
        }
      });
      result.append(reconnect);
    }
    if (pending) timer = setTimeout(() => update(), 12000);
  }

  async function update(reload = false) {
    if (!active() || busy) return;
    busy = true;
    refresh.disabled = true;
    clearTimeout(timer);
    try {
      const data = await request(path + (reload ? '' : '/latest'), {signal});
      if (!active()) return;
      if (reload) {
        branch = data.branch;
        unavailable = data.reason;
      }
      render(data.run);
    } catch (error) {
      if (active()) status.textContent = errorMessage(error);
    } finally {
      busy = false;
      refresh.disabled = false;
    }
  }

  signal.addEventListener('abort', () => clearTimeout(timer), {once:true});
  await update(true);
}
