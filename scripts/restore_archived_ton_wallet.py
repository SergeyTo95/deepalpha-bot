#!/usr/bin/env python3
"""Restore one archived TON wallet record to production.

Safe usage:
  python scripts/restore_archived_ton_wallet.py --archive-id 123 --confirm RESTORE_ARCHIVED_TON_WALLET --dry-run
  python scripts/restore_archived_ton_wallet.py --archive-id 123 --confirm RESTORE_ARCHIVED_TON_WALLET --restored-by incident-2026-07-19

This maintenance tool is intentionally not connected to Telegram or WebApp. It
never decrypts or prints seed_encrypted/public_key/private material.
"""
import argparse
from datetime import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.database import get_connection  # noqa: E402

CONFIRM = "RESTORE_ARCHIVED_TON_WALLET"


def restore_archive_record(archive_id: int, restored_by: str, dry_run: bool = False) -> dict:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.execute(
            """SELECT id,original_wallet_id,user_id,wallet_address,network,wallet_version,public_key,seed_encrypted,seed_revealed_at,seed_reveal_used,status,created_at,updated_at,last_balance_nano,last_balance_checked_at,restore_status
               FROM user_ton_wallet_quarantine_archive
               WHERE id=%s
               FOR UPDATE""",
            (int(archive_id),),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback(); return {"ok": False, "error": "archive_not_found"}
        if str(row[15] or "archived") == "restored":
            conn.rollback(); return {"ok": False, "error": "already_restored"}
        cur.execute("SELECT id,user_id FROM user_ton_wallets WHERE user_id=%s OR wallet_address=%s FOR UPDATE", (int(row[2]), str(row[3])))
        conflicts = cur.fetchall()
        if conflicts:
            conn.rollback(); return {"ok": False, "error": "production_conflict", "conflict_count": len(conflicts)}
        if dry_run:
            conn.rollback(); return {"ok": True, "dry_run": True, "archive_id": int(archive_id), "user_id": int(row[2]), "wallet_address": str(row[3])}
        now = datetime.utcnow().isoformat()
        cur.execute(
            """INSERT INTO user_ton_wallets
               (user_id,network,wallet_address,wallet_version,public_key,seed_encrypted,seed_revealed_at,seed_reveal_used,status,created_at,updated_at,last_balance_nano,last_balance_checked_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (row[2], row[4], row[3], row[5], row[6], row[7], row[8], row[9], row[10] or "active", row[11], now, row[13], row[14]),
        )
        if int(getattr(cur, "rowcount", 0) or 0) != 1:
            raise RuntimeError("restore_insert_failed")
        cur.execute(
            """INSERT INTO user_ton_wallet_quarantine_audit
               (original_wallet_id,user_id,wallet_address,network,wallet_version,status,last_balance_nano,seed_reveal_used,original_created_at,action,canonical_wallet_id,admin_user_id,created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (row[1], row[2], row[3], row[4], row[5], row[10], row[13], row[9], row[11], "restore", None, None, now),
        )
        if int(getattr(cur, "rowcount", 0) or 0) != 1:
            raise RuntimeError("restore_audit_insert_failed")
        cur.execute("UPDATE user_ton_wallet_quarantine_archive SET restored_at=%s,restored_by=%s,restore_status='restored' WHERE id=%s", (now, str(restored_by or "manual"), int(archive_id)))
        if int(getattr(cur, "rowcount", 0) or 0) != 1:
            raise RuntimeError("restore_archive_update_failed")
        conn.commit(); return {"ok": True, "archive_id": int(archive_id), "user_id": int(row[2]), "wallet_address": str(row[3])}
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely restore an archived encrypted TON wallet record.")
    parser.add_argument("--archive-id", type=int, required=True)
    parser.add_argument("--confirm", required=True, help=f"must equal {CONFIRM}")
    parser.add_argument("--restored-by", default="manual")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.confirm != CONFIRM:
        print("Refusing restore: explicit confirmation argument is required.")
        return 2
    result = restore_archive_record(args.archive_id, args.restored_by, args.dry_run)
    safe = {k: v for k, v in result.items() if k in {"ok", "error", "dry_run", "archive_id", "user_id", "wallet_address", "conflict_count"}}
    print(safe)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
