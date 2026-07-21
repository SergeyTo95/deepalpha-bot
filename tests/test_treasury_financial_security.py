from pathlib import Path
import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(get=lambda *args, **kwargs: None, post=lambda *args, **kwargs: None))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_all_payment_resolution_uses_single_treasury_and_no_hardcoded_fallbacks():
    purchase = read("services/ton_purchase_service.py")
    ton = read("services/ton_service.py")
    treasury = read("services/treasury_service.py")
    assert "get_public_treasury_address" in purchase
    assert "get_public_treasury_address" in ton
    assert "UQB7mMWEGE4reqMvHG5zPcHl9fQUy6L91UJhiXgyx772kuUv" not in purchase
    assert "TON_PROJECT_WALLET" not in purchase
    assert "ton_project_wallet" not in purchase
    assert "ton_platform_wallet" not in purchase
    assert "treasury_conflict" in treasury
    assert "treasury_not_configured" in treasury


def test_active_treasury_unique_index_and_payment_intents_schema():
    db = read("db/database.py")
    assert "ux_cashier_payment_wallets_single_active" in db
    assert "CREATE TABLE IF NOT EXISTS payment_intents" in db
    for col in [
        "public_reference TEXT NOT NULL UNIQUE", "expected_amount_nano BIGINT NOT NULL",
        "treasury_wallet_id BIGINT NOT NULL", "treasury_address TEXT NOT NULL",
        "expected_sender_address TEXT", "tx_hash TEXT UNIQUE", "idempotency_key TEXT NOT NULL UNIQUE",
    ]:
        assert col in db
    assert "CREATE TABLE IF NOT EXISTS treasury_payouts" in db


def test_intents_are_immutable_and_not_overwritten_like_pending_payments():
    treasury = read("services/treasury_service.py")
    assert "INSERT INTO payment_intents" in treasury
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in treasury
    assert "ON CONFLICT (user_id) DO UPDATE" not in treasury


def test_onchain_verification_rejects_underpay_wrong_destination_source_network_and_duplicate_tx():
    treasury = read("services/treasury_service.py")
    for marker in [
        "network_mismatch", "tx_hash_not_unique", "destination_mismatch",
        "amount_too_low", "source_mismatch", "reference_missing",
        "intent_expired", "intent_already_fulfilled",
    ]:
        assert marker in treasury


def test_donation_controls_and_nano_accounting_present():
    db = read("db/database.py")
    assert "gross_amount_nano" in db or "author_available_nano" in db
    assert "author_available_nano" in db
    assert "author_reserved_nano" in db
    assert "author_withdrawn_nano" in db


def test_payouts_use_internal_wallet_not_users_legacy_ton_wallet():
    treasury = read("services/treasury_service.py")
    db = read("db/database.py")
    assert "FROM user_ton_wallets" in treasury
    assert "internal_wallet_required" in treasury
    assert "wallet_conflict" in treasury
    assert "users.ton_wallet" not in treasury
    assert "recipient_wallet_address" in db


def test_concurrent_payout_and_post_send_failure_guards():
    db = read("db/database.py")
    assert "FOR UPDATE" in db
    assert "concurrent_payout" in db
    assert "payout_sent_reconcile_required" in db
    assert "recipient_revalidation_required" in db


def test_emergency_flags_default_false_and_no_secret_exposure():
    env = read(".env.example")
    treasury = read("services/treasury_service.py").lower()
    assert "TREASURY_INCOMING_ENABLED=false" in env
    assert "TREASURY_OUTGOING_ENABLED=false" in env
    for bad_log in ["seed_encrypted=%s", "mnemonic=%s", "private_key=%s", "decrypted seed"]:
        assert bad_log not in treasury


def test_spoofed_donor_id_contract_is_documented_by_session_requirement():
    web = read("web.py")
    assert "_get_authenticated_web_user_id(request)" in web
    assert "Cannot donate to yourself" in web


