import sqlite3, sys, uuid, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
import services.polywar_service as polywar
import services.polywar_map_service as m

@pytest.fixture()
def polydb(monkeypatch):
    uri=f"file:polywar_map_{uuid.uuid4().hex}?mode=memory&cache=shared"; keeper=sqlite3.connect(uri,uri=True,check_same_thread=False); keeper.row_factory=sqlite3.Row
    def connect():
        c=sqlite3.connect(uri,uri=True,check_same_thread=False,timeout=10); c.row_factory=sqlite3.Row; return c
    monkeypatch.setattr(polywar,'get_connection',connect); monkeypatch.setattr(polywar,'get_setting',lambda k,d='': d); monkeypatch.setattr(polywar,'get_airdrop_points_balance',lambda uid:{'total':0,'balance':0})
    c=connect(); polywar.init_polywar_schema(c); c.close(); yield connect; keeper.close()

def seed(polydb):
    c=polydb(); polywar.ensure_factions(c); s=polywar.ensure_active_season(c); c.close(); return s

def join(uid,fid): return polywar.join_faction(uid,fid)
def find_near(seedstr,bx,by,terrain=None,capturable=True):
    for y in range(0, m.map_height(), 17):
        for x in range(0, m.map_width(), 17):
            t=m.terrain_at(seedstr,x,y)
            if terrain and t==terrain: return x,y
            if not terrain and ((m.TERRAIN_COSTS[t] is not None)==capturable): return x,y
    raise AssertionError('not found')

def test_map_dimensions_bounds_terrain_and_secret(polydb):
    s=seed(polydb); assert m.map_width()==10000 and m.map_height()==10000 and m.chunk_size()==64
    assert m.in_bounds(0,0) and not m.in_bounds(10000,0)
    c=polydb(); row=c.execute('select secret_seed from polywar_seasons where id=?',(s['id'],)).fetchone(); secret=row['secret_seed']; c.close()
    assert m.terrain_at(secret,123,456)==m.terrain_at(secret,123,456)
    assert any(m.terrain_at(secret,x,y)!=m.terrain_at(secret+'x',x,y) for x in range(0,600,50) for y in range(0,600,50))
    chunk=m.build_chunks(1,[(0,0)]); assert 'secret_seed' not in str(chunk) and chunk['map_width']==10000
    with pytest.raises(ValueError, match='too_many_chunks'): m.build_chunks(1,[(i,0) for i in range(10)])

def test_starting_bases_safe_inside_and_non_overlapping(polydb):
    s=seed(polydb); c=polydb(); secret=c.execute('select secret_seed from polywar_seasons where id=?',(s['id'],)).fetchone()['secret_seed']; c.close()
    bases=m.get_starting_bases(); assert len(bases)==7
    seen=[]
    for b in bases:
        assert m.in_bounds(b['x'],b['y']); assert m.terrain_at(secret,b['x'],b['y']) not in {'water','river'}
        for o in seen: assert abs(b['x']-o['x'])>b['size'] or abs(b['y']-o['y'])>b['size']
        seen.append(b)

def test_capture_rules_energy_idempotency_stats(polydb):
    state=join(10,1); s=state['season']; c=polydb(); secret=c.execute('select secret_seed from polywar_seasons where id=?',(s['id'],)).fetchone()['secret_seed']; c.close(); bx,by=m.faction_base_positions()[1]
    x,y=bx+8,by # adjacent to старт square edge
    assert m.terrain_at(secret,x,y) not in {'water','river'}
    r=m.capture_cell(10,x,y,'a'); assert r['ok'] and r['energy']['current_energy']==9
    dup=m.capture_cell(10,x,y,'a'); assert dup['duplicate'] and dup['energy']['current_energy']==9
    c=polydb(); assert c.execute('select controlled_cells_count from polywar_faction_season_stats where season_id=? and faction_id=1',(s['id'],)).fetchone()[0]==1; c.close()
    with pytest.raises(ValueError, match='already_owned'): m.capture_cell(10,x,y,'b')
    with pytest.raises(ValueError, match='not_adjacent'): m.capture_cell(10,bx+30,by+30,'diag')
    with pytest.raises(ValueError, match='not_adjacent'): m.capture_cell(10,5000,5000,'far')

