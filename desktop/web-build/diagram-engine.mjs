import mermaid from 'mermaid';

mermaid.initialize({
  startOnLoad: false,
  securityLevel: 'strict',
  htmlLabels: false,
  maxTextSize: 6000,
  maxEdges: 150,
  suppressErrorRendering: true,
});

export const renderDiagram = (id, text, container) => mermaid.render(id, text, container);
