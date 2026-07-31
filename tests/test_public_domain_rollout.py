import json
from pathlib import Path

from services.developer_api_openapi_service import build_openapi_spec
from services.public_domain_service import (
    CANONICAL_PUBLIC_ORIGIN,
    configure_public_urls,
    resolve_public_origin,
)


ROOT = Path(__file__).resolve().parents[1]


def test_production_always_uses_custom_domain_even_with_stale_railway_url():
    env = {
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "WEBAPP_URL": "https://deepalpha-bot-production.up.railway.app",
        "WEB_APP_BASE_URL": "https://8hi5141d.up.railway.app",
        "CORS_ALLOWED_ORIGINS": (
            "https://deepalpha-bot-production.up.railway.app,https://partner.example"
        ),
    }

    origin = configure_public_urls(env)

    assert origin == CANONICAL_PUBLIC_ORIGIN
    assert env["WEBAPP_URL"] == CANONICAL_PUBLIC_ORIGIN
    assert env["WEB_APP_BASE_URL"] == CANONICAL_PUBLIC_ORIGIN
    assert env["PUBLIC_BASE_URL"] == CANONICAL_PUBLIC_ORIGIN
    assert "deepalpha-bot-production.up.railway.app" not in env["CORS_ALLOWED_ORIGINS"]
    assert CANONICAL_PUBLIC_ORIGIN in env["CORS_ALLOWED_ORIGINS"].split(",")
    assert "https://partner.example" in env["CORS_ALLOWED_ORIGINS"].split(",")


def test_preview_uses_its_own_railway_public_domain_when_no_custom_url_exists():
    env = {
        "RAILWAY_ENVIRONMENT_NAME": "preview",
        "RAILWAY_PUBLIC_DOMAIN": "deepalpha-pr-999.up.railway.app",
    }

    assert resolve_public_origin(env) == "https://deepalpha-pr-999.up.railway.app"


def test_preview_preserves_explicit_non_railway_origin():
    env = {
        "RAILWAY_ENVIRONMENT_NAME": "preview",
        "WEBAPP_URL": "https://preview.deepalpha.example",
        "RAILWAY_PUBLIC_DOMAIN": "deepalpha-pr-999.up.railway.app",
    }

    assert resolve_public_origin(env) == "https://preview.deepalpha.example"


def test_bot_configures_domain_before_importing_app():
    source = (ROOT / "run_bot_process.py").read_text(encoding="utf-8")
    configure_index = source.index("configure_public_urls(os.environ)")
    import_index = source.index("    import app")
    assert configure_index < import_index


def test_web_process_configures_domain_before_web_imports():
    source = (ROOT / "run_web_process.py").read_text(encoding="utf-8")
    configure_index = source.index("configure_public_urls(os.environ)")
    import_index = source.index("    import web as deepalpha_web")
    assert configure_index < import_index


def test_openapi_and_postman_publish_custom_domain():
    spec = build_openapi_spec()
    assert spec["servers"] == [
        {
            "url": CANONICAL_PUBLIC_ORIGIN,
            "description": "DeepAlpha production API",
        }
    ]
    assert spec["x-documentation-endpoints"]["swagger_ui"].startswith(
        CANONICAL_PUBLIC_ORIGIN
    )

    collection = json.loads(
        (ROOT / "docs/deepalpha_api.postman_collection.json").read_text(encoding="utf-8")
    )
    variables = {item["key"]: item["value"] for item in collection["variable"]}
    assert variables["base_url"] == CANONICAL_PUBLIC_ORIGIN
