"""Focused invariants for connected, faction-owned squad frontlines."""
from types import SimpleNamespace

import pytest

from services import polywar_squad_service as squads


@pytest.fixture
def world(monkeypatch):
    owners={(5,5):1,(6,5):1,(8,5):2}
    config=SimpleNamespace(width=20,height=20)
    monkeypatch.setattr(squads.m,'in_bounds_with_config',lambda x,y,c: 0<=x<c.width and 0<=y<c.height)
    monkeypatch.setattr(squads,'_passable',lambda seed,x,y,c: 0<=x<c.width and 0<=y<c.height)
    monkeypatch.setattr(squads.m,'owner_at_with_config',lambda conn,sid,x,y,c: owners.get((x,y)))
    monkeypatch.setattr(squads,'_playable_enemy',lambda conn,fid: fid==2)
    monkeypatch.setattr(squads,'_capital_at',lambda *a: None)
    from services import polywar_world_service as world_service
    monkeypatch.setattr(world_service,'is_rift',lambda *a,**k: False)
    monkeypatch.setattr(world_service,'is_safe_zone',lambda *a,**k: False)
    return owners,config


def squad(x=5,y=5):
    return {'id':1,'season_id':1,'faction_id':1,'x':x,'y':y,'supply_x':5,'supply_y':5,
            'target_x':19,'target_y':5,'previous_x':x,'previous_y':y,'move_index':0}


def test_orthogonal_steps_only():
    assert all(squads._orthogonal_step(5,5,*p) for p in ((6,5),(4,5),(5,6),(5,4)))
    assert not squads._orthogonal_step(5,5,6,6)
    assert not squads._orthogonal_step(5,5,7,5)


def test_owned_and_connected_frontier_movement(world):
    owners,config=world
    assert squads._is_legal_squad_step(None,squad(),6,5,'seed',config,{})
    assert squads._is_legal_frontier_cell(None,1,1,5,6,'seed',config,{})
    owners[(8,5)]=2; owners[(7,5)]=1
    assert squads._is_legal_frontier_cell(None,1,1,8,5,'seed',config,{})


def test_disconnected_neutral_and_enemy_are_illegal(world):
    owners,config=world
    assert not squads._is_legal_frontier_cell(None,1,1,10,10,'seed',config,{})
    owners[(10,10)]=2
    assert not squads._is_legal_frontier_cell(None,1,1,10,10,'seed',config,{})


def test_no_neutral_leapfrog_then_expansion_after_capture(world):
    owners,config=world
    assert squads._is_legal_squad_step(None,squad(),5,6,'seed',config,{})
    assert not squads._is_legal_squad_step(None,squad(5,6),5,7,'seed',config,{})
    owners[(5,6)]=1
    assert squads._is_legal_squad_step(None,squad(5,6),5,7,'seed',config,{})


def test_distant_commander_target_cannot_bypass_frontier(world,monkeypatch):
    owners,config=world
    monkeypatch.setattr(squads,'_fetchone',lambda *a,**k: None)
    monkeypatch.setattr(squads,'_capital_at',lambda *a,**k: None)
    monkeypatch.setattr(squads.m,'terrain_at_with_config',lambda *a: 'plain')
    result=squads._choose_step(SimpleNamespace(cursor=lambda:None),squad(),{'supply_distance':80},'seed',config)
    assert result[0] in {'move','attack_cell','wait'}
    if result[0]=='move': assert squads._orthogonal_step(5,5,*result[1])


def test_retreating_squad_never_enters_non_owned_cell(world):
    owners,config=world
    retreat=squad(6,5); retreat['status']='retreating'; retreat['target_x']=5
    for nx,ny in ((7,5),(6,6),(6,4)):
        assert not squads._is_legal_squad_step(None,retreat,nx,ny,'seed',config,{})


def test_bounds_and_impassable_are_rejected(world,monkeypatch):
    _,config=world
    assert not squads._is_legal_squad_step(None,squad(0,0),-1,0,'seed',config,{})
    monkeypatch.setattr(squads,'_passable',lambda seed,x,y,c: False)
    assert not squads._is_legal_squad_step(None,squad(),6,5,'seed',config,{})


def test_system_and_non_playable_destinations_are_rejected(world):
    owners,config=world
    owners[(5,6)]=9
    assert not squads._is_legal_frontier_cell(None,1,1,5,6,'seed',config,{})
    assert not squads._is_legal_squad_step(None,squad(),5,6,'seed',config,{})


def test_choose_step_actions_match_owner(world,monkeypatch):
    owners,config=world
    monkeypatch.setattr(squads,'_fetchone',lambda *a,**k: None)
    monkeypatch.setattr(squads,'_capital_at',lambda *a,**k: None)
    monkeypatch.setattr(squads.m,'terrain_at_with_config',lambda *a:'plain')
    # The order points at a neutral connected frontier.
    sq=squad(); sq['target_x']=5; sq['target_y']=6
    assert squads._choose_step(SimpleNamespace(cursor=lambda:None),sq,{'supply_distance':80},'seed',config)[0]=='attack_cell'
    owners[(5,6)]=2
    assert squads._choose_step(SimpleNamespace(cursor=lambda:None),sq,{'supply_distance':80},'seed',config)[0]=='attack_cell'
    owners[(5,6)]=9
    kind,value=squads._choose_step(SimpleNamespace(cursor=lambda:None),sq,{'supply_distance':80},'seed',config)
    assert kind!='move' or owners.get(value)!=9


