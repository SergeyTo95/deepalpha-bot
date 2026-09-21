"""Pinned official RADAR source archive, independent of GitHub availability."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import urllib.request
import zipfile

SOURCE_URL = 'https://zenodo.org/api/records/21504519/files/damo-radar.zip/content'
SOURCE_SHA256 = '777dbdccb1b925ef84e08578749fe6ff12cead60cd2f7245447e7ee22d1a854a'
SOURCE_DOI = '10.5281/zenodo.21504519'


def unpack_source(data: bytes, target: Path) -> None:
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise RuntimeError('RADAR source archive checksum mismatch')
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(info.file_size for info in archive.infolist()) > 50_000_000:
            raise RuntimeError('RADAR source archive too large')
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or stat.S_ISLNK(info.external_attr >> 16):
                raise RuntimeError('Unsafe RADAR source archive path')
            if not path.parts or path.parts[0] != 'damo-radar' or info.is_dir():
                continue
            if path.name == '.DS_Store':
                continue
            destination = target.joinpath(*path.parts[1:])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info))
    for name in ('LICENSE', 'requirements.txt', 'RADAR_inference/inference_demo.py'):
        if not (target / name).is_file():
            raise RuntimeError(f'Incomplete RADAR source archive: {name}')
    (target / 'upstream-source.json').write_text(json.dumps({
        'doi': SOURCE_DOI, 'url': SOURCE_URL, 'sha256': SOURCE_SHA256,
        'git_commit': None,
    }, sort_keys=True))


if __name__ == '__main__':
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
        content = response.read(8_000_001)
    if len(content) > 8_000_000:
        raise SystemExit('RADAR archive download too large')
    unpack_source(content, Path(sys.argv[1]))
    print(f'RADAR_SOURCE_VERIFIED doi={SOURCE_DOI} sha256={SOURCE_SHA256}')
