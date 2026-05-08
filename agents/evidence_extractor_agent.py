import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from agents.schemas.evidence import StructuredEvidence, empty_structured_evidence


class EvidenceExtractorAgent:
    def extract(
        self,
        event_profile: Dict[str, Any],
        driver_map: Dict[str, Any],
        data_plan: Dict[str, Any],
        news_data: Dict[str, Any],
        market_data: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> StructuredEvidence:
        context = context or {}
        evidence = empty_structured_evidence(event_type=str((event_profile or {}).get("event_type") or "generic_binary_event"))

        sources = self._collect_sources(news_data, context)
        evidence["source_quality"]["raw_sources_count"] = len(sources)

        target_text = " ".join([
            str((event_profile or {}).get("target_entity") or ""),
            str((event_profile or {}).get("target_group") or ""),
            str((event_profile or {}).get("event_target") or ""),
            str((market_data or {}).get("question") or ""),
        ]).lower()

        facts: List[Dict[str, Any]] = []
        driver_coverage: Dict[str, int] = {}
        usable_source_indexes = set()

        all_drivers = self._all_drivers(driver_map)
        for idx, source in enumerate(sources):
            source_text, title, url, source_type, freshness = self._source_parts(source)
            if not source_text.strip():
                continue
            for direction, drv in all_drivers:
                if self._matches_driver(source_text, drv, driver_map):
                    claim = self._build_claim(source, source_text)
                    fact_direction = direction if direction in {"YES", "NO", "NEUTRAL"} else "UNKNOWN"
                    confidence = self._confidence_level(source_text, drv, target_text)
                    fact = {
                        "claim": claim,
                        "driver_id": str(drv.get("id") or ""),
                        "driver_label": str(drv.get("label") or ""),
                        "direction": fact_direction,
                        "impact": str(drv.get("impact") or "unknown"),
                        "confidence": confidence,
                        "source_title": title,
                        "source_url": url,
                        "source_type": source_type,
                        "freshness": freshness,
                    }
                    facts.append(fact)
                    usable_source_indexes.add(idx)
                    driver_id = fact["driver_id"]
                    if driver_id:
                        driver_coverage[driver_id] = driver_coverage.get(driver_id, 0) + 1

        evidence["facts"] = facts
        evidence["for_yes"] = [f["claim"] for f in facts if f.get("direction") == "YES"]
        evidence["for_no"] = [f["claim"] for f in facts if f.get("direction") == "NO"]
        evidence["neutral"] = [f["claim"] for f in facts if f.get("direction") in {"NEUTRAL", "UNKNOWN"}]
        evidence["source_quality"]["usable_sources_count"] = len(usable_source_indexes)
        evidence["source_quality"]["driver_coverage"] = driver_coverage
        evidence["source_quality"]["coverage_score"] = self._coverage_score(driver_map, driver_coverage)
        evidence["missing_driver_data"] = self._missing_driver_data(driver_map, data_plan, driver_coverage)
        evidence["contradictions"] = self._contradictions(facts)

        if not facts:
            evidence["notes"].append("No usable source evidence extracted from provided source fields.")
        return evidence

    def _collect_sources(self, news_data: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        candidates = [
            news_data.get("relevant_sources"),
            news_data.get("sources"),
            news_data.get("raw_sources"),
            (news_data.get("news_data") or {}).get("relevant_sources") if isinstance(news_data.get("news_data"), dict) else None,
            (news_data.get("news_data") or {}).get("sources") if isinstance(news_data.get("news_data"), dict) else None,
            (news_data.get("source_summary") or {}).get("sources") if isinstance(news_data.get("source_summary"), dict) else None,
            (news_data.get("source_summary") or {}).get("relevant_sources") if isinstance(news_data.get("source_summary"), dict) else None,
            context.get("relevant_sources") if isinstance(context, dict) else None,
            context.get("sources") if isinstance(context, dict) else None,
        ]
        for group in candidates:
            if isinstance(group, list):
                for item in group:
                    if isinstance(item, dict):
                        out.append(item)
                    elif isinstance(item, str):
                        out.append({"title": item})
        return out

    def _all_drivers(self, driver_map: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
        rows: List[Tuple[str, Dict[str, Any]]] = []
        for key, direction in (("yes_drivers", "YES"), ("no_drivers", "NO"), ("neutral_drivers", "NEUTRAL")):
            items = driver_map.get(key) if isinstance(driver_map, dict) else []
            if isinstance(items, list):
                for d in items:
                    if isinstance(d, dict):
                        rows.append((direction, d))
        return rows

    def _source_parts(self, source: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
        title = str(source.get("title") or source.get("headline") or "")
        snippet = str(source.get("snippet") or source.get("summary") or "")
        url = str(source.get("url") or "")
        source_type = str(source.get("source_type") or source.get("type") or "generic")
        published = str(source.get("published") or source.get("date") or "")
        text = f"{title} {snippet}".strip().lower()
        return text, title or snippet[:120], url, source_type, self._freshness(published)

    def _matches_driver(self, source_text: str, driver: Dict[str, Any], driver_map: Dict[str, Any]) -> bool:
        tokens = []
        tokens.extend(self._tokenize(str(driver.get("id") or "")))
        tokens.extend(self._tokenize(str(driver.get("label") or "")))
        tokens.extend(self._tokenize(str(driver.get("description") or "")))
        for term in driver.get("data_needed") or []:
            tokens.extend(self._tokenize(str(term)))
        for term in driver_map.get("must_find") or []:
            tokens.extend(self._tokenize(str(term)))
        for term in driver_map.get("high_impact_keywords") or []:
            tokens.extend(self._tokenize(str(term)))
        unique = [t for t in set(tokens) if len(t) > 2]
        if not unique:
            return False
        score = sum(1 for tok in unique if tok in source_text)
        return score >= 2

    def _tokenize(self, text: str) -> List[str]:
        return [x for x in re.split(r"[^a-zA-Z0-9$]+", text.lower()) if x]

    def _build_claim(self, source: Dict[str, Any], source_text: str) -> str:
        claim = str(source.get("snippet") or source.get("summary") or source.get("title") or source.get("headline") or "").strip()
        return claim or source_text[:140]

    def _confidence_level(self, source_text: str, driver: Dict[str, Any], target_text: str) -> str:
        label_hits = sum(1 for t in self._tokenize(str(driver.get("label") or "")) if len(t) > 3 and t in source_text)
        entity_match = bool(target_text and any(tok in source_text for tok in self._tokenize(target_text) if len(tok) > 3))
        if label_hits >= 2 and entity_match:
            return "high"
        if label_hits >= 1:
            return "medium" if entity_match else "low"
        return "unknown"

    def _coverage_score(self, driver_map: Dict[str, Any], coverage: Dict[str, int]) -> float:
        hv = []
        for side in ["yes_drivers", "no_drivers", "neutral_drivers"]:
            for d in driver_map.get(side) or []:
                if isinstance(d, dict) and str(d.get("impact") or "") in {"high", "very_high"}:
                    hv.append(str(d.get("id") or ""))
        hv = [x for x in hv if x]
        if not hv:
            return 0.0
        covered = sum(1 for d in set(hv) if coverage.get(d, 0) > 0)
        return round(covered / len(set(hv)), 3)

    def _missing_driver_data(self, driver_map: Dict[str, Any], data_plan: Dict[str, Any], coverage: Dict[str, int]) -> List[Dict[str, Any]]:
        missing: List[Dict[str, Any]] = []
        for side in ["yes_drivers", "no_drivers", "neutral_drivers"]:
            for d in driver_map.get(side) or []:
                if not isinstance(d, dict):
                    continue
                priority = str(d.get("impact") or "")
                driver_id = str(d.get("id") or "")
                if priority in {"high", "very_high"} and coverage.get(driver_id, 0) == 0:
                    missing.append({
                        "driver_id": driver_id,
                        "description": str(d.get("description") or d.get("label") or ""),
                        "priority": priority,
                        "why_missing": "No matched facts from current usable sources for this high-priority driver.",
                    })
        for x in (data_plan or {}).get("missing_data") or []:
            missing.append({
                "driver_id": "data_plan_missing",
                "description": str(x),
                "priority": "high",
                "why_missing": "Explicitly marked as missing by data_plan.",
            })
        return missing

    def _contradictions(self, facts: List[Dict[str, Any]]) -> List[str]:
        by_driver: Dict[str, set] = {}
        for fact in facts:
            driver_id = str(fact.get("driver_id") or "")
            direction = str(fact.get("direction") or "UNKNOWN")
            if not driver_id:
                continue
            by_driver.setdefault(driver_id, set()).add(direction)
        out = []
        for driver_id, dirs in by_driver.items():
            if "YES" in dirs and "NO" in dirs:
                out.append(f"Driver {driver_id} has both YES and NO directional facts.")
        return out

    def _freshness(self, published: str) -> str:
        if not published:
            return "unknown"
        try:
            raw = published.replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - dt).days
            if days <= 2:
                return "fresh"
            if days <= 10:
                return "recent"
            return "stale"
        except Exception:
            return "unknown"
