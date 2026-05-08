import re
from typing import Dict


class EventTypeAgent:
    def classify(self, question: str, market_options: Dict[str, float]) -> str:
        text = str(question or "")
        t = text.lower()
        options_count = len(market_options or {})

        if options_count > 2:
            return "generic_multi_outcome"

        if self._is_football_tournament_winner_group(t):
            return "football_tournament_winner_group"
        if self._is_football_tournament_advancement(t):
            return "football_tournament_advancement"
        if self._is_football_team_win(t):
            return "football_team_win"
        if self._is_crypto_threshold(t):
            return "crypto_price_threshold"
        if self._is_tennis_h2h(t):
            return "tennis_head_to_head"
        if self._is_company_product_release(t):
            return "company_product_release"
        if self._is_legal_regulatory(t):
            return "legal_regulatory_approval"
        return "generic_binary_event"

    def _is_football_team_win(self, t: str) -> bool:
        return bool(re.search(r"\bwill\s+.+?\s+(?:win|beat|defeat)\b", t)) and any(x in t for x in ["fc ", "arsenal", "chelsea", "real madrid", "bayern", "psg", "paris saint germain"])

    def _is_football_tournament_advancement(self, t: str) -> bool:
        return bool(re.search(r"\bwill\s+.+?\s+(?:reach|qualify\s+for|advance\s+to|make)\s+(?:the\s+)?(?:.+?\s+)?(?:semi-?final|quarter-?final|final)\b", t))

    def _is_football_tournament_winner_group(self, t: str) -> bool:
        return ("champions league" in t or "europa league" in t) and bool(re.search(r"\b(team from|english team|spanish team|german team|premier league team|from england|from spain|from germany)\b", t)) and ("winner" in t or "win" in t)

    def _is_crypto_threshold(self, t: str) -> bool:
        return any(x in t for x in ["btc", "bitcoin", "eth", "ethereum"]) and bool(re.search(r"\b(hit|reach|above|at least|over)\b", t)) and bool(re.search(r"\$\s?\d", t))

    def _is_tennis_h2h(self, t: str) -> bool:
        return bool(re.search(r"\bwill\s+.+?\s+(?:beat|defeat|win against)\s+.+", t)) or " vs " in t

    def _is_company_product_release(self, t: str) -> bool:
        return any(x in t for x in ["openai", "apple", "google", "microsoft"]) and any(x in t for x in ["release", "launch", "ship"])

    def _is_legal_regulatory(self, t: str) -> bool:
        return any(x in t for x in ["sec", "cftc", "doj", "regulator"]) and any(x in t for x in ["approve", "approval", "sue", "deny", "reject"])
