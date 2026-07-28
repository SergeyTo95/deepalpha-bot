import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _operations(document):
    result = set()
    for path, item in document.get("paths", {}).items():
        for method in item:
            if method.lower() in HTTP_METHODS:
                result.add((method.upper(), path))
    return result


def _postman_operations(items):
    result = set()
    for item in items or []:
        if isinstance(item.get("item"), list):
            result.update(_postman_operations(item["item"]))
            continue
        request = item.get("request") or {}
        method = str(request.get("method") or "").upper()
        raw_url = request.get("url")
        if isinstance(raw_url, dict):
            raw_url = raw_url.get("raw")
        path = str(raw_url or "").replace("{{base_url}}", "")
        path = path.split("?", 1)[0]
        path = path.replace("{{analysis_job_id}}", "{job_id}")
        path = path.replace("{{opportunity_job_id}}", "{job_id}")
        path = path.replace("{{webhook_id}}", "{webhook_id}")
        path = path.replace("{{delivery_id}}", "{delivery_id}")
        if method and path.startswith("/api/v1/"):
            result.add((method, path))
    return result


def test_committed_openapi_is_public_bearer_only():
    document = json.loads((ROOT / "docs/openapi.json").read_text(encoding="utf-8"))
    text = json.dumps(document)
    assert document["openapi"] == "3.1.0"
    assert document["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    assert "da_test_" in text
    assert "da_live_" in text
    assert "/app-api/v1/developer" not in text
    assert "wallet:send" not in text


def test_committed_openapi_matches_generated_public_paths():
    from services.developer_api_openapi_service import build_openapi_spec

    committed = json.loads((ROOT / "docs/openapi.json").read_text(encoding="utf-8"))
    generated = build_openapi_spec()
    assert _operations(committed) == _operations(generated)


def test_postman_covers_all_authenticated_public_operations():
    openapi = json.loads((ROOT / "docs/openapi.json").read_text(encoding="utf-8"))
    postman = json.loads((ROOT / "docs/deepalpha_api.postman_collection.json").read_text(encoding="utf-8"))
    postman_ops = _postman_operations(postman.get("item"))
    required = {
        operation for operation in _operations(openapi)
        if operation[1] != "/api/v1/health"
    }
    assert required.issubset(postman_ops)
    assert postman["auth"]["type"] == "bearer"
