(() => {
  const patchedForms = new WeakSet();

  function isRussian() {
    return String(document.documentElement.lang || "ru").toLowerCase().startsWith("ru");
  }

  function patchPortal() {
    const ru = isRussian();

    document.querySelectorAll(".create-key-form").forEach((form) => {
      if (patchedForms.has(form)) return;
      patchedForms.add(form);
      for (const scope of ["analysis:run", "analysis:read"]) {
        const input = form.querySelector(`input[name="scope"][value="${scope}"]`);
        if (input) input.checked = true;
      }
    });

    const testNotice = document.querySelector("#appRoot > .notice");
    if (testNotice) {
      const paragraph = testNotice.querySelector("p");
      if (paragraph) {
        paragraph.textContent = ru
          ? "Test-ключи уже запускают Quick Analysis. Live-ключи включим после завершения beta-тестирования."
          : "Test keys can now run Quick Analysis. Live keys will open after beta testing is complete.";
      }
    }

    const docs = document.getElementById("documentation");
    if (docs && docs.dataset.quickAnalysisReady !== "1") {
      docs.dataset.quickAnalysisReady = "1";
      docs.innerHTML = ru
        ? `<h2>Быстрый старт</h2>
           <p>Ключ передаётся только сервером вашего проекта в заголовке Authorization. Не размещайте его в браузере или мобильном приложении.</p>
           <h3>Доступно сейчас</h3>
           <pre>GET  /api/v1/account\nGET  /api/v1/usage\nGET  /api/v1/capabilities\nPOST /api/v1/analyses\nGET  /api/v1/analyses/{job_id}</pre>
           <pre>curl -X POST https://YOUR-DEEPALPHA-HOST/api/v1/analyses \\
  -H "Authorization: Bearer da_test_..." \\
  -H "Idempotency-Key: request_01J_example" \\
  -H "Content-Type: application/json" \\
  -d '{"market_url":"https://polymarket.com/event/example","mode":"quick","language":"ru"}'</pre>
           <p class="success-text">Quick Analysis API работает асинхронно: POST резервирует credits и возвращает job_id, GET возвращает статус и результат.</p>
           <h3>Следующий этап</h3>
           <pre>GET /api/v1/opportunities\nmode=deep\nSigned webhooks</pre>`
        : `<h2>Quick start</h2>
           <p>Send the key only from your project server in the Authorization header. Never embed it in browser or mobile application code.</p>
           <h3>Available now</h3>
           <pre>GET  /api/v1/account\nGET  /api/v1/usage\nGET  /api/v1/capabilities\nPOST /api/v1/analyses\nGET  /api/v1/analyses/{job_id}</pre>
           <pre>curl -X POST https://YOUR-DEEPALPHA-HOST/api/v1/analyses \\
  -H "Authorization: Bearer da_test_..." \\
  -H "Idempotency-Key: request_01J_example" \\
  -H "Content-Type: application/json" \\
  -d '{"market_url":"https://polymarket.com/event/example","mode":"quick","language":"en"}'</pre>
           <p class="success-text">Quick Analysis API is asynchronous: POST reserves credits and returns a job_id, while GET returns status and result.</p>
           <h3>Next phase</h3>
           <pre>GET /api/v1/opportunities\nmode=deep\nSigned webhooks</pre>`;
    }
  }

  const observer = new MutationObserver(patchPortal);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  patchPortal();
})();
