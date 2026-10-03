const status = document.getElementById('status');
const submit = document.getElementById('submit');
document.getElementById('open').addEventListener('click', async () => {
  try { await window.veliaConnect.openPairing(); } catch { status.textContent = 'Не удалось открыть браузер.'; }
});
document.getElementById('connect').addEventListener('submit', async event => {
  event.preventDefault(); submit.disabled = true; status.textContent = 'Подключаю аккаунт…';
  try {
    const result = await window.veliaConnect.submit(document.getElementById('code').value);
    status.textContent = result.ok ? 'Аккаунт подключён. Запускаю Велию…' : result.error;
  } catch { status.textContent = 'Не удалось подключиться. Проверьте соединение и повторите.'; }
  finally { submit.disabled = false; }
});