def test_capture_without_faction_insufficient_and_terrain_costs(polydb):
    s=seed(polydb); c=polydb(); secret=c.execute('select secret_seed from polywar_seasons where id=?',(s['id'],)).fetchone()['secret_seed']; c.close(); bx,by=m.faction_base_positions()[1]
    with pytest.raises(ValueError, match='faction_required'): m.capture_cell(99,bx+8,by,'nf')
    join(11,1); c=polydb(); c.execute('update polywar_players set current_energy=0 where user_id=11'); c.commit(); c.close()
    with pytest.raises(ValueError, match='insufficient_energy'): m.capture_cell(11,bx+8,by,'low')
    assert m.TERRAIN_COSTS['mountain']==2 and m.TERRAIN_COSTS['swamp']==2 and m.TERRAIN_COSTS['plain']==1
    wx,wy=find_near(secret,bx,by,'water',False); rx,ry=find_near(secret,bx,by,'river',False)
    c=polydb(); c.execute('insert into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,?)',(s['id'],wx-1,wy,1)); c.execute('insert into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,?)',(s['id'],rx-1,ry,1)); c.commit(); c.close()
    with pytest.raises(ValueError, match='water_not_capturable'): m.capture_cell(11,wx,wy,'w')
    with pytest.raises(ValueError, match='river_not_capturable'): m.capture_cell(11,rx,ry,'r')

def test_single_cell_concurrent_capture_and_client_user_id_ignored(polydb):
    state=join(21,1); join(22,1); bx,by=m.faction_base_positions()[1]; x,y=bx+8,by; results=[]; errs=[]
    def worker(uid,key):
        try: results.append(m.capture_cell(uid,x,y,key)['ok'])
        except ValueError as e: errs.append(str(e))
    ts=[threading.Thread(target=worker,args=(21,'k1')),threading.Thread(target=worker,args=(22,'k2'))]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results.count(True)==1 and errs
    c=polydb(); assert c.execute('select count(*) from polywar_cells where x=? and y=?',(x,y)).fetchone()[0]==1; c.close()
    block=Path('web.py').read_text()[Path('web.py').read_text().index('async def handle_polywar_action_api'):Path('web.py').read_text().index('async def handle_webapp_summary')]
    assert 'data.get("user_id")' not in block and "data.get('user_id')" not in block

def test_canvas_page_and_routes_present():
    assert '<canvas id="polywarCanvas"' in Path('webapp/polywar.js').read_text()
    w=Path('web.py').read_text(); assert '/api/polywar/map/chunks' in w and '/api/polywar/action' in w and 'app.router.add_get("/polywar", handle_polywar_page)' in w

def test_two_parallel_captures_same_player_spend_energy_once(polydb):
    state = join(31, 1); sid = state['season']['id']; bx, by = m.faction_base_positions()[1]
    c = polydb(); c.execute('update polywar_players set current_energy=1 where user_id=31'); c.commit(); c.close()
    cells = [(bx + 8, by), (bx, by + 8)]
    results, errs = [], []
    def worker(cell, key):
        try: results.append(m.capture_cell(31, cell[0], cell[1], key))
        except ValueError as e: errs.append(str(e))
    ts = [threading.Thread(target=worker, args=(cells[0], 'one')), threading.Thread(target=worker, args=(cells[1], 'two'))]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(results) == 1
    assert 'insufficient_energy' in errs or 'cell_conflict' in errs
    c = polydb()
    assert c.execute('select current_energy from polywar_players where user_id=31 and season_id=?', (sid,)).fetchone()[0] == 0
    assert c.execute('select count(*) from polywar_actions where user_id=31 and season_id=?', (sid,)).fetchone()[0] == 1
    c.close()

def test_relative_bases_for_supported_map_sizes(polydb, monkeypatch):
    for size in (10000, 2000, 512):
        monkeypatch.setattr(polywar, 'get_setting', lambda k, d='', size=size: str(size) if k in {'polywar_map_width','polywar_map_height'} else d)
        bases = m.get_starting_bases()
        assert len(bases) == 7
        seen = []
        for b in bases:
            assert 0 <= b['x'] < size and 0 <= b['y'] < size
            for o in seen:
                assert abs(b['x'] - o['x']) > b['size'] or abs(b['y'] - o['y']) > b['size']
            seen.append(b)
