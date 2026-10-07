"""Bounded browser storage maintenance; authentication and user artifacts are retained."""
import os
from pathlib import Path
import re
import shutil
import time

# Chromium can reconstruct these. Cookies, Sessions, History, IndexedDB,
# Local Storage, Service Worker data, downloads and Agent storages are excluded.
CACHE_PATHS = (
    'chromium/Default/Cache', 'chromium/Default/Code Cache',
    'chromium/Default/GPUCache', 'chromium/Default/DawnCache',
    'chromium/ShaderCache', 'chromium/GrShaderCache',
    'chromium/GraphiteDawnCache', 'chromium/Crashpad/reports',
    'chromium/Crash Reports', 'chromium/BrowserMetrics',
    'chromium/BrowserMetrics-spare.pma', 'dsh-home/cache',
)
TEMP_NAME = re.compile(r'\.(?:agent-session-id|takeover-state|session-cookies\.json)\.[0-9a-f]+\.tmp')


def tree_bytes(root):
    """Do not traverse symlinks, including links to another user's data."""
    total = 0
    if root.is_symlink():
        return 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in files:
            path = Path(directory) / name
            try:
                if not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                pass
    return total


def profiles(base):
    if base.is_symlink():
        return []
    try:
        return [p for p in base.iterdir() if re.fullmatch(r'[0-9a-f]{32}', p.name)
                and not p.is_symlink() and p.is_dir()]
    except FileNotFoundError:
        return []


def _safe_target(root, relative):
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            return None
    return current


def maintain(base, active=()):
    """Only clean closed Chromium/Agent environments. Never evict user profiles."""
    base = Path(base)
    before = shutil.disk_usage(base)
    report = {'free_before_bytes': before.free, 'cache_bytes_removed': 0,
              'cleaned_profiles': 0, 'profiles': 0, 'errors': 0}
    for root in profiles(base):
        report['profiles'] += 1
        if root.name in active:
            continue
        changed = False
        for relative in CACHE_PATHS:
            target = _safe_target(root, relative)
            if target is None or not target.exists():
                continue
            size = tree_bytes(target) if target.is_dir() else target.stat().st_size
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                report['cache_bytes_removed'] += size
                changed = True
            except OSError:
                report['errors'] += 1
        for target in root.iterdir():
            if TEMP_NAME.fullmatch(target.name) and not target.is_symlink():
                try:
                    if target.is_file() and target.stat().st_mtime < time.time() - 3600:
                        target.unlink()
                except OSError:
                    report['errors'] += 1
        report['cleaned_profiles'] += int(changed)
    if shutil.disk_usage(base).free < minimum_free():
        report['breakdown'] = breakdown(base)
    report['retained_bytes'] = sum(tree_bytes(root) for root in profiles(base))
    after = shutil.disk_usage(base)
    report.update(free_bytes=after.free, total_bytes=after.total,
                  used_bytes=after.used, low_space=after.free < minimum_free())
    return report


def minimum_free():
    try:
        value = int(os.getenv('VELIA_AGENT_CORE_MIN_FREE_MB', '128'))
    except ValueError:
        value = 128
    return max(64, min(4096, value)) * 1024 * 1024


def ensure_capacity(base, root=None):
    if shutil.disk_usage(base).free < minimum_free():
        raise RuntimeError('browser_storage_capacity')
    if root and root.exists():
        # Stored artifacts/agent histories are preserved. Stop further growth
        # instead of deleting them silently when one profile exceeds its budget.
        try:
            limit = int(os.getenv('VELIA_AGENT_CORE_PROFILE_MAX_MB', '256'))
        except ValueError:
            limit = 256
        if tree_bytes(root) > max(64, min(4096, limit)) * 1024 * 1024:
            raise RuntimeError('browser_profile_capacity')


def breakdown(base):
    """Aggregate service-directory sizes, never user IDs or file contents."""
    from collections import defaultdict
    totals = defaultdict(int)
    for root in profiles(base):
        for category in ('chromium', 'chromium/Default', 'dsh-home'):
            parent = _safe_target(root, category)
            if parent is None or not parent.is_dir():
                continue
            for child in parent.iterdir():
                if child.is_symlink():
                    continue
                totals[category + '/' + child.name] += tree_bytes(child) if child.is_dir() else child.stat().st_size
    return dict(sorted(totals.items(), key=lambda item: -item[1])[:20])
