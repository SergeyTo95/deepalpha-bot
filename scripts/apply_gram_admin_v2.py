from pathlib import Path

path = Path("bot/admin.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    text = text.replace(old, new, 1)


old_fetch = '''def _fetch_ton_wallet_address_incident_rows(wallet_address: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT id,user_id,wallet_address,status,last_balance_nano,created_at,seed_reveal_used
                       FROM user_ton_wallets
                       WHERE wallet_address=%s
                       ORDER BY id""", (str(wallet_address or "").strip(),))
        return cur.fetchall() if hasattr(cur, "fetchall") else []
    finally:
        conn.close()
'''

new_fetch = old_fetch + '''

def _get_admin_gram_wallet_summary() -> dict:
    """Resolve the configured Telegram admin's custodial Gram wallet without exposing secrets."""
    if ADMIN_ID <= 0:
        return {"ok": False, "error": "admin_id_not_configured", "rows": []}
    rows = _fetch_ton_wallet_incident_rows(ADMIN_ID)
    if not rows:
        return {"ok": False, "error": "admin_wallet_not_found", "rows": []}
    if len(rows) > 1:
        return {"ok": False, "error": "admin_wallet_conflict", "rows": rows}
    row = rows[0]
    status = str(row[4] or "unknown").lower()
    if status != "active":
        return {
            "ok": False,
            "error": "admin_wallet_not_active",
            "wallet_id": int(row[0]),
            "wallet_address": str(row[1] or ""),
            "network": str(row[2] or ""),
            "status": status,
            "rows": rows,
        }
    return {
        "ok": True,
        "wallet_id": int(row[0]),
        "wallet_address": str(row[1] or ""),
        "network": str(row[2] or ""),
        "status": status,
        "rows": rows,
    }


def _gram_treasury_mode(cashier: dict) -> str:
    if not cashier:
        return "not configured"
    # Never display the secret itself. Presence only tells the admin whether this row can sign.
    return "managed" if bool(cashier.get("seed_encrypted")) else "watch-only"


def _admin_gram_role_address(role: str) -> tuple[str, str]:
    role = str(role or "").strip().lower()
    if role == "admin":
        admin_wallet = _get_admin_gram_wallet_summary()
        return "Admin custodial Gram wallet", str(admin_wallet.get("wallet_address") or "")
    if role == "treasury":
        cashier = get_active_cashier_payment_wallet() or {}
        return "Treasury / payment Gram wallet", str(cashier.get("wallet_address") or "")
    if role == "referral":
        referral = get_active_referral_payout_wallet() or {}
        return "Referral payout Gram wallet", str(referral.get("wallet_address") or "")
    return "Gram wallet", ""


def _admin_set_watch_only_treasury_tx(user_id: int, wallet_id: int, admin_user_id: int) -> dict:
    """Promote one active admin custodial address to a watch-only Treasury.

    The seed from user_ton_wallets is intentionally never selected or copied. Existing
    signing-capable cashier rows are never overwritten. An already configured different
    Treasury must be changed through a separate controlled maintenance procedure.
    """
    if int(admin_user_id) != int(ADMIN_ID) or int(user_id) != int(ADMIN_ID):
        return {"ok": False, "error": "unauthorized"}

    from services.ton_chain_service import validate_ton_address

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.execute(
            """SELECT id,user_id,wallet_address,network,status
               FROM user_ton_wallets
               WHERE user_id=%s
               ORDER BY id ASC
               FOR UPDATE""",
            (int(user_id),),
        )
        rows = cur.fetchall() or []
        if not rows:
            conn.rollback(); return {"ok": False, "error": "admin_wallet_not_found"}
        if len(rows) != 1:
            conn.rollback(); return {"ok": False, "error": "admin_wallet_conflict"}
        row = rows[0]
        if int(row[0]) != int(wallet_id):
            conn.rollback(); return {"ok": False, "error": "wallet_selection_stale"}
        address = str(row[2] or "").strip()
        if str(row[4] or "").lower() != "active":
            conn.rollback(); return {"ok": False, "error": "admin_wallet_not_active"}
        if not validate_ton_address(address):
            conn.rollback(); return {"ok": False, "error": "invalid_gram_address"}

        network = str(row[3] or os.getenv("TON_NETWORK", "mainnet") or "mainnet").strip().lower()
        if "test" in network or network not in {"mainnet", "-239"}:
            conn.rollback(); return {"ok": False, "error": "treasury_requires_mainnet"}
        canonical_network = "mainnet"

        cur.execute(
            """SELECT id,wallet_address,status
               FROM cashier_payment_wallets
               WHERE status='active'
               ORDER BY id ASC
               FOR UPDATE"""
        )
        active = cur.fetchall() or []
        if len(active) > 1:
            conn.rollback(); return {"ok": False, "error": "treasury_conflict"}
        if active:
            active_address = str(active[0][1] or "").strip()
            if active_address == address:
                conn.rollback()
                return {"ok": True, "already_configured": True, "watch_only": True, "address": address}
            conn.rollback(); return {"ok": False, "error": "treasury_already_configured"}

        cur.execute(
            """SELECT id,status,seed_encrypted
               FROM cashier_payment_wallets
               WHERE wallet_address=%s
               ORDER BY id ASC
               FOR UPDATE""",
            (address,),
        )
        existing = cur.fetchall() or []
        if len(existing) > 1:
            conn.rollback(); return {"ok": False, "error": "treasury_address_conflict"}
        if existing:
            if existing[0][2]:
                conn.rollback(); return {"ok": False, "error": "managed_treasury_row_requires_manual_review"}
            cur.execute(
                """UPDATE cashier_payment_wallets
                   SET status='active',network=%s,created_by=%s,updated_at=NOW()
                   WHERE id=%s AND seed_encrypted IS NULL""",
                (canonical_network, int(admin_user_id), int(existing[0][0])),
            )
            if int(getattr(cur, "rowcount", 0) or 0) != 1:
                raise RuntimeError("watch_only_treasury_reactivate_failed")
        else:
            cur.execute(
                """INSERT INTO cashier_payment_wallets
                   (wallet_address,seed_encrypted,network,status,created_by,created_at,updated_at)
                   VALUES (%s,NULL,%s,'active',%s,NOW(),NOW())""",
                (address, canonical_network, int(admin_user_id)),
            )
            if int(getattr(cur, "rowcount", 1) or 0) != 1:
                raise RuntimeError("watch_only_treasury_insert_failed")
        conn.commit()
        logger.info("GRAM_ADMIN_TREASURY_CONFIGURED wallet=%s mode=watch_only", _mask_ton_admin(address))
        return {"ok": True, "watch_only": True, "address": address}
    except Exception:
        conn.rollback()
        logger.exception("GRAM_ADMIN_TREASURY_CONFIGURE_FAILED")
        return {"ok": False, "error": "treasury_configure_failed"}
    finally:
        conn.close()
'''
replace_once(old_fetch, new_fetch, "insert Gram admin Treasury helpers")

old_confirm_kb = '''def _admin_gram_wallets_confirm_kb(user_id: int, wallet_id: int, can_quarantine: bool = True) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    if can_quarantine:
        kb.add(InlineKeyboardButton("🟡 Quarantine this wallet", callback_data=f"admin_gram_wallets_quarantine_confirm:{int(user_id)}:{int(wallet_id)}"))
    kb.add(InlineKeyboardButton("✅ Choose as canonical", callback_data=f"admin_gram_wallets_canonical_confirm:{int(user_id)}:{int(wallet_id)}"))
    kb.add(InlineKeyboardButton("❌ Cancel", callback_data="admin_gram_wallets_cancel"))
    return kb
'''
new_confirm_kb = '''def _admin_gram_wallets_confirm_kb(user_id: int, wallet_id: int, can_quarantine: bool = True) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    if can_quarantine:
        kb.add(InlineKeyboardButton("🟡 Quarantine this wallet", callback_data=f"admin_gram_wallets_quarantine_confirm:{int(user_id)}:{int(wallet_id)}"))
    kb.add(InlineKeyboardButton("✅ Choose as canonical", callback_data=f"admin_gram_wallets_canonical_confirm:{int(user_id)}:{int(wallet_id)}"))
    if int(user_id) == int(ADMIN_ID):
        kb.add(InlineKeyboardButton("🛡 Set as watch-only Treasury", callback_data=f"admin_gram_wallets_treasury_prepare:{int(wallet_id)}"))
    kb.add(InlineKeyboardButton("❌ Cancel", callback_data="admin_gram_wallets_cancel"))
    return kb
'''
replace_once(old_confirm_kb, new_confirm_kb, "extend wallet action keyboard")

start = text.index("def admin_gram_wallets_text(search_user_id: int | None = None) -> str:\n")
end = text.index("\ndef _admin_lang(user_id: int) -> str:\n", start)
old_panel = text[start:end]
new_panel = '''def admin_gram_wallets_text(search_user_id: int | None = None) -> str:
    status = get_ton_wallet_runtime_status()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(CASE WHEN status='active' THEN 1 ELSE 0 END),0) FROM user_ton_wallets")
        counts = cur.fetchone() or (0, 0)
        found_rows = _fetch_ton_wallet_incident_rows(int(search_user_id)) if search_user_id else []
    finally:
        conn.close()

    admin_wallet = _get_admin_gram_wallet_summary()
    cashier = get_active_cashier_payment_wallet() or {}
    referral = get_active_referral_payout_wallet() or {}
    web_enabled = str(get_setting("web_ton_enabled", "off") or "off").lower() == "on"

    admin_address = str(admin_wallet.get("wallet_address") or "")
    cashier_address = str(cashier.get("wallet_address") or "")
    referral_address = str(referral.get("wallet_address") or "")
    treasury_mode = _gram_treasury_mode(cashier)
    if cashier_address:
        gram_usdt_status = "READY FOR WORKER LINK"
    elif admin_wallet.get("ok"):
        gram_usdt_status = "WAITING: set admin wallet as Treasury"
    else:
        gram_usdt_status = "WAITING: resolve admin wallet"

    lines = [
        "💎 Gram Wallets", "",
        "💼 Payment routing",
        f"👤 Admin custodial: {_mask_ton_admin(admin_address)} [{admin_wallet.get('status') or admin_wallet.get('error') or 'not configured'}]",
        f"🏦 Treasury / payments: {_mask_ton_admin(cashier_address)} [{cashier.get('status') or 'not configured'}]",
        f"   Mode: {treasury_mode}",
        f"💸 Referral payout: {_mask_ton_admin(referral_address)} [{referral.get('status') or 'not configured'}]",
        f"🪙 USDT on Gram: {gram_usdt_status}",
        "",
        f"👥 Custodial wallets: {int(counts[0] or 0)} total / {int(counts[1] or 0)} active",
        "",
        "⚙️ Runtime (technical)",
        f"TON_WALLET_ENABLED: {'ON' if status.get('enabled') else 'OFF'}",
        f"web_ton_enabled: {'ON' if web_enabled else 'OFF'}",
        f"Network: {status.get('network')}",
        f"Read/Create/Refresh/Send/Seed export: "
        f"{'yes' if status.get('can_read_existing') else 'no'}/"
        f"{'yes' if status.get('can_create') else 'no'}/"
        f"{'yes' if status.get('can_refresh_balance') else 'no'}/"
        f"{'yes' if status.get('can_send') else 'no'}/"
        f"{'yes' if status.get('can_export_seed') else 'no'}",
        f"tonsdk: {'ready' if status.get('tonsdk_ready') else 'missing'}",
        f"MASTER_ENCRYPTION_KEY: {'ready' if status.get('master_encryption_key_ready') else 'missing'}",
        f"Toncenter: {('available' if (status.get('toncenter') or {}).get('endpoint_available') else 'missing')} / API key: {('set' if (status.get('toncenter') or {}).get('api_key_configured') else 'not set')}",
    ]
    if search_user_id:
        lines += ["", f"🔎 Wallet search user_id: {search_user_id}"]
        if found_rows:
            lines.append("Safe wallet rows (secret fields omitted):")
            for r in found_rows:
                lines.append(f"id={r[0]} address={_mask_ton_admin(r[1])} status={r[4] or 'unknown'} balance={r[5] or 0} created_at={r[6] or '—'} seed_reveal_used={bool(r[7])}")
            if len(found_rows) > 1:
                lines.append("⚠️ wallet_conflict: resolve duplicates before Treasury assignment.")
        else:
            lines.append("Wallet: not found")
    return "\\n".join(lines)


def admin_gram_wallets_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    web_enabled = str(get_setting("web_ton_enabled", "off") or "off").lower() == "on"
    admin_wallet = _get_admin_gram_wallet_summary()
    cashier = get_active_cashier_payment_wallet() or {}
    referral = get_active_referral_payout_wallet() or {}

    if admin_wallet.get("wallet_address"):
        kb.add(InlineKeyboardButton("👤 Show admin Gram address", callback_data="admin_gram_wallets_show:admin"))
    if cashier.get("wallet_address"):
        kb.add(InlineKeyboardButton("🏦 Show Treasury address", callback_data="admin_gram_wallets_show:treasury"))
    if referral.get("wallet_address"):
        kb.add(InlineKeyboardButton("💸 Show referral payout address", callback_data="admin_gram_wallets_show:referral"))

    admin_address = str(admin_wallet.get("wallet_address") or "")
    cashier_address = str(cashier.get("wallet_address") or "")
    if admin_wallet.get("ok") and not cashier_address:
        kb.add(InlineKeyboardButton("🛡 Set admin wallet as watch-only Treasury", callback_data=f"admin_gram_wallets_treasury_prepare:{int(admin_wallet['wallet_id'])}"))
    elif admin_wallet.get("ok") and cashier_address == admin_address:
        kb.add(InlineKeyboardButton("✅ Admin wallet = Treasury", callback_data="admin_gram_wallets_noop"))

    kb.add(InlineKeyboardButton("🔍 Search by user_id", callback_data="admin_gram_wallets_search"))
    kb.add(InlineKeyboardButton("🔎 Search by wallet address", callback_data="admin_gram_wallets_search_address"))
    kb.add(InlineKeyboardButton(("🛑 Disable WebApp wallets" if web_enabled else "✅ Enable WebApp wallets"), callback_data="admin_gram_wallets_toggle_web"))
    kb.add(InlineKeyboardButton("🔄 Refresh", callback_data="admin_gram_wallets"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_back"))
    return kb
'''
text = text[:start] + new_panel + text[end:]

anchor = '''    @dp.callback_query_handler(lambda c: c.data == "admin_gram_wallets_toggle_web")
    async def admin_gram_wallets_toggle_web(callback: types.CallbackQuery):
'''
if text.count(anchor) != 1:
    raise RuntimeError("Gram handler anchor not found exactly once")

extra_handlers = '''    @dp.callback_query_handler(lambda c: c.data == "admin_gram_wallets_noop")
    async def admin_gram_wallets_noop(callback: types.CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        await callback.answer("Already configured")

    @dp.callback_query_handler(lambda c: str(c.data or "").startswith("admin_gram_wallets_show:"))
    async def admin_gram_wallets_show(callback: types.CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        role = str(callback.data or "").split(":", 1)[1] if ":" in str(callback.data or "") else ""
        title, address = _admin_gram_role_address(role)
        if not address:
            await callback.answer("Wallet is not configured", show_alert=True)
            return
        await callback.message.answer(
            f"{title}\\n\\n{address}\\n\\nPublic address only. Long-press it to copy."
        )
        await callback.answer()

    @dp.callback_query_handler(lambda c: str(c.data or "").startswith("admin_gram_wallets_treasury_prepare:"))
    async def admin_gram_wallets_treasury_prepare(callback: types.CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        parts = str(callback.data or "").split(":")
        if len(parts) != 2 or not parts[1].isdigit():
            await callback.answer("Invalid wallet action", show_alert=True)
            return
        wallet_id = int(parts[1])
        admin_wallet = _get_admin_gram_wallet_summary()
        if not admin_wallet.get("ok"):
            await callback.answer(f"Cannot use admin wallet: {admin_wallet.get('error')}", show_alert=True)
            return
        if int(admin_wallet.get("wallet_id") or 0) != wallet_id:
            await callback.answer("Wallet selection is stale. Refresh the panel.", show_alert=True)
            return
        cashier = get_active_cashier_payment_wallet() or {}
        cashier_address = str(cashier.get("wallet_address") or "")
        selected_address = str(admin_wallet.get("wallet_address") or "")
        if cashier_address and cashier_address != selected_address:
            await callback.answer("A different Treasury is already active. Automatic replacement is blocked.", show_alert=True)
            return
        if cashier_address == selected_address:
            await callback.answer("This admin wallet is already the Treasury.", show_alert=True)
            return
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("✅ Confirm watch-only Treasury", callback_data=f"admin_gram_wallets_treasury_confirm:{wallet_id}"))
        kb.add(InlineKeyboardButton("❌ Cancel", callback_data="admin_gram_wallets_cancel"))
        await callback.message.edit_text(
            "🛡 Set admin Gram wallet as Treasury?\\n\\n"
            f"Address:\\n{selected_address}\\n\\n"
            "This creates a WATCH-ONLY Treasury record.\\n"
            "The custodial seed is NOT copied.\\n"
            "Outgoing transfers are NOT enabled by this action.\\n"
            "Existing different Treasury addresses are never replaced automatically.",
            reply_markup=kb,
        )
        await callback.answer()

    @dp.callback_query_handler(lambda c: str(c.data or "").startswith("admin_gram_wallets_treasury_confirm:"))
    async def admin_gram_wallets_treasury_confirm(callback: types.CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        parts = str(callback.data or "").split(":")
        if len(parts) != 2 or not parts[1].isdigit():
            await callback.answer("Invalid wallet action", show_alert=True)
            return
        result = _admin_set_watch_only_treasury_tx(ADMIN_ID, int(parts[1]), callback.from_user.id)
        if not result.get("ok"):
            await callback.answer(f"Treasury not changed: {result.get('error')}", show_alert=True)
            await callback.message.edit_text(admin_gram_wallets_text(), reply_markup=admin_gram_wallets_kb())
            return
        await callback.answer("✅ Watch-only Treasury configured", show_alert=True)
        await callback.message.edit_text(admin_gram_wallets_text(), reply_markup=admin_gram_wallets_kb())

'''
text = text.replace(anchor, extra_handlers + anchor, 1)

path.write_text(text, encoding="utf-8")
print("Gram admin v2 codemod applied")
