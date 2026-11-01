from pathlib import Path
from collections import namedtuple
import os
import time

import pytest
from agent_core.browser import storage

Usage = namedtuple('Usage', 'total used free')


def write(root, relative, content=b'keep'):
    p=root / relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content);return p


def test_cleanup_removes_only_regenerable_closed_profile_caches(tmp_path):
    root=tmp_path / ('a'*32)
    for relative in storage.CACHE_PATHS:
        write(root, relative+'/data', b'cache')
    protected=['chromium/Default/Network/Cookies','chromium/Default/Sessions/Session_1',
               'chromium/Default/IndexedDB/site/data','chromium/Default/Local Storage/data',
               'chromium/Default/Service Worker/CacheStorage/data',
               'dsh-home/storages/agent.yml','workspace/report.pdf','session-cookies.json','agent-session-id',
               'chromium/WasmTtsEngine/model.bin','chromium/OnDeviceHeadSuggestModel/model.bin',
               'chromium/CertificateRevocation/list.bin']
    for p in protected:write(root,p)
    result=storage.maintain(tmp_path)
    assert result['cache_bytes_removed']==len(storage.CACHE_PATHS)*5
    assert result['cleaned_profiles']==1
    for p in protected:assert (root/p).read_bytes()==b'keep'
    assert all(not(root/p).exists() for p in storage.CACHE_PATHS)


def test_active_profiles_and_other_directories_are_untouched(tmp_path):
    active=tmp_path/('a'*32);unknown=tmp_path/'not-a-profile'
    write(active,'chromium/Default/Cache/data');write(unknown,'chromium/Default/Cache/data')
    storage.maintain(tmp_path,{active.name})
    assert (active/'chromium/Default/Cache/data').exists()
    assert (unknown/'chromium/Default/Cache/data').exists()


def test_symlinks_cannot_purge_other_profiles_or_external_files(tmp_path):
    outside=tmp_path/'outside';write(outside,'Cache/data')
    root=tmp_path/('a'*32);(root/'chromium').mkdir(parents=True)
    (root/'chromium/Default').symlink_to(outside,target_is_directory=True)
    (tmp_path/('b'*32)).symlink_to(outside,target_is_directory=True)
    storage.maintain(tmp_path)
    assert (outside/'Cache/data').read_bytes()==b'keep'
    assert storage.tree_bytes(tmp_path/('b'*32))==0


def test_only_old_abandoned_atomic_temporary_files_are_removed(tmp_path):
    root=tmp_path/('a'*32)
    old=write(root,'.agent-session-id.aabb.tmp');os.utime(old,(time.time()-7200,)*2)
    recent=write(root,'.session-cookies.json.aabb.tmp')
    other=write(root,'important.tmp');os.utime(other,(time.time()-7200,)*2)
    storage.maintain(tmp_path)
    assert not old.exists();assert recent.exists();assert other.exists()


def test_reserve_and_profile_budget_reject_growth_without_deleting(monkeypatch,tmp_path):
    monkeypatch.setattr(storage.shutil,'disk_usage',lambda p:Usage(1024**3,1024**3-10,10))
    with pytest.raises(RuntimeError,match='browser_storage_capacity'):storage.ensure_capacity(tmp_path)
    root=tmp_path/('a'*32);p=write(root,'workspace/important.txt')
    monkeypatch.setattr(storage.shutil,'disk_usage',lambda p:Usage(1024**3,0,1024**3))
    monkeypatch.setattr(storage,'tree_bytes',lambda p:300*1024**2)
    with pytest.raises(RuntimeError,match='browser_profile_capacity'):storage.ensure_capacity(tmp_path,root)
    assert p.read_bytes()==b'keep'


@pytest.mark.asyncio
async def test_session_creation_holds_maintenance_lock_until_registered(monkeypatch,tmp_path):
    from agent_core.browser import health_server as browser
    browser.sessions.clear();monkeypatch.setattr(browser,'SESSION_BASE',tmp_path)
    async def create(user,conversation):
        assert browser.storage_lock.locked()
        from types import SimpleNamespace
        return browser.BrowserSession(user_key=browser._session_key(user,conversation),root=tmp_path/browser._session_key(user,conversation),endpoint='http://127.0.0.1:1',browser=SimpleNamespace(returncode=None))
    monkeypatch.setattr(browser,'_new_session',create)
    session,_=await browser._get_session('7','test-chat')
    assert browser.sessions[session.user_key] is session
    browser.sessions.clear()
