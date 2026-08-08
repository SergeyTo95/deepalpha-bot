from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional


SUPPORTED_NETWORKS = ("tron", "solana", "ton", "bnb", "polygon")
PHASE1_USDT_NETWORKS = ("tron", "solana", "ton")
USDT_DECIMALS = 6

# Canonical issuer-published identifiers. Phase 1 refuses an override that does
# not exactly match these allow-listed assets. BNB/Polygon stay disabled until a
# current USD₮ identifier is separately reviewed and added here.
CANONICAL_USDT_IDENTIFIERS: Dict[str, Optional[str]] = {
    "tron": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
    "solana": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "ton": "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
    "bnb": None,
    "polygon": None,
}


@dataclass(frozen=True)
class NetworkConfig:
    network: str
    asset: str
    enabled: bool
    rpc_url: str
    asset_identifier: str
    canonical_asset_identifier: str
    deposit_address: str
    api_key: str = ""
    asset_decimals: int = USDT_DECIMALS

    @property
    def canonical_asset(self) -> bool:
        return bool(
            self.canonical_asset_identifier
            and self.asset_identifier == self.canonical_asset_identifier
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self.network in PHASE1_USDT_NETWORKS
            and self.rpc_url.startswith("https://")
            and self.canonical_asset
            and self.deposit_address
            and self.asset_decimals == USDT_DECIMALS
        )


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def worker_enabled() -> bool:
    return _truthy(os.getenv("VELIA_PAYMENT_WORKER_ENABLED", "false"))


def crypto_checkout_enabled() -> bool:
    return _truthy(os.getenv("VELIA_USDT_CHECKOUT_ENABLED", "false"))


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
    canonical = str(CANONICAL_USDT_IDENTIFIERS.get(name) or "")
    configured_identifier = str(os.getenv(identifier_env, "") or "").strip() or canonical
    api_key_env = {
        "tron": "TRON_RPC_API_KEY",
        "solana": "SOLANA_RPC_API_KEY",
        "ton": "TON_RPC_API_KEY",
        "bnb": "BNB_RPC_API_KEY",
        "polygon": "POLYGON_RPC_API_KEY",
    }[name]
    return NetworkConfig(
        network=name,
        asset="USDT",
        enabled=_truthy(os.getenv(f"VELIA_PAYMENT_{prefix}_ENABLED", "false")),
        rpc_url=str(os.getenv(f"{prefix}_RPC_URL", "") or "").strip().rstrip("/"),
        asset_identifier=configured_identifier,
        canonical_asset_identifier=canonical,
        deposit_address=str(os.getenv(f"VELIA_PAYMENT_{prefix}_DEPOSIT_ADDRESS", "") or "").strip(),
        api_key=str(os.getenv(api_key_env, "") or "").strip(),
        asset_decimals=USDT_DECIMALS,
    )


def all_network_configs() -> Dict[str, NetworkConfig]:
    return {name: network_config(name) for name in SUPPORTED_NETWORKS}


def configured_phase1_networks() -> Dict[str, NetworkConfig]:
    return {
        name: config
        for name, config in all_network_configs().items()
        if name in PHASE1_USDT_NETWORKS and config.configured
    }
