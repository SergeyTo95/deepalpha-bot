# Browser syntax highlighting

`web/syntax-vendor.mjs` is the unmodified common-language ES module from
Highlight.js 11.12.0, distributed under BSD-3-Clause.

- Source: https://github.com/highlightjs/cdn-release/blob/11.12.0/build/es/highlight.min.js
- Source SHA-256: `1ed8fc5256b738b8875b74e4d3f0754f60d6411fb034d618dfbc4e6e6c9999d7`
- Full license: `web/syntax-LICENSE.txt` (included in the gateway image).

The app serves this pinned file itself; code snippets never go to a highlighting
service. The wrapper uses only explicit supported fence languages, with escaped
plain text for unknown languages, oversized blocks, or parser failures. No
auto-detection, DOM plugins, or evaluation of code snippets is enabled. During
animated streaming, rendering stays plain; the final render adds colors in both
themes. Highlighting is bounded to 16,000 UTF-16 code units per block and 64,000
per message. Copying still uses the code element's literal `textContent`.