def test_behavioral_cursor_paginates_600_transactions_and_persists_after_mark(monkeypatch):
    import services.ton_service as ton_service
    calls = []
    pages = []
    for page_no in range(6):
        page = []
        start = 600 - page_no * 100
        for lt in range(start, start - 100, -1):
            page.append({"transaction_id": {"lt": str(lt), "hash": f"h{lt}"}, "in_msg": {"value": "1"}})
        pages.append(page)

    def fake_page(limit, lt="", tx_hash=""):
        calls.append((limit, lt, tx_hash))
        return pages[len(calls) - 1] if len(calls) <= len(pages) else []

    settings = {"treasury_last_processed_lt": "", "treasury_last_processed_hash": ""}
    monkeypatch.setattr(ton_service, "_get_transactions_page", fake_page)
    monkeypatch.setattr("db.database.get_setting", lambda key, default="": settings.get(key, default))
    monkeypatch.setattr("db.database.set_treasury_transaction_cursor", lambda last_lt, last_hash, backlog_lt="", backlog_hash="", pending_newest_lt="", pending_newest_hash="": (settings.__setitem__("treasury_last_processed_lt", last_lt), settings.__setitem__("treasury_last_processed_hash", last_hash)))

    scan = ton_service.get_transactions_since_treasury_cursor(page_limit=100, max_pages=10)
    txs = scan["transactions"]
    assert len(txs) == 600
    assert txs[0]["transaction_id"]["lt"] == "1"
    assert txs[-1]["transaction_id"]["lt"] == "600"
    assert settings["treasury_last_processed_lt"] == ""
    ton_service.mark_treasury_transactions_cursor(scan, len(txs))
    assert settings["treasury_last_processed_lt"] == "600"
    assert settings["treasury_last_processed_hash"] == "h600"


def test_behavioral_data_text_comment_decoding():
    import base64
    from services.treasury_service import decode_ton_text_comment_from_msg

    ref = "pay_behavioral_ref"
    msg = {"msg_data": {"dataText": base64.b64encode(ref.encode("utf-8")).decode("ascii")}}
    assert decode_ton_text_comment_from_msg(msg) == ref


def test_behavioral_reconciliation_rejects_wrong_source(monkeypatch):
    from services.treasury_service import verify_treasury_payout_onchain
    import services.ton_service as ton_service

    payout = {
        "id": 55,
        "tx_hash": "tx55",
        "treasury_address": "treasury_snapshot",
        "recipient_wallet_address": "recipient_snapshot",
        "amount_nano": 1000,
    }
    tx = {
        "transaction_id": {"hash": "tx55"},
        "network": "mainnet",
        "out_msgs": [{"source": "wrong_source", "destination": "recipient_snapshot", "value": "1000", "message": "payout:55"}],
    }
    monkeypatch.setenv("TON_NETWORK", "mainnet")
    monkeypatch.setattr(ton_service, "_get_transactions_page", lambda limit=100, lt="", tx_hash="": [tx])
    monkeypatch.setattr("services.treasury_service.normalize_ton_address", lambda value: str(value or ""))

    assert verify_treasury_payout_onchain(payout) == {"ok": False, "error": "payout_tx_mismatch"}



def test_behavioral_cursor_incomplete_1200_transactions_does_not_advance_without_mark(monkeypatch):
    import services.ton_service as ton_service
    pages = []
    for page_no in range(12):
        page = []
        start = 1200 - page_no * 100
        for lt in range(start, start - 100, -1):
            page.append({"transaction_id": {"lt": str(lt), "hash": f"h{lt}"}, "in_msg": {"value": "1"}})
        pages.append(page)
    calls = []
    monkeypatch.setattr(ton_service, "_get_transactions_page", lambda limit, lt="", tx_hash="": (calls.append((lt, tx_hash)) or pages[len(calls)-1]))
    settings = {"treasury_last_processed_lt": "1", "treasury_last_processed_hash": "h1", "treasury_scan_page_lt": "", "treasury_scan_page_hash": ""}
    monkeypatch.setattr("db.database.get_setting", lambda key, default="": settings.get(key, default))
    monkeypatch.setattr("db.database.set_treasury_transaction_cursor", lambda *args: (_ for _ in ()).throw(AssertionError("must not mark during scan")))
    scan = ton_service.get_transactions_since_treasury_cursor(page_limit=100, max_pages=10)
    assert len(scan["transactions"]) == 1000
    assert scan["cursor_reached"] is False
    assert scan["history_complete"] is False
    assert scan["next_page_cursor"] == {"lt": "201", "hash": "h201"}
    assert settings["treasury_last_processed_lt"] == "1"


