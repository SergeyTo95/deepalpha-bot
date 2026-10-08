"""Bounded, unambiguous JSON for model decisions; no provider dependencies."""
import json


def strict_json_loads(text, *, max_bytes=65536, max_depth=32):
    if not isinstance(text, str) or len(text.encode('utf-8')) > max_bytes:
        raise ValueError('invalid_structured_output')

    def reject_constant(_value):
        raise ValueError('invalid_structured_output')

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('invalid_structured_output')
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=object_pairs, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        raise ValueError('invalid_structured_output') from None
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > max_depth:
            raise ValueError('invalid_structured_output')
        if isinstance(item, float):
            import math
            if not math.isfinite(item):
                raise ValueError('invalid_structured_output')
        if isinstance(item, (dict, list)):
            pending.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
    return value
