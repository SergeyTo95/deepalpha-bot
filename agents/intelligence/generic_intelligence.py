
def build_generic_driver_templates():
    return {
        "company_product_release": {"yes": [], "no": [], "required": ["official roadmap", "release window", "company statements"]},
        "legal_regulatory_approval": {"yes": [], "no": [], "required": ["regulator docket", "deadline", "official filings"]},
        "official_confirmation_event": {
            "yes": ["definitive official confirmation matching market wording"],
            "no": ["no definitive official confirmation by deadline", "explicit denial or non-confirmation language"],
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