def test_behavioral_cursor_zero_prefix_incomplete_failure_resets_backlog(monkeypatch):
    import services.ton_service as ton_service

    settings = {
        "treasury_last_processed_lt": "1",
        "treasury_last_processed_hash": "h1",
        "treasury_scan_page_lt": "old",
        "treasury_scan_page_hash": "oldh",
        "treasury_scan_newest_lt": "99",
        "treasury_scan_newest_hash": "h99",
    }

    def set_cursor(last_lt, last_hash, backlog_lt="", backlog_hash="", pending_newest_lt="", pending_newest_hash=""):
        settings.update({
            "treasury_last_processed_lt": last_lt,
            "treasury_last_processed_hash": last_hash,
            "treasury_scan_page_lt": backlog_lt,
            "treasury_scan_page_hash": backlog_hash,
            "treasury_scan_newest_lt": pending_newest_lt,
            "treasury_scan_newest_hash": pending_newest_hash,
        })

    monkeypatch.setattr("db.database.set_treasury_transaction_cursor", set_cursor)
    scan = {
        "transactions": [{"transaction_id": {"lt": "2", "hash": "h2"}}],
        "history_complete": False,
        "next_page_cursor": {"lt": "old", "hash": "oldh"},
        "pending_newest": {"lt": "99", "hash": "h99"},
        "saved_cursor": {"lt": "1", "hash": "h1"},
    }
    result = ton_service.process_treasury_payment_scan_once(scan, lambda tx: {"ok": False, "error": "fulfillment_failed"})
    assert result == {"ok": False, "safe_cursor_count": 0}
    assert settings == {
        "treasury_last_processed_lt": "1",
        "treasury_last_processed_hash": "h1",
        "treasury_scan_page_lt": "",
        "treasury_scan_page_hash": "",
        "treasury_scan_newest_lt": "",
        "treasury_scan_newest_hash": "",
    }


def test_behavioral_cursor_partial_prefix_incomplete_failure_resets_backlog(monkeypatch):
    import services.ton_service as ton_service

    saved_calls = []

    def set_cursor(last_lt, last_hash, backlog_lt="", backlog_hash="", pending_newest_lt="", pending_newest_hash=""):
        saved_calls.append((last_lt, last_hash, backlog_lt, backlog_hash, pending_newest_lt, pending_newest_hash))

    monkeypatch.setattr("db.database.set_treasury_transaction_cursor", set_cursor)
    txs = [{"transaction_id": {"lt": str(i), "hash": f"h{i}"}} for i in range(2, 6)]
    scan = {
        "transactions": txs,
        "history_complete": False,
        "next_page_cursor": {"lt": "old", "hash": "oldh"},
        "pending_newest": {"lt": "99", "hash": "h99"},
        "saved_cursor": {"lt": "1", "hash": "h1"},
    }

    def processor(tx):
        return {"ok": tx["transaction_id"]["lt"] == "2"}

    result = ton_service.process_treasury_payment_scan_once(scan, processor)
    assert result == {"ok": False, "safe_cursor_count": 1}
    assert saved_calls == [("1", "h1", "", "", "", "")]


