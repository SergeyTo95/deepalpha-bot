const diagrams = /^(?:flowchart|graph|sequenceDiagram|stateDiagram(?:-v2)?|classDiagram|erDiagram|gantt|pie|mindmap)\b/u;

export function diagramAllowed(source) {
  return source.length <= 6000 && diagrams.test(source) && !/%%\s*\{|^\s*click\s|^\s*---\s*$/mu.test(source);
}

export function mountDiagramButtons(region, download) {
  for (const block of region.querySelectorAll('.code-block')) {
    const heading = block.querySelector('.code-heading');
    if (heading?.firstElementChild?.textContent.toLowerCase() !== 'mermaid') continue;
    const source = block.querySelector('code')?.textContent.trim() || '';
    if (!diagramAllowed(source)) continue;
    const show = document.createElement('button');
    show.type = 'button';
    show.textContent = 'Показать схему';
    heading.append(show);
    show.onclick = async () => {
      show.disabled = true;
      const output = document.createElement('div');
      output.className = 'diagram-preview';
      block.append(output);
      try {
        const {renderDiagram} = await import('/web/diagram-engine.mjs');
        if (!block.isConnected) return;
        const {svg} = await renderDiagram('diagram-' + crypto.randomUUID(), source, output);
        if (!block.isConnected) return;
        const xml = new DOMParser().parseFromString(svg, 'image/svg+xml');
        if (xml.querySelector('parsererror') || xml.documentElement.localName !== 'svg') throw Error('Invalid diagram');
        for (const element of xml.querySelectorAll('script,foreignObject,iframe,image,link,animate,set')) element.remove();
        for (const element of [xml.documentElement, ...xml.querySelectorAll('*')]) {
          for (const attribute of [...element.attributes]) {
            if (/^on/iu.test(attribute.localName) ||
                attribute.localName === 'href' && !attribute.value.startsWith('#')) element.removeAttributeNode(attribute);
          }
        }
        const clean = new XMLSerializer().serializeToString(xml);
        const img = document.createElement('img');
        const url = URL.createObjectURL(new Blob([clean], {type:'image/svg+xml'}));
        img.onload = img.onerror = () => URL.revokeObjectURL(url);
        img.src = url;img.alt = 'Схема из ответа Велии';
        const save = document.createElement('button');
        save.type = 'button';save.textContent = 'Скачать SVG';
        save.onclick = () => download(clean,'VELIA-diagram.svg','image/svg+xml');
        output.replaceChildren(img,save);
        show.hidden = true;
      } catch {
        output.textContent = 'Не удалось построить схему из этого блока.';
      } finally { show.disabled = false; }
    };
  }
}
