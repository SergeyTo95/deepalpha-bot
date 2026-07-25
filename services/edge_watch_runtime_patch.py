import asyncio
import os
from typing import Any

from services import edge_watch_service
from services.edge_watch_market_resolver import fetch_watch_market_by_slug


def _env_true(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def install(app_module: Any) -> None:
    """Run the edge-decision monitor beside the legacy probability watchlist."""
    original = getattr(app_module, "watchlist_worker", None)
    if not callable(original) or getattr(original, "_deepalpha_edge_watch", False):
        return

    # Legacy watchlist rows may contain an event slug instead of an exact market
    # slug. Resolve the saved question to the correct submarket before reading price.
    edge_watch_service.fetch_market_by_slug = fetch_watch_market_by_slug

    # Edge transitions are included with the watchlist by default. This avoids
    # charging once for a legacy probability move and again for the same edge move.
    original_charge = edge_watch_service.charge_watchlist_event
    billing_enabled = _env_true(os.getenv("EDGE_WATCH_BILLING_ENABLED"), default=False)

    def charge_edge_alert(user_id, watchlist_id, market_slug, event_type, fingerprint):
        if not billing_enabled:
            return {"charged": False, "reason": "edge_alert_included", "cost": 0}
        return original_charge(
            user_id,
            watchlist_id,
            market_slug,
            "probability_change",
            fingerprint,
        )

    edge_watch_service.charge_watchlist_event = charge_edge_alert

    async def combined_watchlist_worker() -> None:
        legacy_task = asyncio.create_task(original())
        edge_task = asyncio.create_task(edge_watch_service.edge_watch_worker(app_module.telegram_bot.bot))
        try:
            await asyncio.gather(legacy_task, edge_task)
        finally:
            for task in (legacy_task, edge_task):
                if not task.done():
                    task.cancel()

    combined_watchlist_worker._deepalpha_edge_watch = True
    app_module.watchlist_worker = combined_watchlist_worker
