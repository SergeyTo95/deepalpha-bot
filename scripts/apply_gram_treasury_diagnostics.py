from pathlib import Path

path = Path("bot/admin.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    text = text.replace(old, new, 1)


insert_marker = "\ndef admin_gram_wallets_text(search_user_id: int | None = None) -> str:\n"
diagnostics_helper = r'''
def _get_cashier_payment_wallet_diagnostics() -> dict:
    """Return public-only Treasury row diagnostics; never fetch or expose secret material."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id,wallet_address,network,status,
                      CASE WHEN seed_encrypted IS NULL THEN 'watch-only' ELSE 'managed' END AS custody_mode
               FROM cashier_payment_wallets
               ORDER BY id ASC"""
        )
        rows = cur.fetchall() or []
        normalized = []
        for row in rows:
            normalized.append({
                "id": int(row[0]),
                "wallet_address": str(row[1] or ""),
                "network": str(row[2] or ""),
                "status": str(row[3] or ""),
                "custody_mode": str(row[4] or "unknown"),
            })
        return {"ok": True, "rows": normalized}
    except Exception as exc:
        logger.exception("GRAM_TREASURY_DIAGNOSTICS_FAILED")
        return {
            "ok": False,
            "error": "treasury_diagnostics_failed",
            "error_class": exc.__class__.__name__,
            "sqlstate": str(getattr(exc, "pgcode", "") or ""),
            "rows": [],
        }
    finally:
        conn.close()


def _format_cashier_payment_wallet_diagnostics() -> str:
    result = _get_cashier_payment_wallet_diagnostics()
    lines = ["🧾 Gram Treasury diagnostics", ""]
    if not result.get("ok"):
        lines.append(f"Status: FAILED ({result.get('error')})")
        lines.append(f"Error class: {result.get('error_class') or 'unknown'}")
        if result.get("sqlstate"):
            lines.append(f"SQLSTATE: {result.get('sqlstate')}")
        lines.append("No secret values are displayed.")
        return "\n".join(lines)

    rows = result.get("rows") or []
    admin_wallet = _get_admin_gram_wallet_summary()
    admin_address = str(admin_wallet.get("wallet_address") or "")
    active_count = sum(1 for row in rows if str(row.get("status") or "").lower() == "active")
    lines.append(f"Rows: {len(rows)} total / {active_count} active")
    if not rows:
        lines.append("No cashier/Treasury rows exist.")
    for row in rows:
        address = str(row.get("wallet_address") or "")
        marker = " ← admin address" if admin_address and address == admin_address else ""
        lines.append(
            f"#{row.get('id')} | {_mask_ton_admin(address)}{marker}\n"
            f"status={row.get('status') or 'unknown'} | network={row.get('network') or 'unknown'} | mode={row.get('custody_mode') or 'unknown'}"
        )
    lines.append("")
    lines.append("Public metadata only. Seed/private-key material is never queried or shown.")
    return "\n".join(lines)

'''
if insert_marker not in text:
    raise RuntimeError("admin_gram_wallets_text marker not found")
text = text.replace(insert_marker, "\n" + diagnostics_helper + "def admin_gram_wallets_text(search_user_id: int | None = None) -> str:\n", 1)

replace_once(
    '    kb.add(InlineKeyboardButton("🔍 Search by user_id", callback_data="admin_gram_wallets_search"))\n',
    '    kb.add(InlineKeyboardButton("🧾 Treasury diagnostics", callback_data="admin_gram_wallets_treasury_diag"))\n'
    '    kb.add(InlineKeyboardButton("🔍 Search by user_id", callback_data="admin_gram_wallets_search"))\n',
    "diagnostics button",
)

show_marker = '    @dp.callback_query_handler(lambda c: str(c.data or "").startswith("admin_gram_wallets_show:"))\n'
diag_handler = r'''    @dp.callback_query_handler(lambda c: str(c.data or "") == "admin_gram_wallets_treasury_diag")
    async def admin_gram_wallets_treasury_diag(callback: types.CallbackQuery):
        if not is_admin(callback.from_user.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        await callback.message.edit_text(
            _format_cashier_payment_wallet_diagnostics(),
            reply_markup=InlineKeyboardMarkup(row_width=1).add(
                InlineKeyboardButton("⬅️ Back to Gram Wallets", callback_data="admin_gram_wallets")
            ),
        )
        await callback.answer()

'''
if show_marker not in text:
    raise RuntimeError("show handler marker not found")
text = text.replace(show_marker, diag_handler + show_marker, 1)

replace_once(
    '    except Exception:\n        conn.rollback()\n        logger.exception("GRAM_ADMIN_TREASURY_CONFIGURE_FAILED")\n        return {"ok": False, "error": "treasury_configure_failed"}\n',
    '    except Exception as exc:\n'
    '        conn.rollback()\n'
    '        logger.exception("GRAM_ADMIN_TREASURY_CONFIGURE_FAILED")\n'
    '        return {\n'
    '            "ok": False,\n'
    '            "error": "treasury_configure_failed",\n'
    '            "error_class": exc.__class__.__name__,\n'
    '            "sqlstate": str(getattr(exc, "pgcode", "") or ""),\n'
    '        }\n',
    "setup exception diagnostics",
)

old_failure = '''        if not result.get("ok"):
            await callback.answer(f"Treasury not changed: {result.get('error')}", show_alert=True)
            await callback.message.edit_text(admin_gram_wallets_text(), reply_markup=admin_gram_wallets_kb())
            return
'''
new_failure = '''        if not result.get("ok"):
            error_code = str(result.get("error") or "unknown")
            await callback.answer(f"Treasury not changed: {error_code}", show_alert=True)
            diagnostic = f"⚠️ Last Treasury setup error: {error_code}"
            if result.get("error_class"):
                diagnostic += f"\\nError class: {result.get('error_class')}"
            if result.get("sqlstate"):
                diagnostic += f"\\nSQLSTATE: {result.get('sqlstate')}"
            diagnostic += "\\nNo secret values are displayed."
            await callback.message.edit_text(
                admin_gram_wallets_text() + "\\n\\n" + diagnostic,
                reply_markup=admin_gram_wallets_kb(),
            )
            return
'''
replace_once(old_failure, new_failure, "persistent setup error")

path.write_text(text, encoding="utf-8")
print("Applied Gram Treasury diagnostics codemod")
