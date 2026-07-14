from pathlib import Path

def test_squad_api_routes_registered():
    src=Path('web.py').read_text()
    assert '/api/polywar/squads/visible' in src
    assert '/api/polywar/squads/{id}/support' in src
    assert 'get_polywar_visible_squads' in src

def test_visible_endpoint_is_readonly_no_schema_init_in_handler():
    src=Path('web.py').read_text()
    block=src[src.index('async def handle_polywar_squads_visible_api'):src.index('async def handle_polywar_squad_support_api')]
    assert 'init_squad_schema' not in block
    assert 'process_squad_tick' not in block
