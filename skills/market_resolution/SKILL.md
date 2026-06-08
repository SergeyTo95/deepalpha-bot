# Market resolution matching

## Purpose
Guide safe matching from a screenshot, OCR text, title, or partial market description to the correct Polymarket market.

## Matching rules
- Use visible title, outcomes, named entities, dates, prices, and category hints together.
- Prefer official Gamma or Polymarket market data when matching candidates.
- Classify confidence as strong, medium, or no match.
- Strong match: title/entities/outcomes align clearly and no close competitor is plausible.
- Medium match: candidate looks likely but title or outcomes are partial; ask user to confirm before full analysis.
- No match: OCR/title is too weak, multiple close candidates exist, or key entities conflict.
- Reject ambiguous close candidates instead of guessing.
- Never auto-run full analysis on weak match.
- Do not guess. Ask for the link or clearer screenshot when confidence is not enough.
