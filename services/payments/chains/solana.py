from __future__ import annotations

from typing import Optional

from services.payments.chains.base import PaymentChainNotImplemented, PollResult, WatchOnlyUSDTAdapter


class SolanaUSDTAdapter(WatchOnlyUSDTAdapter):
    async def poll(self, cursor: Optional[str]) -> PollResult:
        raise PaymentChainNotImplemented("solana_live_watcher_not_enabled_in_foundation")
