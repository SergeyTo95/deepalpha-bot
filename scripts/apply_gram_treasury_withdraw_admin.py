from pathlib import Path

path = Path("bot/admin.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 anchor, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    "from services.ton_wallet_service import get_ton_wallet_runtime_status, get_user_ton_wallet\n",
    "from services.ton_wallet_service import get_ton_wallet_runtime_status, get_user_ton_wallet, get_ton_tx_explorer_url\n"
    "from services.ton_chain_service import normalize_ton_address, validate_ton_address\n"
    "from services.gram_treasury_withdrawal_service import (\n"
    "    CANONICAL_USDT_MASTER, cancel_treasury_withdrawal, confirm_treasury_withdrawal,\n"
    "    get_recent_treasury_withdrawals, get_treasury_withdraw_snapshot,\n"
    "    gram_to_raw, prepare_treasury_withdrawal, usdt_to_raw,\n"
    ")\n",
    "imports",
)

replace_once(
    "class ReferralRewardsAdminStates(StatesGroup):\n"
    "    waiting_reward_percent = State()\n"
    "    waiting_unlock_hours = State()\n"
    "    waiting_min_withdrawal_ton = State()\n"
    "    waiting_daily_cap_ton = State()\n\n\n"
    "def is_admin(user_id):\n",
    "class ReferralRewardsAdminStates(StatesGroup):\n"
    "    waiting_reward_percent = State()\n"
    "    waiting_unlock_hours = State()\n"
    "    waiting_min_withdrawal_ton = State()\n"
    "    waiting_daily_cap_ton = State()\n\n\n"
    "class GramTreasuryWithdrawStates(StatesGroup):\n"
    "    waiting_destination = State()\n"
    "    waiting_amount = State()\n"
    "    waiting_memo = State()\n\n\n"
    "def is_admin(user_id):\n",
    "states",
)

replace_once(
    "    if referral.get(\"wallet_address\"):\n"
    "        kb.add(InlineKeyboardButton(\"💸 Show referral payout address\", callback_data=\"admin_gram_wallets_show:referral\"))\n\n"
    "    admin_address = str(admin_wallet.get(\"wallet_address\") or \"\")\n",
    "    if referral.get(\"wallet_address\"):\n"
    "        kb.add(InlineKeyboardButton(\"💸 Show referral payout address\", callback_data=\"admin_gram_wallets_show:referral\"))\n"
    "    if cashier.get(\"wallet_address\"):\n"
    "        kb.add(InlineKeyboardButton(\"💸 Treasury Withdraw\", callback_data=\"admin_gram_treasury_withdraw\"))\n"
    "        kb.add(InlineKeyboardButton(\"🧾 Treasury withdrawals\", callback_data=\"admin_gram_treasury_withdrawals\"))\n\n"
    "    admin_address = str(admin_wallet.get(\"wallet_address\") or \"\")\n",
    "wallet keyboard",
)

