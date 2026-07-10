import hashlib, math, time
from datetime import datetime
from typing import Any, Dict, List, Tuple

from services import polywar_service as polywar

TERRAIN_COSTS={"plain":1,"forest":1,"mountain":2,"swamp":2,"desert":1,"road":1,"ruins":1,"water":None,"river":None}
FACTION_BASES={1:(900,900),2:(9100,900),3:(900,9100),4:(9100,9100),5:(5000,1200),6:(1500,5000),7:(8500,5000)}
COLORS={fid: color for fid,_,_,color,_ in polywar.FACTIONS}


def _setting_int(key, default, lo, hi): return polywar._setting_int(key, default, lo, hi)
def map_width(): return _setting_int('polywar_map_width',10000,512,100000)
def map_height(): return _setting_int('polywar_map_height',10000,512,100000)
def chunk_size(): return _setting_int('polywar_chunk_size',64,16,128)
def max_chunks_per_request(): return _setting_int('polywar_max_chunks_per_request',9,1,25)
def starting_area_size(): return _setting_int('polywar_starting_area_size',15,3,65)

def init_polywar_map_schema(conn=None):
    own=conn is None; conn=conn or polywar.get_connection(); c=conn.cursor(); id_sql="INTEGER PRIMARY KEY AUTOINCREMENT" if polywar._is_sqlite(conn) else "SERIAL PRIMARY KEY"
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS polywar_cells (season_id INTEGER NOT NULL,x INTEGER NOT NULL,y INTEGER NOT NULL,owner_faction_id INTEGER NOT NULL,capture_progress INTEGER NOT NULL DEFAULT 100,updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_by_user_id BIGINT NULL,UNIQUE(season_id,x,y))""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS polywar_actions (id {id_sql},season_id INTEGER NOT NULL,user_id BIGINT NOT NULL,faction_id INTEGER NOT NULL,action_type TEXT NOT NULL,x INTEGER NOT NULL,y INTEGER NOT NULL,energy_cost INTEGER NOT NULL,idempotency_key TEXT NOT NULL,created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(user_id,idempotency_key))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_polywar_cells_range ON polywar_cells(season_id,x,y)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_polywar_cells_owner ON polywar_cells(season_id,owner_faction_id,x,y)")
        conn.commit()
    finally:
        if own: conn.close()

def _hash(seed,*parts):
    h=hashlib.sha256((str(seed)+':' + ':'.join(map(str,parts))).encode()).digest(); return int.from_bytes(h[:8],'big')/2**64

def _smooth(seed,x,y,scale):
    gx=x/scale; gy=y/scale; x0=math.floor(gx); y0=math.floor(gy); tx=gx-x0; ty=gy-y0
    def fade(t): return t*t*(3-2*t)
    def v(ix,iy): return _hash(seed,ix,iy,scale)
    a=v(x0,y0)*(1-fade(tx))+v(x0+1,y0)*fade(tx); b=v(x0,y0+1)*(1-fade(tx))+v(x0+1,y0+1)*fade(tx)
    return a*(1-fade(ty))+b*fade(ty)

