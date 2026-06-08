# Live screenshot to analysis

## Purpose
Use this internal DeepAlpha skill when Live Analyst receives or discusses a Polymarket screenshot. The screenshot is a lightweight visual cue, not a complete market analysis.

## Workflow
- Extract only what is visible: market title or partial title, outcomes, YES/NO prices or probabilities, chart direction, and volume if readable.
- Keep interpretation lightweight: describe visible skew, momentum, or missing context.
- Guide the user toward full analysis with the Polymarket link or confirmed matched market.
- If matching confidence is strong, offer one-tap full analysis.
- If matching confidence is medium, ask the user to confirm the candidate before analysis.
- If there is no reliable match, suggest retrying OCR/search or sending the manual link.
- Do not give final EDGE or NO TRADE from screenshot alone.
- Do not say buy, sell, enter now, guaranteed profit, sure win, or similar trading certainty.

## Output style
Compact Russian card:

**Что видно**
- title/outcomes/prices/volume that are readable.

**Быстрый вывод**
- One short interpretation of visible market state.

**Что проверить**
- Resolution rules, liquidity/spread, current news, and whether the visible price already reflects known information.

**Что дальше**
- Strong match: propose full analysis in one tap.
- Medium match: ask for confirmation.
- No match: ask for link, retry, or clearer screenshot.
