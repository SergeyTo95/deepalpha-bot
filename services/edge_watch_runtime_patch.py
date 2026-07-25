import asyncio
from typing import Any

from services.edge_watch_service import edge_watch_worker


def install(app_module: Any) -> None:
    """Run the edge-decision monitor beside the legacy probability watchlist."""
    original = getattr(app_module, "watchlist_worker", None)
    if not callable(original) or getattr(original, "_deepalpha_edge_watch", False):
        return

    async def combined_watchlist_worker() -> None:
        legacy_task = asyncio.create_task(original())
        edge_task = asyncio.create_task(edge_watch_worker(app_module.telegram_bot.bot))
        try:
            await asyncio.gather(legacy_task, edge_task)
        finally:
            for task in (legacy_task, edge_task):
                if not task.done():
                    task.cancel()

    combined_watchlist_worker._deepalpha_edge_watch = True
    app_module.watchlist_worker = combined_watchlist_worker
