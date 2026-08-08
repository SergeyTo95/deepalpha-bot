from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import requests

from services.payments.chains.base import PollResult, WatchOnlyUSDTAdapter
from services.payments.models import ObservedTransfer


class SolanaUSDTAdapter(WatchOnlyUSDTAdapter):
    live_polling_implemented = True

    def _rpc(self, method: str, params: List[Any]) -> Any:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        response = requests.post(
            self.config.rpc_url,
            json={"jsonrpc": "2.0", "id": "velia-payment", "method": method, "params": params},
            headers=headers,
            timeout=25,
        )
        if response.status_code != 200:
            raise RuntimeError(f"solana_http_{response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("error"):
            raise RuntimeError("solana_rpc_error")
        return payload.get("result")

    @staticmethod
    def _owner_mint_amount(entries: Any, *, owner: str, mint: str) -> int:
        total = 0
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("owner") or "") != owner or str(entry.get("mint") or "") != mint:
                continue
            ui = entry.get("uiTokenAmount") or {}
            try:
                total += int(str(ui.get("amount") or "0"))
            except (TypeError, ValueError, OverflowError):
                continue
        return total

    def _poll_sync(self, cursor: Optional[str]) -> PollResult:
        options: Dict[str, Any] = {"limit": 50, "commitment": "finalized"}
        if cursor:
            options["until"] = cursor
        signatures = self._rpc(
            "getSignaturesForAddress",
            [self.config.deposit_address, options],
        ) or []
        if not isinstance(signatures, list):
            raise RuntimeError("solana_invalid_signature_response")

        transfers = []
        max_slot = None
        for item in reversed(signatures):
            if not isinstance(item, dict) or item.get("err") is not None:
                continue
            signature = str(item.get("signature") or "").strip()
            if not signature:
                continue
            transaction = self._rpc(
                "getTransaction",
                [
                    signature,
                    {
                        "encoding": "jsonParsed",
                        "commitment": "finalized",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            )
            if not isinstance(transaction, dict):
                continue
            meta = transaction.get("meta") or {}
            if not isinstance(meta, dict) or meta.get("err") is not None:
                continue
            pre_amount = self._owner_mint_amount(
                meta.get("preTokenBalances"),
                owner=self.config.deposit_address,
                mint=self.config.canonical_asset_identifier,
            )
            post_amount = self._owner_mint_amount(
                meta.get("postTokenBalances"),
                owner=self.config.deposit_address,
                mint=self.config.canonical_asset_identifier,
            )
            delta = post_amount - pre_amount
            if delta <= 0:
                continue
            try:
                block_time = int(transaction.get("blockTime") or item.get("blockTime") or 0)
                slot = int(transaction.get("slot") or item.get("slot") or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if block_time <= 0:
                continue
            max_slot = slot if max_slot is None else max(max_slot, slot)
            transfers.append(
                ObservedTransfer(
                    network="solana",
                    asset="USDT",
                    tx_hash=signature,
                    transfer_index=0,
                    recipient_address=self.config.deposit_address,
                    sender_address=None,
                    amount_atomic=delta,
                    block_ref=str(slot) if slot else None,
                    block_timestamp=block_time,
                    confirmations=1,
                    finality="finalized",
                )
            )
        next_cursor = cursor
        if signatures:
            newest = signatures[0] if isinstance(signatures[0], dict) else {}
            next_cursor = str(newest.get("signature") or cursor or "") or None
        return PollResult(
            transfers=transfers,
            next_cursor=next_cursor,
            chain_height=max_slot,
        )

    async def poll(self, cursor: Optional[str]) -> PollResult:
        return await asyncio.to_thread(self._poll_sync, cursor)
