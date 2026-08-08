from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class PaymentIntent:
    id: int
    public_reference: str
    user_id: int
    product_code: Optional[str]
    channel: str
    network: Optional[str]
    asset: Optional[str]
    expected_amount_atomic: Optional[int]
    asset_decimals: Optional[int]
    deposit_address: Optional[str]
    status: str


@dataclass(frozen=True)
class ObservedTransfer:
    network: str
    asset: str
    tx_hash: str
    transfer_index: int
    recipient_address: str
    amount_atomic: int
    sender_address: Optional[str] = None
    block_ref: Optional[str] = None
    confirmations: int = 0
    finality: str = "detected"

    def amount_asset(self, decimals: int) -> Decimal:
        scale = Decimal(10) ** int(decimals)
        return Decimal(int(self.amount_atomic)) / scale


@dataclass(frozen=True)
class WorkerNetworkHealth:
    network: str
    asset: str
    enabled: bool
    configured: bool
    status: str
    reason: str = ""