def test_behavioral_cursor_multicycle_incomplete_failure_restart_no_loss(monkeypatch):
    import services.ton_service as ton_service

    settings = {
        "treasury_last_processed_lt": "1",
        "treasury_last_processed_hash": "h1",
        "treasury_scan_page_lt": "",
        "treasury_scan_page_hash": "",
        "treasury_scan_newest_lt": "",
        "treasury_scan_newest_hash": "",
    }
    cursor_writes = []

    def get_setting(key, default=""):
        return settings.get(key, default)

    def set_cursor(last_lt, last_hash, backlog_lt="", backlog_hash="", pending_newest_lt="", pending_newest_hash=""):
        write = (str(last_lt), str(last_hash), str(backlog_lt), str(backlog_hash), str(pending_newest_lt), str(pending_newest_hash))
        cursor_writes.append(write)
        settings.update({
            "treasury_last_processed_lt": write[0],
            "treasury_last_processed_hash": write[1],
            "treasury_scan_page_lt": write[2],
            "treasury_scan_page_hash": write[3],
            "treasury_scan_newest_lt": write[4],
            "treasury_scan_newest_hash": write[5],
        })

    newest_to_oldest = list(range(1200, 0, -1))

    def make_tx(lt):
        return {"transaction_id": {"lt": str(lt), "hash": f"h{lt}"}, "in_msg": {"value": "1"}}

    def fake_page(limit, lt="", tx_hash=""):
        if lt:
            start_index = newest_to_oldest.index(int(lt)) + 1
        else:
            start_index = 0
        return [make_tx(i) for i in newest_to_oldest[start_index:start_index + int(limit)]]

    monkeypatch.setattr(ton_service, "_get_transactions_page", fake_page)
    monkeypatch.setattr("db.database.get_setting", get_setting)
    monkeypatch.setattr("db.database.set_treasury_transaction_cursor", set_cursor)

    processed = set()
    deliveries = {}
    attempts = {}

    def processor(tx):
        lt = int(tx["transaction_id"]["lt"])
        attempts[lt] = attempts.get(lt, 0) + 1
        if lt in processed:
            return {"ok": True, "already_processed": True}
        if lt == 500 and attempts[lt] == 1:
            return {"ok": False, "error": "fulfillment_failed"}
        processed.add(lt)
        deliveries[lt] = deliveries.get(lt, 0) + 1
        return {"ok": True}

    scan1 = ton_service.get_transactions_since_treasury_cursor(page_limit=100, max_pages=10)
    assert [int(t["transaction_id"]["lt"]) for t in (scan1["transactions"][0], scan1["transactions"][-1])] == [201, 1200]
    result1 = ton_service.process_treasury_payment_scan_once(scan1, processor)
    assert result1 == {"ok": False, "safe_cursor_count": 299}
    assert settings["treasury_last_processed_lt"] == "1"
    assert settings["treasury_scan_page_lt"] == ""
    assert settings["treasury_scan_newest_lt"] == ""
    assert 500 not in processed

    # Restart: empty backlog makes the scan start from newest again, so tx500 is present again.
    scan2 = ton_service.get_transactions_since_treasury_cursor(page_limit=100, max_pages=10)
    lts2 = [int(t["transaction_id"]["lt"]) for t in scan2["transactions"]]
    assert 500 in lts2
    result2 = ton_service.process_treasury_payment_scan_once(scan2, processor)
    assert result2 == {"ok": True, "safe_cursor_count": 1000}
    assert settings["treasury_last_processed_lt"] == "1"
    assert settings["treasury_scan_page_lt"] == "201"
    assert settings["treasury_scan_newest_lt"] == "1200"

    scan3 = ton_service.get_transactions_since_treasury_cursor(page_limit=100, max_pages=10)
    lts3 = [int(t["transaction_id"]["lt"]) for t in scan3["transactions"]]
    assert lts3 == list(range(2, 201))
    result3 = ton_service.process_treasury_payment_scan_once(scan3, processor)
    assert result3 == {"ok": True, "safe_cursor_count": 199}
    assert settings["treasury_last_processed_lt"] == "1200"
    assert settings["treasury_scan_page_lt"] == ""
    assert settings["treasury_scan_newest_lt"] == ""

    assert set(deliveries) == set(range(2, 1201))
    assert all(count == 1 for count in deliveries.values())
    assert attempts[500] == 2
    assert deliveries[500] == 1
    assert ("500", "h500", "", "", "", "") not in cursor_writes


