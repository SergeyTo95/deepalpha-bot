from services.velia_plugin_router import _bounded_search_query


def test_search_query_is_limited_to_brave_api_contract():
    source = " ".join(f"word{index}" for index in range(100))

    bounded = _bounded_search_query(source)

    assert len(bounded) <= 400
    assert len(bounded.split()) <= 50
    assert bounded.startswith("word0 word1")


def test_search_query_trims_empty_input():
    assert _bounded_search_query("   ") == ""