helpers = r'''

def _gram_treasury_withdraw_error_text(error: str) -> str:
    code = str(error or "withdraw_failed")
    messages = {
        "treasury_not_configured": "Treasury is not configured.",
        "treasury_signing_source_mismatch": "Treasury address does not match the admin custodial Gram wallet. Withdrawal blocked.",
        "admin_wallet_conflict": "Admin Gram wallet conflict detected. Withdrawal blocked.",
        "admin_wallet_not_found": "Admin custodial Gram wallet was not found.",
        "admin_wallet_not_active": "Admin custodial Gram wallet is not active.",
        "treasury_requires_mainnet": "Treasury withdrawal requires Gram mainnet.",
        "treasury_runtime_not_ready": "Treasury signing runtime is not ready.",
        "treasury_withdraw_disabled": "Treasury withdrawals are OFF in Railway.",
        "usdt_withdraw_disabled": "USDT withdrawals are OFF in Railway.",
        "invalid_destination": "Invalid Gram destination address.",
        "destination_is_treasury": "Destination is the Treasury itself.",
        "invalid_amount": "Invalid amount.",
        "too_many_decimals": "Too many decimal places.",
        "memo_too_long": "Memo/tag is too long (max 120 UTF-8 bytes).",
        "insufficient_gram_balance": "Insufficient Gram balance after fee reserve.",
        "insufficient_usdt_balance": "Insufficient USDT balance.",
        "insufficient_gram_for_usdt_gas": "Not enough Gram to pay USDT Jetton gas.",
        "usdt_wallet_not_deployed": "Treasury USDT Jetton wallet is not deployed / has no indexed balance yet.",
        "usdt_balance_unavailable": "USDT balance is temporarily unavailable.",
        "gram_balance_unavailable": "Gram balance is temporarily unavailable.",
        "withdrawal_expired": "Withdrawal preview expired. Create a new one.",
        "withdrawal_already_processed": "This withdrawal was already processed. No retry was made.",
        "withdrawal_race_blocked": "Duplicate confirmation was blocked.",
        "wallet_selection_stale": "Admin wallet changed since preview. Withdrawal blocked.",
        "signing_seed_unavailable": "Encrypted signing seed is unavailable for the admin custodial wallet.",
        "wallet_mismatch": "Signing seed does not derive the Treasury address. Withdrawal blocked.",
        "seqno_unavailable": "Gram wallet seqno is unavailable. Nothing was broadcast.",
        "signing_failed": "Transaction signing failed. Nothing was broadcast.",
        "submission_uncertain": "Network submission result is uncertain. DO NOT retry automatically; inspect the chain / recent withdrawals first.",
        "usdt_master_mismatch": "USDT master mismatch. Withdrawal blocked.",
    }
    return messages.get(code, f"Withdrawal blocked: {code}")


def _gram_treasury_withdraw_snapshot_text(admin_user_id: int) -> tuple[str, dict]:
    snapshot = get_treasury_withdraw_snapshot(admin_user_id)
    if not snapshot.get("ok"):
        return (
            "💸 Gram Treasury Withdraw\n\n"
            f"❌ {_gram_treasury_withdraw_error_text(snapshot.get('error'))}\n\n"
            f"Withdraw gate: {'ON' if snapshot.get('withdraw_enabled') else 'OFF'}\n"
            f"USDT gate: {'ON' if snapshot.get('usdt_withdraw_enabled') else 'OFF'}",
            snapshot,
        )
    usdt = snapshot.get("usdt") or {}
    usdt_balance = usdt.get("balance_display") if usdt.get("ok") else "—"
    usdt_note = "" if usdt.get("ok") else f" ({usdt.get('error') or 'unavailable'})"
    text = (
        "💸 Gram Treasury Withdraw\n\n"
        f"🏦 Treasury: {_mask_ton_admin(snapshot.get('source_address'))}\n"
        f"💎 Gram balance: {snapshot.get('gram_balance_display', '—')}\n"
        f"🪙 USDT balance: {usdt_balance}{usdt_note}\n\n"
        f"Network: mainnet\n"
        f"Signing runtime: {'READY' if snapshot.get('runtime_ready') else 'NOT READY'}\n"
        f"Withdraw gate: {'ON' if snapshot.get('withdraw_enabled') else 'OFF'}\n"
        f"USDT gate: {'ON' if snapshot.get('usdt_withdraw_enabled') else 'OFF'}\n\n"
        "USDT on Gram\n"
        f"Master: {CANONICAL_USDT_MASTER}\n"
        "Decimals: 6\n\n"
        "Every withdrawal requires destination → amount → optional memo/tag → exact preview → separate confirmation."
    )
    return text, snapshot


def _gram_treasury_withdraw_kb(snapshot: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    if snapshot.get("ok") and snapshot.get("runtime_ready") and snapshot.get("withdraw_enabled"):
        kb.add(InlineKeyboardButton("💎 Withdraw Gram", callback_data="admin_gram_treasury_asset:gram"))
        if snapshot.get("usdt_withdraw_enabled"):
            kb.add(InlineKeyboardButton("🪙 Withdraw USDT", callback_data="admin_gram_treasury_asset:usdt"))
    kb.add(InlineKeyboardButton("🧾 Recent withdrawals", callback_data="admin_gram_treasury_withdrawals"))
    kb.add(InlineKeyboardButton("⬅️ Back to Gram Wallets", callback_data="admin_gram_wallets"))
    return kb


def _gram_treasury_recent_text(admin_user_id: int) -> str:
    rows = get_recent_treasury_withdrawals(admin_user_id, limit=10)
    if not rows:
        return "🧾 Treasury withdrawals\n\nNo withdrawal records yet."
    lines = ["🧾 Treasury withdrawals", ""]
    for item in rows:
        asset = "USDT" if item.get("asset") == "usdt" else "Gram"
        dest = _mask_ton_admin(item.get("destination_address"))
        tx = str(item.get("tx_hash") or "")
        tx_short = (tx[:10] + "…" + tx[-8:]) if len(tx) > 20 else (tx or "—")
        lines.extend([
            f"{item.get('reference')} | {item.get('status')}",
            f"{item.get('amount_display')} {asset} → {dest}",
            f"tx: {tx_short}",
        ])
        if item.get("error_code"):
            lines.append(f"error: {item.get('error_code')}")
        lines.append("")
    return "\n".join(lines).strip()

'''
replace_once(
    "def _admin_lang(user_id: int) -> str:\n",
    helpers + "def _admin_lang(user_id: int) -> str:\n",
    "helper insertion",
)

