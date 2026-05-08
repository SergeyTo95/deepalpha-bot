
def build_generic_driver_templates():
    return {
        "company_product_release": {"yes": [], "no": [], "required": ["official roadmap", "release window", "company statements"]},
        "legal_regulatory_approval": {"yes": [], "no": [], "required": ["regulator docket", "deadline", "official filings"]},
        "generic_binary_event": {"yes": [], "no": [], "required": ["primary source confirmation", "resolution rule mapping"]},
        "generic_multi_outcome": {"yes": [], "no": [], "required": ["outcome definitions", "source confirmation by option"]},
    }
