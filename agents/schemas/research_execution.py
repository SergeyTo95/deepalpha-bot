from typing import Any, Dict, List, Literal, TypedDict

ParserConfidence = Literal["low", "medium", "high"]
Priority = Literal["low", "medium", "high", "critical"]
QueryStatus = Literal["executed", "skipped", "failed", "not_available"]
Relevance = Literal["low", "medium", "high"]


class ResearchExecutionMarket(TypedDict):
    question: str
    market_type: str
    category_type: str
    subcategory: str
    event_type: str


class ResearchExecutionResult(TypedDict):
    title: str
    snippet: str
    url: str
    source: str
    published: str
    relevance: Relevance


class ExecutedQuery(TypedDict):
    query: str
    driver: str
    outcome_id: str
    outcome_label: str
    priority: Priority
    source_type: str
    status: QueryStatus
    results: List[ResearchExecutionResult]
    error: str


class CoverageAttempt(TypedDict):
    queries_total: int
    queries_executed: int
    queries_with_results: int
    sources_collected: int
    critical_queries_with_results: int
    critical_queries_total: int


class ResearchExecutionParser(TypedDict):
    name: str
    confidence: ParserConfidence


class ResearchExecution(TypedDict):
    version: str
    market: ResearchExecutionMarket
    executed_queries: List[ExecutedQuery]
    collected_sources: List[ResearchExecutionResult]
    coverage_attempt: CoverageAttempt
    notes: List[str]
    parser: ResearchExecutionParser



def safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def empty_research_execution() -> ResearchExecution:
    return {
        "version": "1.0",
        "market": {
            "question": "",
            "market_type": "unknown",
            "category_type": "other",
            "subcategory": "unknown",
            "event_type": "unknown",
        },
        "executed_queries": [],
        "collected_sources": [],
        "coverage_attempt": {
            "queries_total": 0,
            "queries_executed": 0,
            "queries_with_results": 0,
            "sources_collected": 0,
            "critical_queries_with_results": 0,
            "critical_queries_total": 0,
        },
        "notes": [],
        "parser": {
            "name": "research_executor_agent_v1",
            "confidence": "low",
        },
    }
