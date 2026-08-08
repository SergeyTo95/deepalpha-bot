from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict


SUPPORTED_NETWORKS = ("tron", "solana", "ton", "bnb", "polygon")


@dataclass(frozen=True)
class NetworkConfig:
    network: str
    asset: str
    enabled: bool
    rpc_url: str
    asset_identifier: str

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.rpc_url and self.asset_identifier)


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def worker_enabled() -> bool:
    return _truthy(os.getenv("VELIA_PAYMENT_WORKER_ENABLED", "false"))


def poll_interval_seconds() -> float:
    raw = str(os.getenv("VELIA_PAYMENT_POLL_INTERVAL_SECONDS", "30") or "30").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 30.0
    return max(5.0, min(value, 300.0))


def network_config(network: str) -> NetworkConfig:
    name = str(network or "").strip().lower()
    if name not in SUPPORTED_NETWORKS:
        raise ValueError("unsupported_payment_network")
    prefix = name.upper()
    identifier_env = {
        "tron": "TRON_USDT_CONTRACT",
        "solana": "SOLANA_USDT_MINT",
        "ton": "TON_USDT_JETTON_MASTER",
        "bnb": "BNB_USDT_CONTRACT",
        "polygon": "POLYGON_USDT_CONTRACT",
    }[name]
    return NetworkConfig(
        network=name,
        asset="USDT",
        enabled=_truthy(os.getenv(f"VELIA_PAYMENT_{prefix}_ENABLED", "false")),
        rpc_url=str(os.getenv(f"{prefix}_RPC_URL", "") or "").strip(),
        asset_identifier=str(os.getenv(identifier_env, "") or "").strip(),
    )


def all_network_configs() -> Dict[str, NetworkConfig]:
    return {name: network_config(name) for name in SUPPORTED_NETWORKS}
