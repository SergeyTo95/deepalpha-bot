"""Bounded, immutable encrypted files owned by the authenticated notebook user."""
import asyncio
import base64
import hashlib
import json
import re
import threading
from aiohttp import web
from db.database import get_connection

MAX_FILE = 5 * 1024 * 1024 + 16
MAX_TOTAL = 50 * 1024 * 1024
MAX_BODY = 7 * 1024 * 1024 + 4096
_READY = False
_LOCK = threading.Lock()
ID = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}\Z')


class MaterialError(Exception):
    def __init__(self, code, status):
        self.code, self.status = code, status


def ensure_schema():
    global _READY
    if _READY:
        return
    with _LOCK:
        if _READY:
            return
        connection = get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute('CREATE TABLE IF NOT EXISTS velia_notebook_material_owners (user_id BIGINT PRIMARY KEY)')
            cursor.execute('CREATE TABLE IF NOT EXISTS velia_notebook_materials (user_id BIGINT NOT NULL, material_id TEXT NOT NULL, byte_size BIGINT NOT NULL, digest TEXT NOT NULL, record_json TEXT NOT NULL, PRIMARY KEY (user_id, material_id))')
            connection.commit()
            _READY = True
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()


def validate_record(record):
    if not isinstance(record, dict) or set(record) != {'iv', 'data', 'version'} or type(record['version']) is not int or record['version'] != 1:
        raise ValueError()
    if not isinstance(record['iv'], str) or len(record['iv']) != 16 or not isinstance(record['data'], str) or len(record['data']) > 4 * ((MAX_FILE + 2) // 3):
        raise ValueError()
    nonce = base64.b64decode(record['iv'], validate=True)
    cipher = base64.b64decode(record['data'], validate=True)
    if len(nonce) != 12 or not 17 <= len(cipher) <= MAX_FILE:
        raise ValueError()
    return len(cipher), hashlib.sha256(nonce + cipher).hexdigest()


def material_operation(user_id, material_id, method, record=None):
    if not ID.fullmatch(material_id):
        raise ValueError()
    size, digest = validate_record(record) if method == 'PUT' else (0, '')
    ensure_schema()
    connection = get_connection()
    cursor = connection.cursor()
    try:
        if method in ('PUT', 'DELETE'):
            # Serialize quota checks and mutations across processes/replicas.
            cursor.execute('INSERT INTO velia_notebook_material_owners (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING', (int(user_id),))
            cursor.execute('SELECT user_id FROM velia_notebook_material_owners WHERE user_id=%s FOR UPDATE', (int(user_id),))
            cursor.fetchone()
        cursor.execute('SELECT digest, record_json FROM velia_notebook_materials WHERE user_id=%s AND material_id=%s', (int(user_id), material_id))
        existing = cursor.fetchone()
        if method == 'GET':
            if not existing:
                raise MaterialError('notebook_material_not_found', 404)
            return {'record': json.loads(existing[1])}
        if method == 'PUT':
            if existing:
                if existing[0] != digest:
                    raise MaterialError('notebook_material_immutable', 409)
            else:
                cursor.execute('SELECT COUNT(*), COALESCE(SUM(byte_size),0) FROM velia_notebook_materials WHERE user_id=%s', (int(user_id),))
                count, used = cursor.fetchone()
                if count >= 100 or used + size > MAX_TOTAL:
                    raise MaterialError('notebook_material_quota', 413)
                cursor.execute('INSERT INTO velia_notebook_materials (user_id,material_id,byte_size,digest,record_json) VALUES (%s,%s,%s,%s,%s)', (int(user_id), material_id, size, digest, json.dumps(record, separators=(',', ':'))))
        elif method == 'DELETE':
            cursor.execute('DELETE FROM velia_notebook_materials WHERE user_id=%s AND material_id=%s', (int(user_id), material_id))
        else:
            raise ValueError()
        connection.commit()
        return {'saved': method == 'PUT', 'deleted': method == 'DELETE'}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


async def read_body(request):
    if request.content_length and request.content_length > MAX_BODY:
        raise MaterialError('notebook_material_too_large', 413)
    body = bytearray()
    async for chunk in request.content.iter_chunked(16384):
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise MaterialError('notebook_material_too_large', 413)
    return json.loads(body)


def setup_material_routes(app, module):
    slots = asyncio.Semaphore(4)
    async def handle(request):
        def reply(data, status=200):
            response = module._json_response(data, status=status)
            response.headers['Cache-Control'] = 'no-store'
            return response
        if not module._mobile_api_available():
            return module._disabled_response()
        auth = module._require_mobile_auth(request)
        if not auth:
            return reply({'ok': False, 'error': 'unauthorized'}, 401)
        if slots.locked():
            return reply({'ok': False, 'error': 'notebook_busy'}, 429)
        try:
            async with slots:
                body = None
                if request.method != 'GET':
                    body = await read_body(request)
                    if not isinstance(body, dict) or str(body.get('owner')) != str(auth['user_id']):
                        return reply({'ok': False, 'error': 'notebook_owner_changed'}, 403)
                result = await asyncio.to_thread(material_operation, auth['user_id'], request.match_info['material_id'], request.method, body.get('record') if body else None)
                return reply({'ok': True, 'owner': str(auth['user_id']), **result})
        except MaterialError as error:
            return reply({'ok': False, 'error': error.code}, error.status)
        except (ValueError, TypeError, UnicodeDecodeError):
            return reply({'ok': False, 'error': 'invalid_notebook_material'}, 400)
        except Exception:
            return reply({'ok': False, 'error': 'notebook_unavailable'}, 503)
    path = '/mobile-api/v1/notebook/materials/{material_id}'
    for method in ('GET', 'PUT', 'DELETE'):
        app.router.add_route(method, path, handle)
