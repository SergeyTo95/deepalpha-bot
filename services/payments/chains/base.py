from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from services.payments.config import NetworkConfig
from services.payments.models import ObservedTransfer, WorkerNetworkHealth


class PaymentChainNotImplemented(RuntimeError):
    pass


@dataclass(frozen=True)
class PollResult:
    transfers: List[ObservedTransfer]
    next_cursor: Optional[str] = None
    chain_height: Optional[int] = None


class WatchOnlyUSDTAdapter(ABC):
    """Read-only chain adapter contract.

    Adapters may observe public chain data only. This interface deliberately has
    no signing/sending/sweep method and receives no wallet secret material.
    """

    live_polling_implemented = False

    def __init__(self, config: NetworkConfig):
        self.config = config

    def health(self) -> WorkerNetworkHealth:
        if not self.config.enabled:
            return WorkerNetworkHealth(
                network=self.config.network,
                asset=self.config.asset,
                enabled=False,
                configured=False,
                status="disabled",
                reason="network_flag_off",
            )
        if not self.config.configured:
            return WorkerNetworkHealth(
                network=self.config.network,
                asset=self.config.asset,
                enabled=True,
                configured=False,
                status="unconfigured",
                reason="rpc_asset_or_deposit_configuration_invalid",
            )
        if not self.live_polling_implemented:
            return WorkerNetworkHealth(
                network=self.config.network,
                asset=self.config.asset,
                enabled=True,
                configured=True,
                status="blocked",
                reason="live_chain_polling_not_implemented",
            )
        return WorkerNetworkHealth(
            network=self.config.network,
            asset=self.config.asset,
            enabled=True,
            configured=True,
            status="ready",
            reason="",
        )

    @abstractmethod
    async def poll(self, cursor: Optional[str]) -> PollResult:
        raise PaymentChainNotImplemented("live_chain_polling_not_implemented")
