from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, Dict, Optional

import requests

from services.payments.chains.base import PollResult, WatchOnlyUSDTAdapter
from services.payments.models import ObservedTransfer


class TonUSDTAdapter(WatchOnlyUSDTAdapter):
    live_polling_implemented = True

    def _base_url(self) -> str:
        base = self.config.rpc_url.rstrip("/")
        if base.endswith("/api/v3"):
            return base
        return base + "/api/v3"

    def _fetch(self, cursor: Optional[str]) -> Dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.config.api_key:
            headers["X-API-Key"] = self.config.api_key
        params: Dict[str, Any] = {
            "owner_address": self.config.deposit_address,
            "jetton_master": self.config.canonical_asset_identifier,
            "direction": "in",
            "limit": 100,
            "sort": "desc",
        }
        if cursor:
            try:
                params["start_utime"] = max(0, int(cursor) - 2)
            except (TypeError, ValueError, OverflowError):
                pass
        else:
            # First start only needs the active invoice window. Re-reading a
            # little beyond 30 minutes is harmless because intent matching also
            # validates the transfer timestamp against created/expires_at.
            params["start_utime"] = int(time.time()) - 40 * 60
        response = requests.get(
            self._base_url() + "/jetton/transfers",
            params=params,
            headers=headers,
            timeout=20,
        )
        if response.status_code != 200:
            raise RuntimeError(f"ton_http_{response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("ton_invalid_response")
        return payload

    @staticmethod
    def _transfer_index(item: Dict[str, Any]) -> int:
        identity = "|".join(
            str(item.get(key) or "")
            for key in ("transaction_hash", "source", "destination", "amount", "query_id")
        )
        return int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF

    async def poll(self, cursor: Optional[str]) -> PollResult:
        payload = await asyncio.to_thread(self._fetch, cursor)
        transfers = []
        newest_timestamp = None
        for item in payload.get("jetton_transfers") or []:
            if not isinstance(item, dict):
                continue
            if bool(item.get("transaction_aborted")):
                continue
            if str(item.get("jetton_master") or "").strip() != self.config.canonical_asset_identifier:
                continue
            try:
                amount_atomic = int(str(item.get("amount") or "0"))
                block_timestamp = int(item.get("transaction_now") or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            tx_hash = str(item.get("transaction_hash") or "").strip()
            if not tx_hash or amount_atomic <= 0 or block_timestamp <= 0:
                continue
            newest_timestamp = (
                block_timestamp
                if newest_timestamp is None
                else max(newest_timestamp, block_timestamp)
            )
            transfers.append(
                ObservedTransfer(
                    network="ton",
                    asset="USDT",
                    tx_hash=tx_hash,
                    transfer_index=self._transfer_index(item),
                    recipient_address=self.config.deposit_address,
                    sender_address=str(item.get("source") or "").strip() or None,
                    amount_atomic=amount_atomic,
                    block_ref=str(item.get("transaction_lt") or "") or None,
                    block_timestamp=block_timestamp,
                    confirmations=1,
                    finality="finalized",
                )
            )
        return PollResult(
            transfers=transfers,
            next_cursor=str(newest_timestamp) if newest_timestamp is not None else cursor,
        )
