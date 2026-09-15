from services.velia_agent_routes import setup_velia_agent_routes
from services.velia_developer_routes import setup_velia_developer_routes
import logging
import os

from aiohttp import web as aiohttp_web

from services.aiohttp_handler_cancellation_service import (
    handler_cancellation_run_app_kwargs,
)
from services.public_domain_service import configure_public_urls

logger = logging.getLogger(__name__)


def main() -> None:
    # Install public URL and CORS defaults before importing modules that read
    # environment settings at import/runtime initialization.
    public_origin = configure_public_urls(os.environ)
    # Railway web endpoints are HTTPS in both production and PR environments.
    # Fail secure for the new admin cookies even when an environment name is not
    # literally "production"; local HTTP development can explicitly opt out.
    os.environ.setdefault("COOKIE_SECURE", "true")
    os.environ.setdefault("VELYON_IMAGES_DAILY_GLOBAL_LIMIT", "20")
    os.environ.setdefault("VELYON_IMAGES_ESTIMATED_COST_USD", "0.25")
    # FLUX 3 Stage 1 remains fail-closed until a server-side BFL key and the
    # explicit feature flag are configured. The defaults cap each request to a
    # five-second Draft HD clip and the global daily exposure to five clips.
    os.environ.setdefault("VELYON_VIDEOS_ENABLED", "false")
    os.environ.setdefault("VELYON_VIDEOS_DAILY_GLOBAL_LIMIT", "5")
    os.environ.setdefault("VELYON_VIDEOS_DURATION_SECONDS", "5")
    os.environ.setdefault("VELYON_VIDEOS_GENERATE_AUDIO", "true")
    logger.info("DEEPALPHA_PUBLIC_ORIGIN origin=%s", public_origin)

    import admin_routes as admin_routes_module
    import services.velia_chat_service as velia_chat_service_module
    import velia_mobile_routes as velia_mobile_routes_module
    import web as deepalpha_web

    from developer_api_routes import setup_developer_api_routes
    from services.developer_api_analysis_service import ensure_api_analysis_tables
    from services.developer_api_billing_service import ensure_api_billing_tables
    from services.developer_api_commercial_final_service import ensure_commercial_launch_tables
    from services.developer_api_commercial_runtime_v2_patch import install as install_commercial_runtime
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
    from services.developer_api_schema_bootstrap import run_serialized_developer_api_schema_bootstrap
    from services.developer_api_service import ensure_developer_api_tables
    from services.developer_api_webhook_cors_patch import install as install_webhook_cors
    from services.developer_api_webhook_runtime_patch import install as install_webhook_runtime
    from services.developer_api_webhook_service import ensure_api_webhook_tables
    from services.developer_portal_quick_analysis_patch import install as install_portal_quick_analysis
    from services.developer_portal_service import ensure_developer_portal_tables
    from services.developer_portal_webhook_scope_patch import install as install_portal_webhook_scope
    from services.http_security_service import install_http_security
    from services.payments.schema import ensure_payment_tables
    from services.velia_admin_observability_service import install as install_velia_admin_observability
    from services.velia_agent_chat_conflict_patch import install as install_velia_agent_chat_conflict
    from services.velia_agent_chat_planner_service import ensure_velia_agent_chat_tables
    from services.velia_agent_chat_runtime_patch import install as install_velia_agent_chat
    from services.velia_agent_job_service import ensure_velia_agent_tables
    from services.velia_attachment_chat_runtime_patch import install as install_velia_attachment_chat
    from services.velia_attachment_feature_flag_service import install as install_velia_attachment_feature_flag
    from services.velia_attachment_final_safety_patch import install as install_velia_attachment_final_safety
    from services.velia_attachment_message_runtime_patch import install as install_velia_attachment_messages
    from services.velia_attachment_service import ensure_velia_attachment_tables
    from services.velia_chat_latency_runtime_patch import install as install_velia_chat_latency
    from services.velia_code_archive_runtime_patch import install as install_velia_code_archive
    from services.velia_code_archive_service import ensure_velia_code_archive_tables
    from services.velia_chat_service import ensure_velia_chat_tables
    from services.velia_chat_streaming_runtime_patch import install as install_velia_chat_streaming
    from services.velia_conversation_quality_patch import install as install_velia_conversation_quality
    from services.velia_developer_chat_runtime_patch import (
        ensure_velia_developer_chat_tables,
        install as install_velia_developer_chat,
    )
    from services.velia_images_runtime_patch import install as install_velia_images
    from services.velia_images_service import ensure_velia_image_tables
    from services.velia_live_plugins_patch import install as install_velia_live_plugins
    from services.velia_memory_shadow_runtime_patch import install as install_velia_memory_shadow
    from services.velia_memory_shadow_service import ensure_velia_memory_shadow_tables
    from services.velia_mobile_auth_service import ensure_velia_mobile_auth_tables
    from services.velia_mobile_commercial_routes import setup_velia_mobile_commercial_routes
    from services.velia_mobile_commercial_service import ensure_commercial_runtime_tables
    from services.velia_mobile_hardening_service import install as install_velia_mobile_hardening
    from services.velia_mobile_streaming_service import setup_velia_mobile_streaming_route
    from services.velia_plugin_service import ensure_velia_plugin_tables
    from services.velia_software_factory_autonomy_service import install_autonomy as install_velia_software_factory_autonomy
    from services.velia_software_factory_chat_runtime_patch import install as install_velia_software_factory_chat
    from services.velia_software_factory_live_pilot_admin_routes import setup_factory_pilot_admin_routes
    from services.velia_software_factory_routes import setup_velia_software_factory_routes
    from services.velia_telegram_connect_page_patch import install as install_velia_telegram_connect_page
    from services.velia_usdt_checkout_routes import setup_velia_usdt_checkout_routes
    from services.velia_user_profile_runtime_patch import install as install_velia_user_profile
    from services.velia_user_profile_service import ensure_velia_user_profile_table
    from services.velia_videos_runtime_patch import install as install_velia_videos
    from services.velia_videos_service import ensure_velia_video_tables
    from velia_code_archive_routes import setup_velia_code_archive_routes
    from velia_image_routes import setup_velia_image_routes
    from velia_mobile_attachment_routes import setup_velia_mobile_attachment_routes
    from velia_plugin_routes import setup_velia_plugin_routes
    from velia_profile_routes import setup_velia_profile_routes
    from velia_video_routes import setup_velia_video_routes

    install_http_security(deepalpha_web.app, admin_routes_module)
    install_velia_admin_observability(admin_routes_module)
    if not any(path == "/admin/factory-pilot" for _, path in admin_routes_module.SECTIONS):
        admin_routes_module.SECTIONS.append(("Factory Pilot", "/admin/factory-pilot"))
    setup_factory_pilot_admin_routes(
        deepalpha_web.app,
        guard=admin_routes_module._guard,
        layout=admin_routes_module._layout,
        key=admin_routes_module._key,
        request_id=admin_routes_module._request_id,
    )
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
    install_velia_attachment_final_safety(
        velia_chat_service_module,
        velia_mobile_routes_module,
    )
    # Attachment-aware persistence must be the innermost chat sender so every
    # existing VELIA quality, profile, image, video, memory and hardening wrapper
    # still applies to file-backed turns. Restore the original prompt builder;
    # bounded attachment context is appended after the established wrappers.
    original_velia_prompt_builder = velia_chat_service_module._build_prompt
    install_velia_attachment_chat(velia_chat_service_module)
    velia_chat_service_module._build_prompt = original_velia_prompt_builder
    velia_mobile_routes_module.send_message = velia_chat_service_module.send_message
    install_velia_live_plugins(velia_chat_service_module)
    install_velia_conversation_quality(
        velia_chat_service_module,
        velia_mobile_routes_module,
    )
    install_velia_user_profile(velia_chat_service_module)
    install_velia_images(velia_chat_service_module)
    # Video is outer to image routing. It can intentionally consume one linked
    # image for i2v while delegating every non-video request to the unchanged
    # image/File Analyst path.
    install_velia_videos(velia_chat_service_module)
    install_velia_memory_shadow(
        velia_chat_service_module,
        velia_mobile_routes_module,
    )

    # Compatibility-only legacy admin imports/patches. Existing Developer API
    # regression suites and patch chains expect these modules to be initialized,
    # but VELIA Control Center Stage 1 intentionally DOES NOT register either
    # legacy admin route set below. In particular, developer_api_admin_routes can
    # create and reveal a raw API key in browser HTML, so setup_*admin_routes(app)
    # must stay absent until a separate secret-safe Control Center design exists.
    from developer_api_admin_routes import setup_developer_api_admin_routes  # noqa: F401
    from developer_api_commercial_admin_routes_v2 import (
        install as install_admin_commercial,
        setup_developer_api_commercial_admin_routes,  # noqa: F401
    )
    from developer_api_commercial_routes_v2 import setup_developer_api_commercial_routes
    from developer_api_openapi_routes import setup_developer_api_openapi_routes
    from developer_api_opportunity_routes import setup_developer_api_opportunity_routes
    from developer_api_webhook_routes import setup_developer_api_webhook_routes
    from developer_portal_jobs_routes import setup_developer_portal_jobs_routes
    from developer_portal_opportunity_routes import setup_developer_portal_opportunity_routes
    from developer_portal_routes import setup_developer_portal_routes
    from services.developer_api_admin_observability_patch import install as install_admin_observability
    from services.developer_api_admin_opportunity_patch import install as install_admin_opportunity
    from services.developer_api_admin_webhook_patch import install as install_admin_webhooks

    # These wrap functions in the unmounted legacy admin module only; they do not
    # register routes. Keeping them preserves the established runtime/test patch
    # chain without reopening /admin/api or legacy query-key authentication.
    install_admin_observability()
    install_admin_webhooks()
    install_admin_opportunity()
    install_admin_commercial()

    setup_developer_api_openapi_routes(deepalpha_web.app)
    setup_developer_api_routes(deepalpha_web.app)
    setup_developer_api_opportunity_routes(deepalpha_web.app)
    setup_developer_api_webhook_routes(deepalpha_web.app)
    setup_developer_portal_routes(deepalpha_web.app)
    setup_developer_portal_jobs_routes(deepalpha_web.app)
    setup_developer_portal_opportunity_routes(deepalpha_web.app)
    setup_developer_api_commercial_routes(deepalpha_web.app)

    web_user_resolver = getattr(deepalpha_web, "_get_authenticated_web_user_id", None)
    if not callable(web_user_resolver):
        web_user_resolver = getattr(deepalpha_web, "_current_web_user_id", None)
    if not callable(web_user_resolver):
        raise RuntimeError("Web session resolver is unavailable")
    velia_mobile_routes_module.setup_velia_mobile_routes(
        deepalpha_web.app,
        web_user_resolver,
    )
    setup_velia_mobile_commercial_routes(
        deepalpha_web.app,
        velia_mobile_routes_module,
    )
    setup_velia_usdt_checkout_routes(
        deepalpha_web.app,
        velia_mobile_routes_module,
        web_user_resolver,
    )
    install_velia_attachment_feature_flag(
        deepalpha_web.app,
        velia_mobile_routes_module,
    )
    setup_velia_mobile_attachment_routes(deepalpha_web.app)
    setup_velia_image_routes(deepalpha_web.app)
    setup_velia_code_archive_routes(deepalpha_web.app)
    setup_velia_video_routes(deepalpha_web.app)
    setup_velia_plugin_routes(deepalpha_web.app)
    setup_velia_profile_routes(deepalpha_web.app)
    install_velia_mobile_hardening(
        deepalpha_web.app,
        velia_chat_service_module,
        velia_mobile_routes_module,
    )
    # Add safe attachment context and public metadata only after hardening has
    # installed its final history reader, then let latency wrap the complete
    # prompt path.
    install_velia_attachment_messages(
        velia_chat_service_module,
        velia_mobile_routes_module,
    )
    # Install after every functional wrapper so timing covers the complete
    # production path and the mobile route keeps the final wrapped sender.
    install_velia_chat_latency(
        velia_chat_service_module,
        velia_mobile_routes_module,
    )
    # Streaming wraps only generation and calls the already hardened final
    # sender, preserving idempotency, budget, shadow memory and persistence.
    install_velia_chat_streaming(velia_chat_service_module)
    # Developer handles repository requests. Agent Chat is installed after it;
    # its conflict guard protects the two legacy plan types. Software Factory is
    # outermost for high-level product-build intents and adds its own cross-plan
    # conflict check before creating an autonomous run.
    install_velia_developer_chat(velia_chat_service_module)
    install_velia_agent_chat(velia_chat_service_module)
    install_velia_agent_chat_conflict(velia_chat_service_module)
    install_velia_software_factory_chat(velia_chat_service_module)
    # Code archive routing is outermost: an explicit ZIP request after a
    # completed Coding Agent run is served deterministically without
    # entering Developer/Agent/Factory planning again.
    install_velia_code_archive(velia_chat_service_module)
    install_velia_software_factory_autonomy(deepalpha_web.app)
    setup_velia_mobile_streaming_route(
        deepalpha_web.app,
        velia_chat_service_module,
        velia_mobile_routes_module,
    )
    setup_velia_developer_routes(deepalpha_web.app, velia_mobile_routes_module)
    setup_velia_software_factory_routes(deepalpha_web.app, velia_mobile_routes_module)
    setup_velia_agent_routes(deepalpha_web.app, velia_mobile_routes_module)
    install_velia_telegram_connect_page(
        deepalpha_web.app,
        web_user_resolver,
    )

    def ensure_developer_api_schema() -> None:
        ensure_developer_api_tables()
        ensure_api_billing_tables()
        ensure_developer_portal_tables()
        ensure_api_analysis_tables()
        ensure_api_observability_tables()
        ensure_api_opportunity_tables()
        ensure_api_webhook_tables()
        ensure_opportunity_webhook_trigger()
        ensure_api_commercial_tables()
        ensure_commercial_launch_tables()
        ensure_velia_mobile_auth_tables()
        ensure_commercial_runtime_tables()
        ensure_payment_tables()
        ensure_velia_chat_tables()
        ensure_velia_developer_chat_tables()
        ensure_velia_agent_tables()
        ensure_velia_agent_chat_tables()
        ensure_velia_attachment_tables()
        ensure_velia_image_tables()
        ensure_velia_code_archive_tables()
        ensure_velia_video_tables()
        ensure_velia_plugin_tables()
        ensure_velia_user_profile_table()
        ensure_velia_memory_shadow_tables()

    try:
        run_serialized_developer_api_schema_bootstrap(
            "webapp",
            ensure_developer_api_schema,
        )
    except Exception:
        # Keep the main WebApp available during a transient or invalid schema
        # startup. Developer API and VELIA mobile endpoints remain fail-closed
        # until their storage is ready.
        logger.exception("DEVELOPER_API_TABLE_INIT_FAILED")

    port = int(os.getenv("PORT", 3000))
    # aiohttp 3.9+ supports native handler cancellation. The production stack
    # currently resolves aiohttp 3.8 through aiogram 2.x, where a focused
    # protocol backport is installed instead and no unsupported run_app keyword
    # is passed.
    run_app_kwargs = handler_cancellation_run_app_kwargs(aiohttp_web)
    aiohttp_web.run_app(
        deepalpha_web.app,
        host="0.0.0.0",
        port=port,
        **run_app_kwargs,
    )


if __name__ == "__main__":
    main()