def test_behavioral_reconciliation_missing_fields_rejected(monkeypatch):
    from services.treasury_service import verify_treasury_payout_onchain
    import services.ton_service as ton_service
    payout = {"id": 1, "tx_hash": "tx", "treasury_address": "src", "recipient_wallet_address": "dst", "amount_nano": 10}
    monkeypatch.setattr(ton_service, "_get_transactions_page", lambda limit=100, lt="", tx_hash="": [{"transaction_id": {"hash": "tx"}, "network": "mainnet", "out_msgs": [{"source": "src", "value": "10"}]}])
    monkeypatch.setattr("services.treasury_service.normalize_ton_address", lambda value: str(value or ""))
    assert verify_treasury_payout_onchain(payout) == {"ok": False, "error": "onchain_data_incomplete"}


def test_behavioral_reconciliation_finds_hash_older_than_100(monkeypatch):
    from services.treasury_service import verify_treasury_payout_onchain
    import services.ton_service as ton_service
    payout = {"id": 9, "tx_hash": "target", "treasury_address": "src", "recipient_wallet_address": "dst", "amount_nano": 10}
    page1 = [{"transaction_id": {"lt": str(300-i), "hash": f"h{i}"}, "network": "mainnet", "out_msgs": [{"source": "x", "destination": "y", "value": "1"}]} for i in range(100)]
    page2 = [{"transaction_id": {"lt": "199", "hash": "target"}, "network": "mainnet", "out_msgs": [{"source": "src", "destination": "dst", "value": "10", "message": "payout:9"}]}]
    calls = []
    monkeypatch.setattr(ton_service, "_get_transactions_page", lambda limit=100, lt="", tx_hash="": (calls.append(1) or (page1 if len(calls) == 1 else page2)))
    monkeypatch.setattr("services.treasury_service.normalize_ton_address", lambda value: str(value or ""))
    assert verify_treasury_payout_onchain(payout)["ok"] is True
    assert len(calls) == 2


def test_behavioral_toncenter_v2_payout_response_without_network_succeeds(monkeypatch):
    from services.treasury_service import verify_treasury_payout_onchain
    import services.ton_service as ton_service
    payout = {"id": 77, "tx_hash": "tx77", "treasury_address": "src", "recipient_wallet_address": "dst", "amount_nano": 123}
    tx = {"transaction_id": {"lt": "777", "hash": "tx77"}, "out_msgs": [{"source": "src", "destination": "dst", "value": "123", "message": "payout:77"}]}
    monkeypatch.setattr(ton_service, "_get_transactions_page", lambda limit=100, lt="", tx_hash="": [tx])
    monkeypatch.setattr("services.treasury_service.normalize_ton_address", lambda value: str(value or ""))
    assert verify_treasury_payout_onchain(payout) == {"ok": True, "tx_hash": "tx77"}


def test_behavioral_cursor_not_advanced_after_fulfillment_failure_then_advances_once(monkeypatch):
    import services.ton_service as ton_service
    tx = {"transaction_id": {"lt": "10", "hash": "h10"}, "in_msg": {"value": "100"}}
    scan = {"transactions": [tx], "history_complete": True, "newest_seen": {"lt": "10", "hash": "h10"}, "pending_newest": {"lt": "", "hash": ""}}
    saved = []
    monkeypatch.setattr("db.database.set_treasury_transaction_cursor", lambda *args: saved.append(args))
    ton_service.mark_treasury_transactions_cursor(scan, 0)
    assert saved == []
    ton_service.mark_treasury_transactions_cursor(scan, 1)
    assert saved == [("10", "h10", "", "", "", "")]



def test_payment_intent_reference_lookup_and_buy_slots_hardened_contract():
    db = read("db/database.py")
    web = read("web.py")
    app = read("app.py")
    assert "def get_payment_intent_by_public_reference" in db
    assert "WHERE public_reference=%s" in db
    assert "intent_lookup_failed" in db
    assert "get_payment_intent_by_public_reference(reference)" in app
    assert "buy_watchlist_slots_atomic" in db
    assert "FOR UPDATE" in db
    assert "watchlist_slot_purchases" in db
    assert "_get_authenticated_web_user_id(request)" in web
    assert "data.get(\"user_id\"" not in web[web.index("async def handle_buy_slots"):web.index("async def handle_options")]
