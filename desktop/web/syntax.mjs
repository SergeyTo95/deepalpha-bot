import highlighter from './syntax-vendor.mjs';

// Explicit fence languages only: no costly or surprising automatic detection.
// The caller escapes the original source if a grammar or budget is unavailable.
export const CODE_BLOCK_LIMIT = 16000;
export const CODE_RENDER_BUDGET = 64000;
export function highlightCode(source, language) {
  const name = String(language).trim().toLowerCase();
  if (!source || source.length > CODE_BLOCK_LIMIT ||
      !/^[a-z0-9_+#.-]{1,40}$/.test(name) || !highlighter.getLanguage(name)) return null;
  try {
    return highlighter.highlight(source, {language: name, ignoreIllegals: true}).value;
  } catch {
    return null;
  }
}