def test_spawn_requires_owned_hq_and_uses_owned_fallback(world):
    owners,config=world; config.bases={1:(5,5),2:(8,5)}
    assert squads._spawn_cell(None,1,1,'seed',config)==(5,5)
    owners[(5,5)]=2
    assert squads._spawn_cell(None,1,1,'seed',config)==(6,5)
    owners.pop((6,5)); owners.pop((5,5))
    assert squads._spawn_cell(None,1,1,'seed',config) is None


def test_stranded_recovery_retreat_block_and_expiry(world,monkeypatch):
    owners,config=world; statements=[]; events=[]
    monkeypatch.setattr(squads,'_execute',lambda c,sql,params=(): statements.append((sql,params)))
    monkeypatch.setattr(squads,'_insert_squad_event',lambda *args: events.append(args))
    conn=SimpleNamespace(cursor=lambda:object())
    stranded=squad(5,6); stranded.update(status='marching',blocked_ticks=0)
    assert squads._recover_stranded_squad(conn,stranded,None,None,'seed',config)
    assert "status='retreating'" in statements[-1][0] and 'x=%s,y=%s' in statements[-1][0]
    owners.pop((5,5)); owners.pop((6,5)); statements.clear()
    assert squads._recover_stranded_squad(conn,stranded,None,None,'seed',config)
    assert "status='retreating'" in statements[-1][0]
    stranded['blocked_ticks']=2
    assert squads._recover_stranded_squad(conn,stranded,None,None,'seed',config)
    assert "status='expired'" in statements[-1][0]
    assert events and events[-1][3]=='squad_stranded_expired' and events[-1][-1]==1


@pytest.mark.parametrize('special', ['rift','safe'])
def test_special_frontier_is_rejected_before_selection_with_alternative(world,monkeypatch,special):
    owners,config=world; forbidden=(6,5); alternative=(5,6)
    owners.pop(forbidden)  # make the commander-facing cell a neutral frontier
    owners[(4,5)]=owners[(5,4)]=9
    from services import polywar_world_service as world_service
    monkeypatch.setattr(world_service,'is_rift',lambda conn,sid,x,y: special=='rift' and (x,y)==forbidden)
    monkeypatch.setattr(world_service,'is_safe_zone',lambda conn,sid,x,y,config=None: special=='safe' and (x,y)==forbidden)
    monkeypatch.setattr(squads,'_fetchone',lambda *a,**k: None)
    monkeypatch.setattr(squads.m,'terrain_at_with_config',lambda *a:'plain')
    sq=squad(); sq.update(target_x=7,target_y=5)
    assert not squads._is_legal_normal_attack_target(None,1,1,*forbidden,'seed',config,{})
    kind,target=squads._choose_step(SimpleNamespace(cursor=lambda:None),sq,{'supply_distance':80},'seed',config)
    assert (kind,target)==('attack_cell',alternative)


def test_all_forbidden_neighbours_wait(world,monkeypatch):
    owners,config=world; owners.clear(); owners[(5,5)]=1; forbidden={(6,5),(4,5),(5,6),(5,4)}
    from services import polywar_world_service as world_service
    monkeypatch.setattr(world_service,'is_rift',lambda conn,sid,x,y:(x,y) in forbidden)
    monkeypatch.setattr(squads,'_fetchone',lambda *a,**k: None)
    monkeypatch.setattr(squads.m,'terrain_at_with_config',lambda *a:'plain')
    assert squads._choose_step(SimpleNamespace(cursor=lambda:None),squad(),{'supply_distance':80},'seed',config)==('wait',None)


def test_capital_uses_only_capital_validator(world,monkeypatch):
    owners,config=world; capital=(5,6)
    monkeypatch.setattr(squads,'_capital_at',lambda conn,sid,x,y: {'controller_faction_id':2,'status':'active'} if (x,y)==capital else None)
    monkeypatch.setattr(squads,'_fetchone',lambda *a,**k: None)
    monkeypatch.setattr(squads.m,'terrain_at_with_config',lambda *a:'plain')
    sq=squad(); sq.update(target_x=5,target_y=6)
    assert squads._choose_step(SimpleNamespace(cursor=lambda:None),sq,{'supply_distance':80},'seed',config)==('capital',capital)
    monkeypatch.setattr(squads,'_playable_enemy',lambda conn,fid: False)
    assert squads._choose_step(SimpleNamespace(cursor=lambda:None),sq,{'supply_distance':80},'seed',config)[0]!='attack_cell'
