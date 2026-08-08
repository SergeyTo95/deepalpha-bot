from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

from services.payments.chains import (
    BnbUSDTAdapter,
    PolygonUSDTAdapter,
    SolanaUSDTAdapter,
    TonUSDTAdapter,
    TronUSDTAdapter,
)
from services.payments.config import all_network_configs, poll_interval_seconds, worker_enabled
from services.payments.live_runtime import process_finalized_transfer
from services.payments.models import WorkerNetworkHealth
from services.payments.repository import update_worker_state


logger = logging.getLogger(__name__)


class PaymentWorker:
    """Watch-only VELIA USDT worker.

    Phase 1 can observe finalized transfers on TRON, Solana and TON. The worker
    has no signing, sending, seed, private-key or sweep capability. BNB/Polygon
    remain structurally present but fail closed until a separate reviewed rail.
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
        self._cursors: Dict[str, Optional[str]] = {name: None for name in self.adapters}

    def health_snapshot(self) -> Dict[str, object]:
        global_enabled = worker_enabled()
        networks = {name: adapter.health().__dict__ for name, adapter in self.adapters.items()}
        ready_networks = [
            name
            for name, health in networks.items()
            if global_enabled and health.get("status") == "ready"
        ]
        return {
            "service": "velia-payment-worker",
            "mode": "usdt_watch_only_v1",
            "worker_enabled": global_enabled,
            "live_money_acceptance": bool(ready_networks),
            "signing_capability": False,
            "ready_networks": ready_networks,
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
                continue

            results[name] = health
            if health.status != "ready":
                try:
                    await asyncio.to_thread(
                        update_worker_state,
                        name,
                        enabled=bool(health.enabled),
                        mode="usdt_watch_only_v1",
                        status=health.status,
                        error_code=health.reason or None,
                        success=False,
                    )
                except Exception:
                    logger.exception("VELIA_PAYMENT_WORKER_STATE_WRITE_FAILED network=%s", name)
                continue

            try:
                poll_result = await adapter.poll(self._cursors.get(name))
                matched = 0
                failures = 0
                for transfer in poll_result.transfers:
                    outcome = await asyncio.to_thread(process_finalized_transfer, transfer)
                    if outcome.get("ok") and outcome.get("matched"):
                        matched += 1
                    elif not outcome.get("ok"):
                        failures += 1
                self._cursors[name] = poll_result.next_cursor or self._cursors.get(name)
                await asyncio.to_thread(
                    update_worker_state,
                    name,
                    enabled=True,
                    mode="usdt_watch_only_v1",
                    status="running",
                    cursor_value=self._cursors.get(name),
                    chain_height=poll_result.chain_height,
                    error_code=None if failures == 0 else "transfer_processing_failed",
                    success=failures == 0,
                )
                logger.info(
                    "VELIA_PAYMENT_POLL_OK network=%s observed=%s matched=%s failures=%s",
                    name,
                    len(poll_result.transfers),
                    matched,
                    failures,
                )
            except Exception as exc:
                logger.warning(
                    "VELIA_PAYMENT_POLL_FAILED network=%s error=%s",
                    name,
                    exc.__class__.__name__,
                )
                try:
                    await asyncio.to_thread(
                        update_worker_state,
                        name,
                        enabled=True,
                        mode="usdt_watch_only_v1",
                        status="error",
                        error_code=exc.__class__.__name__,
                        success=False,
                    )
                except Exception:
                    logger.exception("VELIA_PAYMENT_WORKER_STATE_WRITE_FAILED network=%s", name)
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
