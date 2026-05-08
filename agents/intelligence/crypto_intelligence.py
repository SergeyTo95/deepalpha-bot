
def build_crypto_driver_templates():
    return {
        "crypto_price_threshold": {
            "yes": [
                {"id": "price_momentum", "label": "Price momentum", "description": "Sustained upward momentum supports threshold hit.", "impact": "high", "data_needed": ["spot price trend", "volatility"]}
            ],
            "no": [
                {"id": "macro_risk", "label": "Macro risk", "description": "Risk-off regime may block threshold.", "impact": "high", "data_needed": ["macro catalysts", "volatility"]}
            ],
            "required": ["spot price", "distance to threshold", "volatility", "liquidity", "macro/regulatory catalyst"],
        }
    }
