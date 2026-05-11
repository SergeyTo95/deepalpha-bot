from dataclasses import asdict
from typing import Any, Dict, List


class TopAnalysisSpecialistBase:
    name = "specialist"
    role = "placeholder"
    provider_key = ""

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def safe_result(self, result_obj: Any, fallback: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if result_obj is None:
                return fallback
            if hasattr(result_obj, "__dataclass_fields__"):
                return asdict(result_obj)
            if isinstance(result_obj, dict):
                return result_obj
            return fallback
        except Exception:
            return fallback

    def normalize_list(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item is not None]
        return [str(value)]