def terrain_at(seed,x:int,y:int)->str:
    if not in_bounds(x,y): raise ValueError('out_of_bounds')
    # roads radiate near faction bases and along long corridors
    for bx,by in FACTION_BASES.values():
        if abs(x-bx)<=1 and abs(y-by)<90 or abs(y-by)<=1 and abs(x-bx)<90: return 'road'
    r1=abs((x*37+y*19+int(_hash(seed,'river')*9973))%911-455)
    r2=abs((x*13-y*29+int(_hash(seed,'river2')*9973))%1327-663)
    if r1 < 3 or r2 < 2: return 'river'
    water=_smooth(seed,x,y,900)*.65+_smooth(seed,x,y,260)*.35
    if water < .23: return 'water'
    hills=_smooth(seed+'m',x,y,520)*.7+_smooth(seed+'m',x,y,130)*.3
    woods=_smooth(seed+'f',x,y,380)
    dry=_smooth(seed+'d',x,y,700)
    rare=_hash(seed,'rare',x//5,y//5)
    if hills>.78: return 'mountain'
    if water<.30 and woods>.55: return 'swamp'
    if dry>.78: return 'desert'
    if woods>.62: return 'forest'
    if rare>.995: return 'ruins'
    return 'plain'

def in_bounds(x,y): return 0<=int(x)<map_width() and 0<=int(y)<map_height()
def get_starting_bases(): return [{'faction_id':fid,'x':x,'y':y,'size':starting_area_size(),'color':COLORS.get(fid)} for fid,(x,y) in FACTION_BASES.items()]
def _start_owner(x,y):
    s=starting_area_size(); half=s//2
    for fid,(bx,by) in FACTION_BASES.items():
        if abs(x-bx)<=half and abs(y-by)<=half: return fid
    return None

def _private_active_season(conn):
    s=polywar.ensure_active_season(conn); c=conn.cursor(); row=polywar._fetchone(c,"SELECT secret_seed FROM polywar_seasons WHERE id = %s",(int(s['id']),)); s['secret_seed']=row['secret_seed']; return s

def _owner_at(conn,season_id,x,y):
    so=_start_owner(x,y)
    if so: return so
    row=polywar._fetchone(conn.cursor(),"SELECT owner_faction_id FROM polywar_cells WHERE season_id=%s AND x=%s AND y=%s",(season_id,x,y))
    return int(row['owner_faction_id']) if row else None

def build_chunks(user_id:int, chunks:List[Tuple[int,int]]):
    if len(chunks)>max_chunks_per_request(): raise ValueError('too_many_chunks')
    cs=chunk_size(); conn=polywar.get_connection()
    try:
        polywar.init_polywar_schema(conn); init_polywar_map_schema(conn); season=_private_active_season(conn); sid=int(season['id']); seed=season['secret_seed']
        out=[]
        for cx,cy in chunks:
            if cx<0 or cy<0 or cx*cs>=map_width() or cy*cs>=map_height(): raise ValueError('out_of_bounds')
            x0,y0=cx*cs,cy*cs; w=min(cs,map_width()-x0); h=min(cs,map_height()-y0)
            rows=polywar._fetchall(conn.cursor(),"SELECT x,y,owner_faction_id FROM polywar_cells WHERE season_id=%s AND x >= %s AND x < %s AND y >= %s AND y < %s",(sid,x0,x0+w,y0,y0+h))
            sparse={(r['x'],r['y']):r['owner_faction_id'] for r in rows}; terr=[]; owners=[]
            for yy in range(y0,y0+h):
                terr.append([terrain_at(seed,xx,yy) for xx in range(x0,x0+w)])
                owners.append([sparse.get((xx,yy)) or _start_owner(xx,yy) for xx in range(x0,x0+w)])
            bases=[b for b in get_starting_bases() if x0<=b['x']<x0+w and y0<=b['y']<y0+h]
            out.append({'chunk_x':cx,'chunk_y':cy,'chunk_size':cs,'width':w,'height':h,'terrain':terr,'owners':owners,'bases':bases})
        return {'ok':True,'season_id':sid,'chunks':out,'chunk_size':cs,'map_width':map_width(),'map_height':map_height(),'server_timestamp':int(time.time())}
    finally: conn.close()

def capture_cell(user_id:int,x:int,y:int,idempotency_key:str):
    if not idempotency_key or len(str(idempotency_key))>120: raise ValueError('bad_idempotency_key')
    conn=polywar.get_connection(); c=conn.cursor()
    try:
        polywar.init_polywar_schema(conn); init_polywar_map_schema(conn); season=_private_active_season(conn); sid=int(season['id']); seed=season['secret_seed']
        player=polywar.get_or_create_player(user_id,sid,conn); fid=player.get('faction_id')
        if not fid: raise ValueError('faction_required')
        if not in_bounds(x,y): raise ValueError('out_of_bounds')
        e=polywar._energy(player)
        if e.get('is_locked'): raise ValueError('player_locked')
        terr=terrain_at(seed,x,y); cost=TERRAIN_COSTS[terr]
        if cost is None: raise ValueError(f'{terr}_not_capturable')
        existing=polywar._fetchone(c,"SELECT * FROM polywar_actions WHERE user_id=%s AND idempotency_key=%s",(user_id,idempotency_key))
        if existing:
            return {'ok':True,'duplicate':True,'cell':{'x':existing['x'],'y':existing['y'],'terrain':terrain_at(seed,existing['x'],existing['y']),'owner_faction_id':existing['faction_id']},'energy':{k:v for k,v in e.items() if k!='energy_updated_at'}}
        owner=_owner_at(conn,sid,x,y)
        if owner==fid: raise ValueError('already_owned')
        if owner is not None: raise ValueError('enemy_capture_unavailable')
        if not any(_owner_at(conn,sid,nx,ny)==fid for nx,ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)) if in_bounds(nx,ny)): raise ValueError('not_adjacent')
        if int(e['current_energy'])<cost: raise ValueError('insufficient_energy')
        now=datetime.utcnow(); new_energy=int(e['current_energy'])-cost
        try:
            polywar._execute(c,"INSERT INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at,updated_by_user_id) VALUES (%s,%s,%s,%s,100,%s,%s)",(sid,x,y,fid,now,user_id))
            polywar._execute(c,"INSERT INTO polywar_actions (season_id,user_id,faction_id,action_type,x,y,energy_cost,idempotency_key,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",(sid,user_id,fid,'capture',x,y,cost,idempotency_key,now))
            polywar._execute(c,"UPDATE polywar_players SET current_energy=%s, energy_updated_at=%s, last_active_at=%s WHERE user_id=%s AND season_id=%s",(new_energy,e['energy_updated_at'],now,user_id,sid))
            polywar._execute(c,"UPDATE polywar_faction_season_stats SET controlled_cells_count=controlled_cells_count+1, updated_at=%s WHERE season_id=%s AND faction_id=%s",(now,sid,fid))
            conn.commit()
        except Exception:
            polywar._safe_rollback(conn); raise ValueError('cell_conflict')
        player['current_energy']=new_energy; en=polywar._energy(player)
        return {'ok':True,'cell':{'x':x,'y':y,'terrain':terr,'owner_faction_id':fid,'energy_cost':cost},'energy':{k:v for k,v in en.items() if k!='energy_updated_at'}}
    finally: conn.close()
