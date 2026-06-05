import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None))
sys.modules.setdefault(
    "services.live_analyst_admin_service",
    types.SimpleNamespace(get_max_image_size_bytes=lambda: 8 * 1024 * 1024),
)

from services import live_analyst_image_service as svc


def test_extract_text_from_gemini_candidate_content_parts():
    candidate = {"content": {"parts": [{"text": "hello "}, {"text": 42}, {"inline_data": {}}]}}

    assert svc._extract_text_from_gemini_candidate(candidate) == "hello 42"


def test_extract_text_from_gemini_candidate_output_text_fallbacks():
    assert svc._extract_text_from_gemini_candidate({"output": "from output"}) == "from output"
    assert svc._extract_text_from_gemini_candidate({"text": ["from", " text"]}) == "from text"


def test_payload_from_unstructured_vision_text_polymarket_rows():
    raw = """
    What will Dr. Oz say during the next White House press briefing?
    Tariff 51%
    Health care 54%
    """

    payload = svc._payload_from_unstructured_vision_text(raw)

    assert payload["screen_type"] == "polymarket"
    assert payload["market"] == "What will Dr. Oz say during the next White House press briefing?"
    assert "Tariff 51%" in payload["visible"]
    assert "Health care 54%" in payload["visible"]


def test_merge_polymarket_payloads_prefers_specific_crop_over_fallback():
    fallback = {
        "screen_type": "polymarket",
        "market": "Polymarket-рынок",
        "visible": "Видны исходы, график вероятностей и кнопки YES/NO; часть текста мелкая",
    }
    crop = {
        "screen_type": "polymarket",
        "market": "What will Dr. Oz say during the next White House press briefing?",
        "visible": "Tariff 51%, Health care 54%, видны YES/NO цены",
    }

    merged = svc._merge_polymarket_payloads(fallback, crop)

    assert merged["market"] == crop["market"]
    assert merged["visible"] == crop["visible"]


def test_format_live_image_summary_limit_and_required_sections():
    raw = '{"screen_type":"polymarket","market":"What will Dr. Oz say during the next White House press briefing?","visible":"Tariff 51%, Health care 54%, видны YES/NO цены","takeaway":"Исходы рядом с серединой диапазона; для вывода нужен полный анализ по ссылке."}'

    summary = svc._format_live_image_summary(raw)

    assert len(summary) <= 700
    assert "Рынок:" in summary
    assert "Быстрый вывод" in summary
    assert "🔍 Анализ" in summary
