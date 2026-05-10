
def build_generic_driver_templates():
    return {
        "company_product_release": {"yes": [], "no": [], "required": ["official roadmap", "release window", "company statements"]},
        "legal_regulatory_approval": {"yes": [], "no": [], "required": ["regulator docket", "deadline", "official filings"]},
        "official_confirmation_event": {
            "yes": [
                {
                    "id": "definitive_official_confirmation",
                    "label": "Definitive Official Confirmation",
                    "description": "Official actor definitively confirms the event/fact using wording that matches market resolution rules.",
                    "impact": "very_high",
                    "data_needed": ["official statement", "market wording match", "primary source"],
                }
            ],
            "no": [
                {
                    "id": "no_definitive_confirmation_by_deadline",
                    "label": "No Definitive Confirmation By Deadline",
                    "description": "No qualifying official confirmation appears before the market deadline.",
                    "impact": "very_high",
                    "data_needed": ["deadline", "official statements", "absence of qualifying confirmation"],
                },
                {
                    "id": "denial_or_nonconfirmation_language",
                    "label": "Denial Or Non-Confirmation Language",
                    "description": "Official reports/statements deny the claim or use ambiguous/non-confirming language.",
                    "impact": "high",
                    "data_needed": ["agency report language", "official denial", "ambiguous wording"],
                },
            ],
            "required": [
                "official_statement",
                "agency_report_language",
                "white_house_or_cabinet_statement",
                "congressional_hearings",
                "declassified_documents",
                "credible_reporting_consensus",
                "wording_specificity",
                "ambiguity_risk",
                "deadline_sensitivity",
                "denial_or_nonconfirmation",
                "aaro_report_language",
                "pentagon_uap_reports",
                "congressional_uap_hearings",
                "declassified_uap_files",
                "extraterrestrial_wording_specificity",
                "uap_vs_alien_ambiguity",
            ],
        },
        "generic_binary_event": {"yes": [], "no": [], "required": ["primary source confirmation", "resolution rule mapping"]},
        "generic_multi_outcome": {"yes": [], "no": [], "required": ["outcome definitions", "source confirmation by option"]},
    }
