from typing import Dict, List, Literal, TypedDict


DecisionType = Literal["CONSIDER", "WATCH", "WAIT", "NO TRADE"]


class ValueDecision(TypedDict):
    version: str
    market_price: Dict[str, float]
    edge: Dict[str, float]
    edge_range: Dict[str, Dict[str, float]]
    decision: DecisionType
    best_side: str
    entry_price: Dict[str, float]
    avoid_price: Dict[str, float]
    reason: List[str]
    risk_flags: List[str]



def empty_value_decision() -> ValueDecision:
    return {
        "version": "1.0",
        "market_price": {},
        "edge": {},
        "edge_range": {},
        "decision": "NO TRADE",
        "best_side": "NONE",
        "entry_price": {},
        "avoid_price": {},
        "reason": [],
        "risk_flags": [],
    }
