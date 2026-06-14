import ast
import sys
import types
from io import BytesIO

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
    assert "поиск по скрину" in summary


def test_polymarket_screenshot_card_normalizes_visible_and_local_takeaway():
    visible = (
        "Tariff 51% (Buy Yes 87c, Buy No 86c); "
        "Health / Healthcare 54% (Buy Yes 91c, Buy No 84c); "
        "Alien / Alien.gov 53% (Buy Yes 91c, Buy No 86c); "
        "-No Qualifying Event- 52% (Buy Yes 94c, Buy No 91c); "
        "President 30+ times 54%"
    )
    raw_takeaway = (
        "The market is 'What will Dr. Oz say during the next White House press briefing?'. "
        "The first visible исход is 'Tariff' at 51%, with 'Buy Yes' at 87c and 'Buy No' at 86c. Other…"
    )
    raw = (
        '{"screen_type":"polymarket",'
        '"market":"What will Dr. Oz say during the next White House press briefing?",'
        '"visible":"' + visible + '",'
        '"takeaway":"' + raw_takeaway + '"}'
    )

    summary = svc._format_live_image_summary(raw)

    assert "Tariff — 51%" in summary
    assert "Health / Healthcare — 54%" in summary
    assert "Alien / Alien.gov — 53%" in summary
    assert "No Qualifying Event — 52%" in summary
    assert "President 30+ times — 54%" in summary
    assert "Buy Yes" not in summary
    assert "Buy No" not in summary
    assert "Other…" not in summary
    assert "The market is" not in summary
    assert "Видимые исходы держатся около 50–55%" in summary
    assert raw_takeaway not in summary
    assert "Что видно" in summary
    assert "Быстрый вывод" in summary
    assert "Что проверить" in summary
    assert "Что дальше" in summary
    assert "поиск по скрину" in summary
    assert len(summary) <= svc.LIVE_IMAGE_SUMMARY_LIMIT


def test_failed_or_empty_full_extraction_short_max_tokens_payload_empty():
    assert svc._is_failed_or_empty_full_extraction({}, "abc", "MAX_TOKENS") is True


def test_failed_or_empty_full_extraction_empty_raw_and_payload():
    assert svc._is_failed_or_empty_full_extraction({}, "", "") is True


def test_failed_or_empty_full_extraction_useful_polymarket_payload():
    payload = {"screen_type": "polymarket", "market": "X", "visible": "Tariff 51%"}

    assert svc._is_failed_or_empty_full_extraction(payload, "...", "") is False


def test_crop_trigger_allows_failed_max_tokens_without_polymarket_marker():
    payload = {}
    text = "abc"
    finish_reason = "MAX_TOKENS"
    context_text = ""

    failed_full_extraction = svc._is_failed_or_empty_full_extraction(payload, text, finish_reason)
    attempt_crops = failed_full_extraction or svc._should_attempt_crop_extraction(payload, text, context_text)

    assert failed_full_extraction is True
    assert attempt_crops is True


def test_crop_trigger_preserves_useful_polymarket_behavior():
    payload = {
        "screen_type": "polymarket",
        "market": "What will Dr. Oz say during the next White House press briefing?",
        "visible": "Tariff 51%, Health care 54%, видны YES/NO цены",
    }
    text = '{"screen_type":"polymarket","market":"What will Dr. Oz say during the next White House press briefing?","visible":"Tariff 51%, Health care 54%, видны YES/NO цены"}'
    context_text = ""

    failed_full_extraction = svc._is_failed_or_empty_full_extraction(payload, text, "")
    attempt_crops = failed_full_extraction or svc._should_attempt_crop_extraction(payload, text, context_text)

    assert failed_full_extraction is False
    assert attempt_crops is True


def test_generic_live_image_payload_detects_old_fallback_phrase():
    summary = "Содержимое видно не полностью, поэтому точные детали лучше уточнить вопросом или текстом."

    assert svc._is_generic_live_image_payload({"screen_type": "generic", "summary": summary}, summary) is True


def test_generic_live_image_payload_keeps_specific_polymarket_payload():
    payload = {
        "screen_type": "polymarket",
        "market": "What will Dr. Oz say during the next White House press briefing?",
        "visible": "Tariff — 51%, Health — 54%",
    }

    assert svc._is_generic_live_image_payload(payload, "") is False


def test_build_nested_screenshot_crops_returns_labeled_png_crops_when_pillow_available():
    if svc.Image is None:
        return

    image = svc.Image.new("RGB", (1000, 1600), "white")
    output = BytesIO()
    image.save(output, format="PNG")

    crops = svc._build_nested_screenshot_crops(output.getvalue(), "image/png")
    labels = [label for label, _crop_bytes, crop_mime in crops if crop_mime == "image/png"]

    assert "nested_right_preview" in labels
    assert "nested_upper_media" in labels
    assert "nested_center_media" in labels
    assert "nested_full_without_chat_header" in labels


