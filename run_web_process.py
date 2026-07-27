import logging
import os

from aiohttp import web as aiohttp_web

logger = logging.getLogger(__name__)


def main() -> None:
    import admin_routes as admin_routes_module
    import web as deepalpha_web

    from developer_api_routes import setup_developer_api_routes
    from services.developer_api_analysis_service import ensure_api_analysis_tables
    from services.developer_api_billing_service import ensure_api_billing_tables
    from services.developer_api_commercial_runtime_patch import install as install_commercial_runtime
    from services.developer_api_commercial_service import ensure_api_commercial_tables
    from services.developer_api_health_patch import install as install_api_health_observability
    from services.developer_api_observability_service import ensure_api_observability_tables
    from services.developer_api_openapi_runtime_patch import install as install_openapi_runtime
    from services.developer_api_opportunity_runtime_patch import install as install_opportunity_runtime
    from services.developer_api_opportunity_scope_patch import install as install_opportunity_scope
    from services.developer_api_opportunity_service import ensure_api_opportunity_tables
    from services.developer_api_opportunity_webhook_patch import (
        ensure_opportunity_webhook_trigger,
        install as install_opportunity_webhook_events,
    )
    from services.developer_api_service import ensure_developer_api_tables
    from services.developer_api_webhook_cors_patch import install as install_webhook_cors
    from services.developer_api_webhook_runtime_patch import install as install_webhook_runtime
    from services.developer_api_webhook_service import ensure_api_webhook_tables
    from services.developer_portal_quick_analysis_patch import install as install_portal_quick_analysis
    from services.developer_portal_service import ensure_developer_portal_tables
    from services.developer_portal_webhook_scope_patch import install as install_portal_webhook_scope
    from services.http_security_service import install_http_security

    install_http_security(deepalpha_web.app, admin_routes_module)
    install_webhook_cors(deepalpha_web.app)
    install_portal_quick_analysis()
    install_portal_webhook_scope()
    install_opportunity_scope()
    install_opportunity_webhook_events()
    install_api_health_observability()
    install_webhook_runtime()
    install_opportunity_runtime()
    install_openapi_runtime()
    install_commercial_runtime()

    # Import after runtime security and capability patches are installed.
    from developer_api_admin_routes import setup_developer_api_admin_routes
    from developer_api_commercial_admin_routes import (
        install as install_admin_commercial,
        setup_developer_api_commercial_admin_routes,
    )
    from developer_api_commercial_routes import setup_developer_api_commercial_routes
    from developer_api_openapi_routes import setup_developer_api_openapi_routes
    from developer_api_opportunity_routes import setup_developer_api_opportunity_routes
    from developer_api_webhook_routes import setup_developer_api_webhook_routes
    from developer_portal_jobs_routes import setup_developer_portal_jobs_routes
    from developer_portal_opportunity_routes import setup_developer_portal_opportunity_routes
    from developer_portal_routes import setup_developer_portal_routes
    from services.developer_api_admin_observability_patch import install as install_admin_observability
    from services.developer_api_admin_opportunity_patch import install as install_admin_opportunity
    from services.developer_api_admin_webhook_patch import install as install_admin_webhooks

    install_admin_observability()
    install_admin_webhooks()
    install_admin_opportunity()
    install_admin_commercial()

    setup_developer_api_openapi_routes(deepalpha_web.app)
    setup_developer_api_routes(deepalpha_web.app)
    setup_developer_api_opportunity_routes(deepalpha_web.app)
    setup_developer_api_webhook_routes(deepalpha_web.app)
    setup_developer_api_admin_routes(deepalpha_web.app)
    setup_developer_api_commercial_admin_routes(deepalpha_web.app)
    setup_developer_portal_routes(deepalpha_web.app)
    setup_developer_portal_jobs_routes(deepalpha_web.app)
    setup_developer_portal_opportunity_routes(deepalpha_web.app)
    setup_developer_api_commercial_routes(deepalpha_web.app)

    try:
        ensure_developer_api_tables()
        ensure_api_billing_tables()
        ensure_developer_portal_tables()
        ensure_api_analysis_tables()
        ensure_api_observability_tables()
        ensure_api_opportunity_tables()
        ensure_api_webhook_tables()
        ensure_opportunity_webhook_trigger()
        ensure_api_commercial_tables()
    except Exception:
        # Keep the existing WebApp available during a transient database issue;
        # authenticated Developer API endpoints will return 503 until storage recovers.
        logger.exception("DEVELOPER_API_TABLE_INIT_FAILED")

    port = int(os.getenv("PORT", 3000))
    aiohttp_web.run_app(deepalpha_web.app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
