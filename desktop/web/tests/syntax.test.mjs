import test from 'node:test';
import assert from 'node:assert/strict';
import {renderMarkdown, escapeHTML} from '../core.mjs';
import {highlightCode, CODE_BLOCK_LIMIT, CODE_RENDER_BUDGET} from '../syntax.mjs';

const fence = (language, code) => `\`\`\`${language}\n${code}\n\`\`\``;
const withoutSpans = html => html.replace(/<\/?span(?: class="[a-zA-Z0-9_ .-]+")?>/g, '');
test('common fence languages and aliases color tokens without altering source', () => {
  for (const [language, source] of [
    ['HTML', '<!DOCTYPE html>\n<a href="#home">Главная & 🦎</a>'],
    ['js', 'const name = "Велия"; // comment\nconsole.log(name);'],
    ['ts', 'const ready: boolean = true;'],
    ['py', '# comment\ndef hello():\n    return "Привет"'],
    ['css', 'body { color: #ffffff; margin: 12px; }'],
    ['json', '{"name": "Велия", "ready": true, "count": 42}'],
    ['sql', 'SELECT name FROM users WHERE id = 42;'],
    ['bash', 'echo "$HOME" # comment'],
    ['kotlin', 'val greeting = "Привет"'],
  ]) {
    const colored = highlightCode(source, language);
    assert.match(colored, /<span class="hljs-/);
    assert.equal(withoutSpans(colored), escapeHTML(source), language);
    assert.ok(renderMarkdown(fence(language, source)).includes(colored));
  }
});
test('executable HTML stays escaped even for recognized languages', () => {
  const source = '</code><script>alert("x")</script><img src=x onerror="alert(1)"> &lt;';
  for (const language of ['html', 'js', 'python', '"><svg onload=x>', '__proto__']) {
    const html = renderMarkdown(fence(language, source));
    assert.doesNotMatch(html, /<script|<img|<svg/i);
    assert.ok(html.includes('copy-code'));
    assert.equal(withoutSpans(html.match(/<pre><code>([\s\S]*?)<\/code><\/pre>/)[1]), escapeHTML(source));
  }
});
test('unknown, plain, streaming and oversized code remains literal', () => {
  for (const language of ['', 'unknown-language', 'text', 'plaintext']) {
    const source = 'const x = "<b>";';
    assert.ok(renderMarkdown(fence(language, source)).includes(`<code>${escapeHTML(source)}</code>`));
  }
  const source = 'const x = "<b>";';
  assert.ok(renderMarkdown(fence('js', source), {highlight: false}).includes(`<code>${escapeHTML(source)}</code>`));
  const large = 'x'.repeat(CODE_BLOCK_LIMIT + 1);
  assert.equal(highlightCode(large, 'js'), null);
  assert.ok(renderMarkdown(fence('js', large)).includes(`<code>${large}</code>`));
});
test('a message has a bounded highlighting budget and unfinished fences are safe', () => {
  const source = 'const x = 42;'.padEnd(CODE_BLOCK_LIMIT, ' ');
  const blocks = Math.floor(CODE_RENDER_BUDGET / source.length);
  const html = renderMarkdown(Array.from({length: blocks + 1}, () => fence('js', source)).join('\n'));
  assert.equal((html.match(/<code>const x = 42;/g) || []).length, 1);
  const partial = renderMarkdown('```html\n<div title="<unfinished');
  assert.doesNotMatch(partial, /<div title=/);
  assert.equal(withoutSpans(partial.match(/<pre><code>([\s\S]*?)<\/code><\/pre>/)[1]), escapeHTML('<div title="<unfinished'));
});
