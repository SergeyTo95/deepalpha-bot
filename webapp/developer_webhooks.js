(() => {
  function isRu() {
    return String(document.documentElement.lang || "ru").toLowerCase().startsWith("ru");
  }

  function appendWebhookDocs() {
    const docs = document.getElementById("documentation");
    if (!docs || docs.dataset.signedWebhooksReady === "1") return;
    docs.dataset.signedWebhooksReady = "1";
    const section = document.createElement("section");
    section.className = "webhook-docs";
    section.innerHTML = isRu()
      ? `<h3>Signed Webhooks v1</h3>
         <p>Добавьте ключу scope <code>webhooks:manage</code>, создайте HTTPS endpoint и сохраните signing secret: он показывается только один раз.</p>
         <pre>POST /api/v1/webhooks
{
  "name": "production",
  "url": "https://example.com/deepalpha/webhook",
  "events": ["analysis.completed", "analysis.failed"]
}</pre>
         <p>Каждая доставка содержит заголовки:</p>
         <pre>X-DeepAlpha-Event
X-DeepAlpha-Delivery
X-DeepAlpha-Timestamp
X-DeepAlpha-Signature: v1=...</pre>
         <p>Подпись: <code>HMAC-SHA256(secret, timestamp + "." + raw_body)</code>. Проверяйте подпись по сырым байтам тела до JSON-разбора.</p>
         <pre>GET  /api/v1/webhooks
DELETE /api/v1/webhooks/{webhook_id}
POST /api/v1/webhooks/{webhook_id}/rotate-secret
GET  /api/v1/webhook-deliveries
GET  /api/v1/webhook-deliveries/{delivery_id}
POST /api/v1/webhook-deliveries/{delivery_id}/retry</pre>`
      : `<h3>Signed Webhooks v1</h3>
         <p>Add the <code>webhooks:manage</code> scope to the key, create an HTTPS endpoint, and save the signing secret. It is shown only once.</p>
         <pre>POST /api/v1/webhooks
{
  "name": "production",
  "url": "https://example.com/deepalpha/webhook",
  "events": ["analysis.completed", "analysis.failed"]
}</pre>
         <p>Every delivery includes:</p>
         <pre>X-DeepAlpha-Event
X-DeepAlpha-Delivery
X-DeepAlpha-Timestamp
X-DeepAlpha-Signature: v1=...</pre>
         <p>Signature: <code>HMAC-SHA256(secret, timestamp + "." + raw_body)</code>. Verify it against the raw request body before parsing JSON.</p>
         <pre>GET  /api/v1/webhooks
DELETE /api/v1/webhooks/{webhook_id}
POST /api/v1/webhooks/{webhook_id}/rotate-secret
GET  /api/v1/webhook-deliveries
GET  /api/v1/webhook-deliveries/{delivery_id}
POST /api/v1/webhook-deliveries/{delivery_id}/retry</pre>`;
    docs.appendChild(section);
  }

  const observer = new MutationObserver(appendWebhookDocs);
  observer.observe(document.getElementById("appRoot") || document.body, { childList: true, subtree: true });
  appendWebhookDocs();
})();