handlers = r'''
    @dp.callback_query_handler(lambda c: c.data == "admin_gram_treasury_withdraw")
    async def admin_gram_treasury_withdraw(callback: types.CallbackQuery, state: FSMContext):
        if not is_admin(callback.from_user.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        await state.finish()
        text_value, snapshot = _gram_treasury_withdraw_snapshot_text(callback.from_user.id)
        await callback.message.edit_text(text_value, reply_markup=_gram_treasury_withdraw_kb(snapshot))
        await callback.answer()

    @dp.callback_query_handler(lambda c: c.data == "admin_gram_treasury_withdrawals")
    async def admin_gram_treasury_withdrawals(callback: types.CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("💸 New withdrawal", callback_data="admin_gram_treasury_withdraw"))
        kb.add(InlineKeyboardButton("⬅️ Back to Gram Wallets", callback_data="admin_gram_wallets"))
        await callback.message.edit_text(_gram_treasury_recent_text(callback.from_user.id), reply_markup=kb)
        await callback.answer()

    @dp.callback_query_handler(lambda c: str(c.data or "").startswith("admin_gram_treasury_asset:"))
    async def admin_gram_treasury_asset(callback: types.CallbackQuery, state: FSMContext):
        if not is_admin(callback.from_user.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        asset = str(callback.data or "").split(":", 1)[1].strip().lower()
        if asset not in {"gram", "usdt"}:
            await callback.answer("Unsupported asset", show_alert=True)
            return
        snapshot = get_treasury_withdraw_snapshot(callback.from_user.id)
        if not snapshot.get("ok") or not snapshot.get("runtime_ready") or not snapshot.get("withdraw_enabled"):
            await callback.answer(_gram_treasury_withdraw_error_text(snapshot.get("error") or "treasury_withdraw_disabled"), show_alert=True)
            return
        if asset == "usdt" and not snapshot.get("usdt_withdraw_enabled"):
            await callback.answer(_gram_treasury_withdraw_error_text("usdt_withdraw_disabled"), show_alert=True)
            return
        await state.finish()
        await state.update_data(gram_treasury_asset=asset)
        await GramTreasuryWithdrawStates.waiting_destination.set()
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("❌ Cancel", callback_data="admin_gram_treasury_flow_cancel"))
        label = "USDT" if asset == "usdt" else "Gram"
        await callback.message.edit_text(
            f"💸 Withdraw {label}\n\nSend the destination Gram address (UQ… / EQ…).\n\nFor exchange deposits, use the exact network/address shown by the exchange.",
            reply_markup=kb,
        )
        await callback.answer()

    @dp.message_handler(state=GramTreasuryWithdrawStates.waiting_destination)
    async def admin_gram_treasury_destination(message: types.Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            await state.finish()
            return
        destination = normalize_ton_address(str(message.text or "").strip())
        if not validate_ton_address(destination):
            await message.answer("❌ Invalid Gram address. Send a valid UQ… / EQ… address, or press Cancel.")
            return
        await state.update_data(gram_treasury_destination=destination)
        await GramTreasuryWithdrawStates.waiting_amount.set()
        data = await state.get_data()
        label = "USDT" if data.get("gram_treasury_asset") == "usdt" else "Gram"
        await message.answer(f"Amount to withdraw in {label}? Use a decimal string, for example: 12.5")

    @dp.message_handler(state=GramTreasuryWithdrawStates.waiting_amount)
    async def admin_gram_treasury_amount(message: types.Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            await state.finish()
            return
        data = await state.get_data()
        asset = str(data.get("gram_treasury_asset") or "")
        amount_text = str(message.text or "").strip()
        try:
            if asset == "usdt":
                usdt_to_raw(amount_text)
            else:
                gram_to_raw(amount_text)
        except Exception as exc:
            await message.answer(f"❌ {_gram_treasury_withdraw_error_text(str(exc))}\nSend the amount again.")
            return
        await state.update_data(gram_treasury_amount=amount_text)
        await GramTreasuryWithdrawStates.waiting_memo.set()
        await message.answer(
            "Optional memo/tag?\n\n"
            "If the destination (for example an exchange) requires a memo/tag, paste it exactly.\n"
            "Send - for no memo."
        )

    @dp.message_handler(state=GramTreasuryWithdrawStates.waiting_memo)
    async def admin_gram_treasury_memo(message: types.Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            await state.finish()
            return
        data = await state.get_data()
        memo_input = str(message.text or "").strip()
        memo = "" if memo_input in {"-", "—", "skip", "Skip", "нет", "Нет"} else memo_input
        result = prepare_treasury_withdrawal(
            admin_user_id=message.from_user.id,
            asset=str(data.get("gram_treasury_asset") or ""),
            destination_address=str(data.get("gram_treasury_destination") or ""),
            amount_text=str(data.get("gram_treasury_amount") or ""),
            memo=memo,
        )
        await state.finish()
        if not result.get("ok"):
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("💸 Back to Treasury Withdraw", callback_data="admin_gram_treasury_withdraw"))
            await message.answer(f"❌ {_gram_treasury_withdraw_error_text(result.get('error'))}", reply_markup=kb)
            return
        asset_label = "USDT" if result.get("asset") == "usdt" else "Gram"
        extra = ""
        if result.get("asset") == "usdt":
            extra = (
                f"\nUSDT master: {CANONICAL_USDT_MASTER}\n"
                "Jetton gas envelope: 0.1 Gram; 0.02 Gram is forwarded for transfer_notification.\n"
            )
        memo_label = result.get("memo") or "—"
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("✅ CONFIRM WITHDRAWAL", callback_data=f"admin_gram_treasury_confirm:{result['reference']}"))
        kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"admin_gram_treasury_cancel:{result['reference']}"))
        await message.answer(
            "⚠️ Treasury withdrawal preview\n\n"
            f"Reference: {result['reference']}\n"
            f"Asset: {asset_label}\n"
            f"Amount: {result['amount_display']} {asset_label}\n"
            f"From: {result['source_address']}\n"
            f"To: {result['destination_address']}\n"
            f"Memo/tag: {memo_label}\n"
            f"{extra}\n"
            "Preview expires in 10 minutes.\n"
            "Confirmation is single-use. A duplicate tap is blocked in the database.\n"
            "If network submission becomes uncertain, the system will NOT automatically retry.",
            reply_markup=kb,
        )

    @dp.callback_query_handler(lambda c: str(c.data or "").startswith("admin_gram_treasury_confirm:"))
    async def admin_gram_treasury_confirm(callback: types.CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        reference = str(callback.data or "").split(":", 1)[1].strip()
        if not reference.startswith("GTW_") or len(reference) > 32:
            await callback.answer("Invalid withdrawal reference", show_alert=True)
            return
        await callback.answer("Submitting…")
        await callback.message.edit_text(
            f"⏳ Submitting Treasury withdrawal\n\nReference: {reference}\n\nDo not submit another withdrawal until this result is known."
        )
        result = confirm_treasury_withdrawal(callback.from_user.id, reference)
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("🧾 Recent withdrawals", callback_data="admin_gram_treasury_withdrawals"))
        kb.add(InlineKeyboardButton("💸 Treasury Withdraw", callback_data="admin_gram_treasury_withdraw"))
        if result.get("ok"):
            asset_label = "USDT" if result.get("asset") == "usdt" else "Gram"
            tx_hash = str(result.get("tx_hash") or "")
            explorer = get_ton_tx_explorer_url(tx_hash, network="mainnet") if tx_hash else ""
            tx_line = tx_hash or "pending hash resolution"
            explorer_line = f"\nExplorer: {explorer}" if explorer else ""
            status_note = "" if result.get("status") == "submitted" else "\n⚠️ Broadcast accepted, but tx hash was not resolved yet. Do NOT resend automatically."
            await callback.message.edit_text(
                "✅ Treasury withdrawal submitted\n\n"
                f"Reference: {reference}\n"
                f"Amount: {result.get('amount_display')} {asset_label}\n"
                f"Destination: {result.get('destination_address')}\n"
                f"Status: {result.get('status')}\n"
                f"Tx: {tx_line}{explorer_line}{status_note}",
                reply_markup=kb,
            )
            return
        error = str(result.get("error") or "withdraw_failed")
        warning = "\n\n🚨 DO NOT RETRY. Check the chain / Recent withdrawals first." if result.get("status") == "submission_uncertain" else ""
        await callback.message.edit_text(
            "❌ Treasury withdrawal not completed\n\n"
            f"Reference: {reference}\n"
            f"Status: {result.get('status') or 'failed'}\n"
            f"{_gram_treasury_withdraw_error_text(error)}{warning}",
            reply_markup=kb,
        )

    @dp.callback_query_handler(lambda c: str(c.data or "").startswith("admin_gram_treasury_cancel:"))
    async def admin_gram_treasury_cancel(callback: types.CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        reference = str(callback.data or "").split(":", 1)[1].strip()
        result = cancel_treasury_withdrawal(callback.from_user.id, reference)
        await callback.message.edit_text(
            f"❌ Treasury withdrawal {reference}: {result.get('status')}",
            reply_markup=InlineKeyboardMarkup(row_width=1).add(
                InlineKeyboardButton("💸 Treasury Withdraw", callback_data="admin_gram_treasury_withdraw")
            ),
        )
        await callback.answer()

    @dp.callback_query_handler(lambda c: c.data == "admin_gram_treasury_flow_cancel", state="*")
    async def admin_gram_treasury_flow_cancel(callback: types.CallbackQuery, state: FSMContext):
        if not is_admin(callback.from_user.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        await state.finish()
        text_value, snapshot = _gram_treasury_withdraw_snapshot_text(callback.from_user.id)
        await callback.message.edit_text(text_value, reply_markup=_gram_treasury_withdraw_kb(snapshot))
        await callback.answer("Cancelled")

'''
replace_once(
    '    @dp.callback_query_handler(lambda c: str(c.data or "").startswith("admin_gram_wallets_treasury_prepare:"))\n',
    handlers + '    @dp.callback_query_handler(lambda c: str(c.data or "").startswith("admin_gram_wallets_treasury_prepare:"))\n',
    "handler insertion",
)

path.write_text(text, encoding="utf-8")
print("Gram Treasury Withdraw admin flow applied")
