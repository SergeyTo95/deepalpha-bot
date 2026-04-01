from dataclasses import dataclass, asdict
from typing import Dict, Any


@dataclass
class AnalysisRecord:
    url: str
    question: str
    category: str
    market_probability: str
    system_probability: str
    confidence: str
    reasoning: str
    main_scenario: str
    alt_scenario: str
    conclusion: str
    created_at: str = ""

    @classmethod
    def from_result(cls, url: str, result: Dict[str, Any]) -> "AnalysisRecord":
        return cls(
            url=url,
            question=str(result.get("question", "")),
            category=str(result.get("category", "")),
            market_probability=str(result.get("market_probability", "")),
            system_probability=str(result.get("probability", "")),
            confidence=str(result.get("confidence", "")),
            reasoning=str(result.get("reasoning", "")),
            main_scenario=str(result.get("main_scenario", "")),
            alt_scenario=str(result.get("alt_scenario", "")),
            conclusion=str(result.get("conclusion", "")),
            created_at=str(result.get("created_at", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
