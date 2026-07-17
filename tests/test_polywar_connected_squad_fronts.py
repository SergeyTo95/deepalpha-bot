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
