from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from services.payments.chains.base import PollResult, WatchOnlyUSDTAdapter
from services.payments.models import ObservedTransfer


class TronUSDTAdapter(WatchOnlyUSDTAdapter):
    live_polling_implemented = True

    def _fetch(self) -> Dict[str, Any]:
        url = (
            self.config.rpc_url
            + "/v1/accounts/"
            + quote(self.config.deposit_address, safe="")
            + "/transactions/trc20"
        )
        headers = {"Accept": "application/json"}
        if self.config.api_key:
            headers["TRON-PRO-API-KEY"] = self.config.api_key
        response = requests.get(
            url,
            params={
                "only_confirmed": "true",
                "limit": 200,
                "contract_address": self.config.canonical_asset_identifier,
            },
            headers=headers,
            timeout=20,
        )
        if response.status_code != 200:
            raise RuntimeError(f"tron_http_{response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("tron_invalid_response")
        return data

    @staticmethod
    def _transfer_index(item: Dict[str, Any]) -> int:
        identity = "|".join(
            str(item.get(key) or "")
            for key in ("transaction_id", "from", "to", "value")
        )
        return int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF

    async def poll(self, cursor: Optional[str]) -> PollResult:
        del cursor  # latest confirmed account history is intentionally re-read; DB dedupes it.
        payload = await asyncio.to_thread(self._fetch)
        transfers = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            token_info = item.get("token_info") or {}
            contract = str(token_info.get("address") or item.get("contract_address") or "").strip()
            recipient = str(item.get("to") or "").strip()
            if contract != self.config.canonical_asset_identifier:
                continue
            if recipient != self.config.deposit_address:
                continue
            try:
                amount_atomic = int(str(item.get("value") or "0"))
                block_timestamp_ms = int(item.get("block_timestamp") or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            tx_hash = str(item.get("transaction_id") or "").strip()
            if not tx_hash or amount_atomic <= 0 or block_timestamp_ms <= 0:
                continue
            transfers.append(
                ObservedTransfer(
                    network="tron",
                    asset="USDT",
                    tx_hash=tx_hash,
                    transfer_index=self._transfer_index(item),
                    recipient_address=self.config.deposit_address,
                    sender_address=str(item.get("from") or "").strip() or None,
                    amount_atomic=amount_atomic,
                    block_ref=str(block_timestamp_ms),
                    block_timestamp=block_timestamp_ms // 1000,
                    confirmations=1,
                    finality="finalized",
                )
            )
        return PollResult(transfers=transfers)
