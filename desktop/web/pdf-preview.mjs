const citationPattern = /\[\[PDF:([^\]\r\n|]{1,180})\|([1-9]\d{0,3})\]\]/gu;

export function readablePdfReferences(text) {
  return String(text ?? '').replace(citationPattern, (_token, _name, page) => 'стр. ' + page);
}

export function pdfReferences(text) {
  const matches = String(text ?? '').matchAll(citationPattern);
  const references = new Map();
  for (const [marker, name, page] of matches) {
    references.set(marker, {name: name.trim(), page:Number(page)});
    if (references.size >= 12) break;
  }
  return [...references.values()];
}

export async function mountPDF(body, file, initialPage, signal, button) {
  const pdf = await import('/web/pdf-engine.mjs');
  signal.throwIfAborted();
  pdf.GlobalWorkerOptions.workerSrc = '/web/pdf-worker.mjs';
  const data = new Uint8Array(await file.arrayBuffer());
  signal.throwIfAborted();
  const task = pdf.getDocument({data, isEvalSupported:false, useWasm:false, disableFontFace:true, useSystemFonts:true});
  signal.addEventListener('abort', () => { task.destroy().catch(() => {}); }, {once:true});
  const documentPDF = await task.promise;
  signal.throwIfAborted();

  const navigation = document.createElement('nav');
  navigation.className = 'file-pager';
  const canvas = document.createElement('canvas');
  canvas.className = 'file-pdf-page';
  const label = document.createElement('span');
  label.setAttribute('aria-live','polite');
  const previous = button('Назад', () => show(pageNumber - 1));
  const next = button('Дальше', () => show(pageNumber + 1));
  navigation.append(previous, label, next);
  body.append(navigation, canvas);
  let pageNumber = initialPage;
  if (initialPage > documentPDF.numPages) {
    const note = document.createElement('p');
    note.textContent = `В этом PDF нет страницы ${initialPage}.`;
    body.prepend(note);
    pageNumber = 1;
  }

  async function show(target) {
    previous.disabled = next.disabled = true;
    try {
      const page = await documentPDF.getPage(target);
      signal.throwIfAborted();
      const base = page.getViewport({scale:1});
      const width = Math.max(200, body.clientWidth - 8);
      const scale = Math.min(width / base.width * Math.min(devicePixelRatio || 1, 2),
        Math.sqrt(3000000 / (base.width * base.height)));
      const viewport = page.getViewport({scale});
      canvas.width = Math.max(1, Math.floor(viewport.width));
      canvas.height = Math.max(1, Math.floor(viewport.height));
      await page.render({canvas, canvasContext:canvas.getContext('2d'), viewport}).promise;
      page.cleanup();
      pageNumber = target;
      label.textContent = `Страница ${target} из ${documentPDF.numPages}`;
    } catch (error) {
      if (!signal.aborted) label.textContent = 'Не удалось показать страницу.';
    } finally {
      previous.disabled = pageNumber <= 1;
      next.disabled = pageNumber >= documentPDF.numPages;
    }
  }
  await show(pageNumber);
}