def test_generic_fallback_copy_points_to_original_polymarket_or_link():
    summary = svc._format_live_image_summary('{"screen_type":"generic","summary":"Содержимое видно не полностью, поэтому точные детали лучше уточнить вопросом или текстом."}')

    assert "оригинальный скрин Polymarket" in summary or "ссылку на рынок" in summary


def test_existing_polymarket_formatter_keeps_required_card_sections_and_edge_copy():
    raw = '{"screen_type":"polymarket","market":"What will Dr. Oz say during the next White House press briefing?","visible":"Tariff — 51%, Health — 54%","takeaway":"Видимые исходы около середины диапазона."}'

    summary = svc._format_live_image_summary(raw)

    assert "Что видно" in summary
    assert "Быстрый вывод" in summary
    assert "Что проверить" in summary
    assert "Что дальше" in summary
    assert "EDGE / NO TRADE" in summary


class _GeminiResponse:
    def __init__(self, status_code, data=None, text=""):
        self.status_code = status_code
        self._data = data
        self.text = text or ("{}" if data is not None else "")

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def _max_tokens_candidate(text=""):
    parts = [] if text == "" else [{"text": text}]
    return {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"role": "model", "parts": parts}}]}


def test_get_live_image_vision_models_primary_first_no_duplicates():
    models = svc._get_live_image_vision_models("gemini-2.5-flash")

    assert models[0] == "gemini-2.5-flash"
    assert "gemini-2.5-flash-lite" in models
    assert "gemini-2.0-flash" in models
    assert len(models) == len(set(models))


def test_retryable_empty_max_tokens_response_detection():
    assert svc._is_retryable_empty_max_tokens("MAX_TOKENS", "{}") is True
    assert svc._is_retryable_empty_max_tokens("STOP", "{}") is False
    assert svc._is_retryable_empty_max_tokens("MAX_TOKENS", "x" * 40) is False


def test_json_mode_fallback_selected_for_max_tokens_empty(monkeypatch):
    calls = []
    responses = [
        _GeminiResponse(200, _max_tokens_candidate("")),
        _GeminiResponse(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"screen_type":"polymarket","market":"M","visible":"Tariff 51%","takeaway":"T"}'}]}}]}),
    ]

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return responses.pop(0)

    monkeypatch.setattr(svc.requests, "post", fake_post)

    text, finish = svc._call_gemini_vision("key", "gemini-2.0-flash", 10, "long prompt", b"abc", "image/png", 1024)

    assert finish == "STOP"
    assert "Tariff 51%" in text
    assert calls[0]["generationConfig"].get("responseMimeType") == "application/json"
    assert "responseMimeType" not in calls[1]["generationConfig"]
    assert calls[1]["contents"][0]["parts"][0]["text"].startswith("Extract visible text")


def test_thinking_config_retry_path_unsupported(monkeypatch):
    calls = []
    responses = [
        _GeminiResponse(400, {"error": "unsupported thinkingConfig"}, "unsupported thinkingConfig"),
        _GeminiResponse(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"screen_type":"generic","summary":"ok"}'}]}}]}),
    ]

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return responses.pop(0)

    monkeypatch.setattr(svc.requests, "post", fake_post)

    text, finish = svc._call_gemini_vision("key", "gemini-2.5-flash", 10, "prompt", b"abc", "image/png", 1024)

    assert finish == "STOP"
    assert "generic" in text
    assert "thinkingConfig" in calls[0]["generationConfig"]
    assert "thinkingConfig" not in calls[1]["generationConfig"]


def test_model_fallback_retries_after_max_tokens_empty(monkeypatch):
    urls = []
    responses = [
        _GeminiResponse(200, _max_tokens_candidate("")),
        _GeminiResponse(200, _max_tokens_candidate("")),
        _GeminiResponse(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"screen_type":"polymarket","market":"M","visible":"Health 54%","takeaway":"T"}'}]}}]}),
    ]

    def fake_post(url, headers, json, timeout):
        urls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(svc.requests, "post", fake_post)

    text, finish = svc._call_gemini_vision("key", "gemini-2.5-flash", 10, "prompt", b"abc", "image/png", 1024)

    assert finish == "STOP"
    assert "Health 54%" in text
    assert "models/gemini-2.5-flash:generateContent" in urls[0]
    assert "models/gemini-2.5-flash-lite:generateContent" in urls[-1]


def test_live_image_metadata_for_polymarket_payload():
    payload = {
        "screen_type": "polymarket",
        "market": "What will Dr. Oz say during the next White House press briefing?",
        "visible": "Tariff 51%, Health care 54%",
        "takeaway": "Нужен полный анализ.",
    }
    raw = '{"screen_type":"polymarket"}'
    summary = svc._format_polymarket_summary(payload)

    metadata = svc._build_live_image_metadata(payload, raw, "", summary)

    assert metadata["screen_type"] == "polymarket"
    assert metadata["market"] == payload["market"]
    assert "Tariff" in metadata["visible"]
    assert metadata["takeaway"]


