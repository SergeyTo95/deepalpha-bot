import logging
import os

from aiohttp import web as aiohttp_web

logger = logging.getLogger(__name__)


def main() -> None:
    import admin_routes as admin_routes_module
    import web as deepalpha_web

    from developer_api_routes import setup_developer_api_routes
    from services.developer_api_billing_service import ensure_api_billing_tables
    from services.developer_api_service import ensure_developer_api_tables
    from services.http_security_service import install_http_security

    install_http_security(deepalpha_web.app, admin_routes_module)

    # Import after the admin guard is replaced so the API admin handlers capture
    # cookie-based authentication instead of the legacy query-string secret.
    from developer_api_admin_routes import setup_developer_api_admin_routes

    setup_developer_api_routes(deepalpha_web.app)
    setup_developer_api_admin_routes(deepalpha_web.app)

    try:
        ensure_developer_api_tables()
        ensure_api_billing_tables()
    except Exception:
        # Keep the existing WebApp available during a transient database issue;
        # authenticated Developer API endpoints will return 503 until storage recovers.
        logger.exception("DEVELOPER_API_TABLE_INIT_FAILED")

    port = int(os.getenv("PORT", 3000))
    aiohttp_web.run_app(deepalpha_web.app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
