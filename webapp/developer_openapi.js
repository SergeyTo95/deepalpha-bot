(() => {
  function isRu() {
    return String(document.documentElement.lang || "ru").toLowerCase().startsWith("ru");
  }

  function mountDocumentationLinks() {
    const docs = document.getElementById("documentation");
    if (!docs || docs.dataset.openapiLinksReady === "1") return;
    docs.dataset.openapiLinksReady = "1";

    const section = document.createElement("section");
    section.className = "openapi-docs";
    section.innerHTML = isRu()
      ? `<h3>Интерактивная документация</h3>
         <p>OpenAPI 3.1 является каноническим машинным контрактом Developer API. Swagger UI позволяет авторизоваться test-ключом и выполнять запросы прямо в браузере.</p>
         <div class="button-row">
           <a class="primary button-link" href="/api/docs" target="_blank" rel="noreferrer">Открыть Swagger UI</a>
           <a class="secondary button-link" href="/api/openapi.json" target="_blank" rel="noreferrer">OpenAPI JSON</a>
           <a class="secondary button-link" href="/api/postman.json" target="_blank" rel="noreferrer">Postman collection</a>
         </div>`
      : `<h3>Interactive documentation</h3>
         <p>OpenAPI 3.1 is the canonical machine contract for the Developer API. Swagger UI lets you authorize with a test key and run requests directly in the browser.</p>
         <div class="button-row">
           <a class="primary button-link" href="/api/docs" target="_blank" rel="noreferrer">Open Swagger UI</a>
           <a class="secondary button-link" href="/api/openapi.json" target="_blank" rel="noreferrer">OpenAPI JSON</a>
           <a class="secondary button-link" href="/api/postman.json" target="_blank" rel="noreferrer">Postman collection</a>
         </div>`;
    docs.prepend(section);
  }

  const observer = new MutationObserver(mountDocumentationLinks);
  observer.observe(document.getElementById("appRoot") || document.body, {
    childList: true,
    subtree: true,
  });
  mountDocumentationLinks();
})();