def test_new_live_image_callback_data_under_telegram_limit():
    callbacks = [
        "live_img_run_full_analysis",
        "live_img_full_analysis_help",
        "live_img_explain_edge",
        "live_img_risks",
        "live_img_confirm_candidate_analysis",
        "live_img_retry_market_resolution",
    ]

    assert all(len(callback.encode("utf-8")) <= 64 for callback in callbacks)


def test_live_image_keyboard_source_gates_auto_run_to_strong_confidence():
    source = __import__("pathlib").Path("telegram_bot.py").read_text()

    assert "LIVE_IMAGE_STRONG_CONFIDENCE_THRESHOLD = 0.82" in source
    assert "LIVE_IMAGE_MEDIUM_CONFIDENCE_THRESHOLD = 0.70" in source
    assert "_is_live_image_strong_market_match(resolved_market)" in source
    medium_branch = source.split('elif resolved_market and resolved_market.get("url") and _is_live_image_medium_market_match(resolved_market):', 1)[1]
    medium_branch = medium_branch.split("else:", 1)[0]
    assert "LIVE_IMAGE_RUN_FULL_ANALYSIS_CALLBACK" not in medium_branch
    assert "LIVE_IMAGE_CONFIRM_CANDIDATE_ANALYSIS_CALLBACK" in medium_branch
    assert "Да, анализировать этот рынок" in medium_branch
    assert "Искать ещё раз" in medium_branch


def test_live_image_keyboard_source_shows_auto_run_for_strong_confidence():
    source = __import__("pathlib").Path("telegram_bot.py").read_text()

    strong_branch = source.split('if resolved_market and resolved_market.get("url") and _is_live_image_strong_market_match(resolved_market):', 1)[1]
    strong_branch = strong_branch.split("elif resolved_market", 1)[0]
    assert "LIVE_IMAGE_RUN_FULL_ANALYSIS_CALLBACK" in strong_branch
    assert "Открыть рынок" in strong_branch


def test_live_image_keyboard_source_no_match_retry_and_manual_link_help():
    source = __import__("pathlib").Path("telegram_bot.py").read_text()

    no_match_branch = source.split("else:\n        kb.add(InlineKeyboardButton(\"🔎 Искать по скрину ещё раз\"", 1)[1]
    no_match_branch = no_match_branch.split("kb.add(\n        InlineKeyboardButton(\"🧠 Объясни edge\"", 1)[0]
    assert "LIVE_IMAGE_RETRY_MARKET_RESOLUTION_CALLBACK" in no_match_branch
    assert "Как отправить ссылку" in no_match_branch


def test_live_image_keyboard_callback_names_remain_unchanged():
    source = __import__("pathlib").Path("telegram_bot.py").read_text()
    keyboard = source.split("def get_live_image_keyboard", 1)[1]
    keyboard = keyboard.split("def _is_private_callback", 1)[0]

    assert 'InlineKeyboardButton("🔍 Запустить полный анализ", callback_data=LIVE_IMAGE_RUN_FULL_ANALYSIS_CALLBACK)' in keyboard
    assert 'InlineKeyboardButton("🔗 Открыть рынок", url=resolved_market.get("url"))' in keyboard
    assert 'InlineKeyboardButton("✅ Да, анализировать этот рынок", callback_data=LIVE_IMAGE_CONFIRM_CANDIDATE_ANALYSIS_CALLBACK)' in keyboard
    assert 'InlineKeyboardButton("🔎 Искать ещё раз", callback_data=LIVE_IMAGE_RETRY_MARKET_RESOLUTION_CALLBACK)' in keyboard
    assert 'InlineKeyboardButton("🔎 Искать по скрину ещё раз", callback_data=LIVE_IMAGE_RETRY_MARKET_RESOLUTION_CALLBACK)' in keyboard
    assert 'InlineKeyboardButton("🔍 Как отправить ссылку", callback_data=LIVE_IMAGE_FULL_ANALYSIS_HELP_CALLBACK)' in keyboard
    assert 'InlineKeyboardButton("🧠 Объясни edge", callback_data=LIVE_IMAGE_EXPLAIN_EDGE_CALLBACK)' in keyboard
    assert 'InlineKeyboardButton("⚠️ Риски", callback_data=LIVE_IMAGE_RISKS_CALLBACK)' in keyboard


