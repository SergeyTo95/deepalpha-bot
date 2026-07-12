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
    assert 'bootstrapInitialLoad' in js and 'Promise.allSettled([this.ensureSectors(), this.refreshCapitals(), this.refreshGovernance()])' in js
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


def test_postgresql_get_capitals_uses_module_map_import(monkeypatch):
    from types import SimpleNamespace
    from services import polywar_capital_service as caps
    calls = []
    class Cursor:
        def execute(self, sql, params=()): calls.append(sql)
        def fetchall(self): return []
        def fetchone(self): return None
        @property
        def description(self): return []
    class Conn:
        def __init__(self): self.closed = False
        def cursor(self): return Cursor()
        def rollback(self): calls.append('rollback')
        def close(self): self.closed = True; calls.append('close')
    conn = Conn()
    monkeypatch.setattr(caps.polywar, '_is_sqlite', lambda c: False)
    monkeypatch.setattr(caps.polywar, 'get_connection', lambda: conn)
    monkeypatch.setattr(caps.m, 'begin_polywar_readonly', lambda c: calls.append('begin_readonly'))
    monkeypatch.setattr(caps.m, 'load_map_config', lambda c: SimpleNamespace(capital_siege_required=777))
    monkeypatch.setattr(caps.m, 'get_active_season_readonly', lambda c: {'id': 9})
    out = caps.get_capitals(123)
    assert out['ok'] and out['siege_required'] == 777
    assert calls[0] == 'begin_readonly'
    assert 'close' in calls


def test_postgresql_get_governance_uses_config_rules_without_min_contribution(monkeypatch):
    from types import SimpleNamespace
    from services import polywar_governance_service as gov
    calls = []
    class Cursor:
        def execute(self, sql, params=()): calls.append(sql)
        def fetchall(self): return []
        def fetchone(self): return None
        @property
        def description(self): return []
    class Conn:
        def cursor(self): return Cursor()
        def rollback(self): calls.append('rollback')
        def close(self): calls.append('close')
    rules = {'election_hours':24,'term_hours':168,'min_contribution':5,'min_members':2,'max_statement_length':280,'max_orders':5,'order_duration_hours':24}
    monkeypatch.setattr(gov.polywar, '_is_sqlite', lambda c: False)
    monkeypatch.setattr(gov.polywar, 'get_connection', lambda: Conn())
    monkeypatch.setattr(gov.m, 'begin_polywar_readonly', lambda c: calls.append('begin_readonly'))
    monkeypatch.setattr(gov.m, 'load_map_config', lambda c: SimpleNamespace(governance_rules=rules))
    monkeypatch.setattr(gov.m, 'get_active_season_readonly', lambda c: {'id': 9})
    monkeypatch.setattr(gov, 'min_contribution', lambda: (_ for _ in ()).throw(AssertionError('min_contribution called')))
    out = gov.get_governance(123)
    assert out['ok'] and out['rules']['min_contribution'] == 5
    assert out['nomination_eligibility']['eligible'] is False
    assert calls[0] == 'begin_readonly' and 'close' in calls


def test_legacy_terrain_helpers_build_full_config(monkeypatch):
    from services import polywar_map_service as m
    monkeypatch.setattr(m, 'map_width', lambda: 512)
    monkeypatch.setattr(m, 'map_height', lambda: 512)
    monkeypatch.setattr(m, 'chunk_size', lambda: 16)
    monkeypatch.setattr(m, 'starting_area_size', lambda: 15)
    monkeypatch.setattr(m, 'max_chunks_per_request', lambda: 9)
    monkeypatch.setattr(m, 'faction_base_positions', lambda width=None, height=None: {1: (20, 20)})
    monkeypatch.setattr(m, '_setting_int', lambda *args: args[1])
    assert m.terrain_at('seed', 1, 1)
    assert m._start_owner(20, 20) == 1
    chunk = m._terrain_chunk(1, 'seed', 0, 0, 16)
    assert len(chunk) == 16 and len(chunk[0]) == 16


def test_get_sectors_uses_config_sector_size_without_get_setting(monkeypatch):
    from types import SimpleNamespace
    from services import polywar_sector_service as sectors
    conn_count = {'n': 0}
    class Cursor:
        def execute(self, sql, params=()): pass
        def fetchall(self): return []
        def fetchone(self): return None
        @property
        def description(self): return []
    class Conn:
        def cursor(self): return Cursor()
        def rollback(self): pass
        def close(self): pass
    def get_conn(): conn_count['n'] += 1; return Conn()
    cfg = SimpleNamespace(width=1000, height=1000, sector_size=77, max_sectors_per_request=10)
    monkeypatch.setattr(sectors.polywar, 'get_connection', get_conn)
    monkeypatch.setattr(sectors.polywar, 'get_setting', lambda *a, **k: (_ for _ in ()).throw(AssertionError('get_setting called')))
    from services import polywar_map_service as map_service
    monkeypatch.setattr(map_service, 'load_map_config', lambda c: cfg)
    monkeypatch.setattr(map_service, 'get_active_season_readonly', lambda c: {'id': 3})
    monkeypatch.setattr(sectors, '_set_read_timeouts', lambda c: None)
    out = sectors.get_sectors(1, 0, 0, 0, 0)
    assert out['ok'] and out['sector_size'] == 77 and conn_count['n'] == 1
