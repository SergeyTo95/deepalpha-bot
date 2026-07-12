from pathlib import Path


def test_chunk_backend_uses_request_scoped_config_and_readonly_flow():
    src = Path('services/polywar_map_service.py').read_text()
    assert 'class PolyWarMapConfig' in src
    assert 'def load_map_config(conn)' in src
    assert 'terrain_at_with_config' in src
    assert 'start_owner_with_config' in src
    assert 'terrain_chunk_with_config' in src
    build = src.split('def build_chunks', 1)[1].split('def legacy_action_duplicate_response', 1)[0]
    forbidden = ['init_polywar_schema', 'init_polywar_map_schema', 'ensure_active_season_in_transaction', 'ensure_world_caught_up', 'ensure_capitals_initialized', 'get_or_create_player', 'conn.commit()']
    for token in forbidden:
        assert token not in build
    for token in ['CREATE ', 'ALTER ', 'INSERT ', 'UPDATE ', 'DELETE ', 'FOR UPDATE']:
        assert token not in build.upper()


def test_terrain_cache_key_includes_config_values():
    src = Path('services/polywar_map_service.py').read_text()
    fn = src.split('def terrain_chunk_with_config', 1)[1].split('def _terrain_chunk', 1)[0]
    assert 'config.width' in fn
    assert 'config.height' in fn
    assert 'config.starting_area_size' in fn
    assert 'bases_key' in fn


def test_read_endpoints_do_not_bootstrap_or_write():
    for path, fn_name in [
        ('services/polywar_sector_service.py', 'def get_sectors'),
        ('services/polywar_capital_service.py', 'def get_capitals'),
        ('services/polywar_governance_service.py', 'def get_governance'),
    ]:
        src = Path(path).read_text()
        body = src.split(fn_name, 1)[1].split('\ndef ', 1)[0]
        for token in ['init_polywar_schema', 'ensure_active_season_in_transaction', 'prepare_gameplay_mutation_in_transaction', 'ensure_capitals_initialized', 'initialize_sector', 'INSERT ', 'UPDATE ', 'DELETE ', 'FOR UPDATE']:
            assert token not in body
    assert '_synthetic_sector' in Path('services/polywar_sector_service.py').read_text()


def test_frontend_chunk_loader_grid_retry_and_initial_order():
    js = Path('webapp/polywar.js').read_text()
    assert 'this.ensureChunks().finally(() => Promise.allSettled([this.ensureSectors(), this.refreshCapitals(), this.refreshGovernance()]))' in js
    ensure = js.split('async ensureChunks', 1)[1].split('async ensureSectors', 1)[0]
    assert 'finally' in ensure
    assert '300' in ensure and '1000' in ensure
    assert 'Map data unavailable' in ensure
    assert 'Retry map' in js
    assert 'drawCellGrid' in js and 'drawSkeleton' in js
    assert 'this.drawCellGrid(ctx)' in js


def test_mobile_compact_sheet_buttons_stay_on_one_row():
    css = Path('webapp/polywar.css').read_text()
    assert '.compact-cell-sheet .sheet-actions .btn{grid-column:auto;width:auto}' in css
    assert 'grid-template-columns:minmax(0,1fr) auto' in css
