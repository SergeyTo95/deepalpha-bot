import json
from collections import Counter


def safe_json_loads(value):
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def extract_trading_plan(row):
    data = safe_json_loads(row.get("reasoning"))
    return data.get("trading_plan") if isinstance(data, dict) else None


def extract_sports_context(row):
    data = safe_json_loads(row.get("reasoning"))
    return data.get("sports_context") if isinstance(data, dict) else None


def extract_decision(row):
    tp = extract_trading_plan(row) or {}
    return tp.get("decision") or row.get("conclusion")


def extract_source_count(row):
    data = safe_json_loads(row.get("reasoning"))
    sources = data.get("sources") if isinstance(data, dict) else None
    return len(sources) if isinstance(sources, list) else 0


def extract_edge(row):
    tp = extract_trading_plan(row) or {}
    try:
        return float(tp.get("edge", 0) or 0)
    except Exception:
        return 0.0


def extract_recommended_action(row):
    tp = extract_trading_plan(row) or {}
    return str(tp.get("recommended_action", "") or "").upper()


def extract_data_quality(row):
    sc = extract_sports_context(row) or {}
    return sc.get("data_quality")


def is_model_repeated_market(row):
    m = str(row.get("market_probability", "")).strip()
    s = str(row.get("system_probability", "")).strip()
    return bool(m and s and m == s)


def classify_analysis_quality(row):
    no_sources = extract_source_count(row) == 0
    edge_zero = extract_edge(row) == 0
    decision = str(extract_decision(row) or "").upper()
    low_conf = str(row.get("confidence", "")).lower() in {"low", "very low"}
    return {"no_sources": no_sources, "edge_zero": edge_zero, "decision": decision, "low_confidence": low_conf}


def summarize_quality(rows):
    total = len(rows)
    if total == 0:
        return {"total": 0}
    q = [classify_analysis_quality(r) for r in rows]
    decisions = Counter([x["decision"] for x in q if x["decision"]])
    return {
        "total": total,
        "no_sources": sum(1 for x in q if x["no_sources"]),
        "edge_zero": sum(1 for x in q if x["edge_zero"]),
        "low_confidence": sum(1 for x in q if x["low_confidence"]),
        "repeated_market": sum(1 for r in rows if is_model_repeated_market(r)),
        "decisions": dict(decisions),
        "sports_usage": sum(1 for r in rows if extract_sports_context(r)),
        "trading_plan_usage": sum(1 for r in rows if extract_trading_plan(r)),
        "turbo_usage": sum(1 for r in rows if "turbo" in str(r.get("category", "")).lower()),
        "avg_edge": round(sum(extract_edge(r) for r in rows) / total, 2),
    }
