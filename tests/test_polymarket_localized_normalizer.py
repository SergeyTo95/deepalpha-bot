from services.polymarket_localized_normalizer import (
    extract_visible_prices_from_text,
    market_title_candidates,
    normalize_outcome_name,
    normalize_polymarket_screenshot_payload,
)


def test_russian_world_cup_title_normalization_candidates():
    candidates = market_title_candidates("Победитель Кубка мира", "ru")

    assert "2026 FIFA World Cup Winner" in candidates
    assert "World Cup Winner" in candidates


def test_russian_country_outcome_normalization():
    names = ["Испания", "Франция", "Португалия", "Англия"]

    assert [normalize_outcome_name(name, "ru") for name in names] == [
        "Spain",
        "France",
        "Portugal",
        "England",
    ]


def test_decimal_percentages_preserved_from_visible_text():
    prices = extract_visible_prices_from_text("Испания 16.7%\nФранция 16.4%\nПортугалия 11.8%\nАнглия 9.7%", "ru")

    assert prices[0]["probability"] == 16.7
    assert prices[1]["probability"] == 16.4
    assert prices[2]["probability"] == 11.8
    assert prices[3]["probability"] == 9.7


def test_screenshot_payload_normalization_maps_title_and_outcomes():
    payload = normalize_polymarket_screenshot_payload(
        {
            "market_title_original": "Победитель Кубка мира",
            "outcomes_original": ["Испания", "Франция", "Португалия", "Англия"],
            "ui_language": "ru",
        }
    )

    assert payload["market_title_canonical"] == "2026 FIFA World Cup Winner"
    assert payload["outcomes_canonical"] == ["Spain", "France", "Portugal", "England"]
