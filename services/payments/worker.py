from __future__ import annotations

import asyncio
import logging
from typing import Dict

from services.payments.chains import (
    BnbUSDTAdapter,
    PolygonUSDTAdapter,
    SolanaUSDTAdapter,
    TonUSDTAdapter,
    TronUSDTAdapter,
)
from services.payments.config import all_network_configs, poll_interval_seconds, worker_enabled
from services.payments.models import WorkerNetworkHealth
from services.payments.repository import update_worker_state


logger = logging.getLogger(__name__)


class PaymentWorker:
    """Watch-only payment worker orchestrator.

    Foundation mode never invokes a live chain poll. Adapter implementations are
    deliberately blocked until each network gets its own reviewed implementation
    and canonical asset/finality tests.
    """

    def __init__(self) -> None:
        configs = all_network_configs()
        self.adapters = {
            "tron": TronUSDTAdapter(configs["tron"]),
            "solana": SolanaUSDTAdapter(configs["solana"]),
            "ton": TonUSDTAdapter(configs["ton"]),
            "bnb": BnbUSDTAdapter(configs["bnb"]),
            "polygon": PolygonUSDTAdapter(configs["polygon"]),
        }
        self._stopping = asyncio.Event()

    def health_snapshot(self) -> Dict[str, object]:
        networks = {name: adapter.health().__dict__ for name, adapter in self.adapters.items()}
        return {
            "service": "velia-payment-worker",
            "mode": "foundation_watch_only",
            "worker_enabled": worker_enabled(),
            "live_money_acceptance": False,
            "signing_capability": False,
            "networks": networks,
        }

    async def run_once(self) -> Dict[str, WorkerNetworkHealth]:
        results: Dict[str, WorkerNetworkHealth] = {}
        global_enabled = worker_enabled()
        for name, adapter in self.adapters.items():
            health = adapter.health()
            if not global_enabled:
                health = WorkerNetworkHealth(
                    network=health.network,
                    asset=health.asset,
                    enabled=False,
                    configured=False,
                    status="disabled",
                    reason="worker_flag_off",
                )
            results[name] = health
            try:
                await asyncio.to_thread(
                    update_worker_state,
                    name,
                    enabled=bool(global_enabled and health.enabled),
                    mode="foundation_watch_only",
                    status=health.status,
                    error_code=health.reason or None,
                    success=health.status == "disabled",
                )
            except Exception:
                logger.exception("VELIA_PAYMENT_WORKER_STATE_WRITE_FAILED network=%s", name)

            # Critical safety boundary: Stage 1 never calls adapter.poll(). A
            # network-specific PR must explicitly remove this guard only after
            # canonical asset verification/finality/idempotency tests exist.
            if global_enabled and health.configured:
                logger.warning(
                    "VELIA_PAYMENT_NETWORK_BLOCKED network=%s reason=foundation_live_polling_disabled",
                    name,
                )
        return results

    async def run_forever(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("VELIA_PAYMENT_WORKER_TICK_FAILED")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=poll_interval_seconds())
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stopping.set()