def test_live_image_educational_callback_copy_is_polished_russian():
    source = __import__("pathlib").Path("telegram_bot.py").read_text()
    handler = source.split("async def live_image_educational_callback", 1)[1]
    handler = handler.split(
        "@dp.callback_query_handler(lambda c: c.data == LIVE_IMAGE_RETRY_MARKET_RESOLUTION_CALLBACK)", 1
    )[0]
    edge_text = (
        "🧠 Edge — это разница между ценой рынка и твоей оценкой вероятности.\n\n"
        "Пример:\n"
        "рынок даёт 51%, а анализ даёт 60% — появляется потенциальное преимущество.\n\n"
        "Но перед выводом нужно проверить правила, новости, ликвидность и спред.\n\n"
        "Финальный вывод EDGE / NO TRADE нужен только после полного анализа."
    )
    risk_text = (
        "⚠️ Главные риски Polymarket:\n\n"
        "• правила рынка можно понять неправильно;\n"
        "• новость может быть уже заложена в цену;\n"
        "• спред и ликвидность могут съесть edge;\n"
        "• скрин не показывает весь контекст.\n\n"
        "Поэтому по одному скрину нельзя честно дать EDGE / NO TRADE — нужна ссылка и полный анализ."
    )
    full_analysis_help_text = (
        "🔍 Для полного анализа отправь ссылку на рынок Polymarket.\n\n"
        "Тогда я проверю:\n"
        "• правила рынка;\n"
        "• текущие цены;\n"
        "• ликвидность и спред;\n"
        "• свежие новости;\n"
        "• разницу между ценой и AI-вероятностью.\n\n"
        "После этого можно дать вывод: EDGE или NO TRADE."
    )

    assert len(edge_text) <= 500
    assert len(risk_text) <= 450
    assert len(full_analysis_help_text) <= 450
    tree = ast.parse(source)
    callback_texts = [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "text" for target in node.targets)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]

    assert edge_text in callback_texts
    assert risk_text in callback_texts
    assert full_analysis_help_text in callback_texts
    assert "EDGE / NO TRADE" in edge_text
    assert "EDGE / NO TRADE" in risk_text
    assert "EDGE или NO TRADE" in full_analysis_help_text
    for forbidden in (
        "headline risk",
        "screenshot limitations",
        "resolution ambiguity",
        "slippage",
    ):
        assert forbidden not in handler


def test_live_image_confirm_candidate_callback_guards_and_uses_normal_analysis():
    source = __import__("pathlib").Path("telegram_bot.py").read_text()
    handler = source.split("async def live_image_confirm_candidate_analysis_callback", 1)[1]
    handler = handler.split("@dp.callback_query_handler(lambda c: c.data == LIVE_IMAGE_RUN_FULL_ANALYSIS_CALLBACK)", 1)[0]

    assert "get_live_analyst_active_session(uid)" in handler
    assert "if not session:" in handler
    assert "if not candidate_url:" in handler
    assert "candidate_confidence < LIVE_IMAGE_MEDIUM_CONFIDENCE_THRESHOLD" in handler
    assert "_is_polymarket_url(candidate_url)" in handler
    assert "_run_normal_polymarket_analysis(callback.message, url_override=candidate_url" in handler


def test_live_image_medium_candidate_not_stored_as_current_market_until_confirmed():
    source = __import__("pathlib").Path("telegram_bot.py").read_text()
    image_handler = source.split("async def live_image_handler", 1)[1]
    image_handler = image_handler.split("@dp.message_handler(lambda m: is_private_chat(m)", 1)[0]
    medium_branch = image_handler.split('elif resolved_market and resolved_market.get("url") and _is_live_image_medium_market_match(resolved_market):', 1)[1]
    medium_branch = medium_branch.split("else:", 1)[0]

    assert "_remember_live_image_candidate(uid, resolved_market)" in medium_branch
    assert "update_current_market_context" not in medium_branch


def test_polymarket_decimal_visible_values_are_not_split():
    visible = "Испания 16.7%; Франция 16.4%; Португалия 11.8%; Англия 9.7%"

    cleaned = svc._clean_polymarket_visible_text(visible)

    assert "Испания — 16.7%" in cleaned
    assert "Франция — 16.4%" in cleaned
    assert "16. — 7%" not in cleaned


def test_build_live_image_metadata_normalizes_localized_polymarket_payload():
    payload = {
        "screen_type": "polymarket_market",
        "ui_language": "ru",
        "market_title_original": "Победитель Кубка мира",
        "outcomes_original": ["Испания", "Франция", "Португалия", "Англия"],
        "visible": "Испания 16.7%, Франция 16.4%",
    }

    metadata = svc._build_live_image_metadata(payload, "", "", "")

    assert metadata["screen_type"] == "polymarket"
    assert metadata["market_title_canonical"] == "2026 FIFA World Cup Winner"
    assert metadata["outcomes_canonical"][:4] == ["Spain", "France", "Portugal", "England"]
    assert metadata["visible_prices"][0]["probability"] == 16.7
