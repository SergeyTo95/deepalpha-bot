
def build_tennis_driver_templates():
    return {
        "tennis_head_to_head": {
            "yes": [
                {"id": "surface_fit", "label": "Surface fit", "description": "Surface profile favors target player.", "impact": "high", "data_needed": ["surface record", "recent form"]}
            ],
            "no": [
                {"id": "fitness_risk", "label": "Fitness risk", "description": "Injury/fatigue risk weakens target side.", "impact": "high", "data_needed": ["injury updates", "schedule load"]}
            ],
            "required": ["surface", "recent form", "injury/fatigue", "head-to-head context", "serve/return matchup"],
        }
    }
