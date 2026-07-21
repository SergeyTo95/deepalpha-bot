import os
import json
import time
import secrets
import hashlib
from urllib.parse import urlparse
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    import psycopg2
    import psycopg2.extras
    from psycopg2 import errors
except ModuleNotFoundError:  # pragma: no cover - minimal test env
    class _MissingPsycopg2:
        def connect(self, *args, **kwargs):
            raise RuntimeError("psycopg2 is not installed")
    class _Errors:
        pass
    psycopg2 = _MissingPsycopg2()
    errors = _Errors()

DATABASE_URL = os.getenv("DATABASE_URL", "")


def _db_identifier_redacted() -> str:
    parsed = urlparse(DATABASE_URL or "")
    if parsed.scheme or parsed.hostname or parsed.path:
        name = (parsed.path or "").lstrip("/") or "unknown"
        return f"{parsed.scheme or 'db'}://{parsed.hostname or 'unknown'}/{name}"
    return "missing"


def _first_scalar(row, default=0):
    if not row:
        return default
    if isinstance(row, dict):
        values = list(row.values())
        return values[0] if values else default
    return row[0]


def _table_exists(cursor, table: str) -> bool:
    try:
        cursor.execute("SELECT to_regclass(%s)", (table,))
        row = cursor.fetchone()
        return bool(_first_scalar(row))
    except Exception:
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            return cursor.fetchone() is not None
        except Exception:
            return False


def _list_tables(cursor) -> List[str]:
    try:
        cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        return [str(_first_scalar(r, "")) for r in cursor.fetchall()]
    except Exception:
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            return [str(_first_scalar(r, "")) for r in cursor.fetchall()]
        except Exception:
            return []


def _count_table(cursor, table: str) -> int:
    if not table.replace("_", "").isalnum():
        return 0
    cursor.execute(f"SELECT COUNT(*) AS count FROM {table}")
    row = cursor.fetchone()
    return int(_first_scalar(row, 0) or 0)


def _referral_relationship_table(cursor) -> Optional[Tuple[str, str, str]]:
    # user_id/referrer owner and source_user_id/referred user: referral_rewards stores concrete historical referral payout edges.
    if _table_exists(cursor, "referral_relationships"):
        return ("referral_relationships", "referrer_id", "user_id")
    if _table_exists(cursor, "referrals"):
        return ("referrals", "referrer_id", "user_id")
    if _table_exists(cursor, "referral_rewards"):
        return ("referral_rewards", "user_id", "source_user_id")
    return None


def _insert_referral_relationship(cursor, referrer_id: int, referred_user_id: int) -> None:
    rel = _referral_relationship_table(cursor)
    if not rel or int(referrer_id) == int(referred_user_id):
        return
    table, ref_col, user_col = rel
    if table == "referral_rewards":
        return
    try:
        cursor.execute(f"SELECT 1 FROM {table} WHERE {ref_col} = %s AND {user_col} = %s LIMIT 1", (referrer_id, referred_user_id))
        if cursor.fetchone():
            return
        cursor.execute(f"INSERT INTO {table} ({ref_col}, {user_col}, created_at) VALUES (%s, %s, %s)", (referrer_id, referred_user_id, datetime.utcnow().isoformat()))
    except Exception as e:
        print(f"insert_referral_relationship error: {e}")


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing")
    conn = psycopg2.connect(
        DATABASE_URL,
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )
    return conn



def _diagnose_and_enforce_user_ton_wallet_uniques(conn, cursor) -> None:
    if not _table_exists(cursor, "user_ton_wallets"):
        return
    reports = []
    duplicate_user_ids = []
    duplicate_addresses = []
    for col, target in (("user_id", duplicate_user_ids), ("wallet_address", duplicate_addresses)):
        cursor.execute(f"SELECT {col}, COUNT(*) FROM user_ton_wallets GROUP BY {col} HAVING COUNT(*) > 1")
        dupes = cursor.fetchall()
        for row in dupes:
            value = _first_scalar(row, "")
            count = row[1] if not isinstance(row, dict) else list(row.values())[1]
            target.append(str(value))
            reports.append(f"duplicate {col}: {value} x{count}")
    if reports:
        report = "; ".join(reports[:50])
        print(f"CRITICAL user_ton_wallets duplicate incident; unique indexes deferred; {report}")
        try:
            cursor.execute("""
                INSERT INTO settings (key, value, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at
            """, ("ton_wallet_duplicate_incident", report, datetime.utcnow().isoformat()))
        except Exception as exc:
            print(f"CRITICAL failed to write ton_wallet_duplicate_incident marker: {exc.__class__.__name__}")
        return
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_ton_wallets_user_id_unique ON user_ton_wallets(user_id)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_ton_wallets_wallet_address_unique ON user_ton_wallets(wallet_address)")

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _init_db_inner(conn, cursor)
    finally:
        try:
            cursor.close()
        finally:
            conn.close()


def _init_db_inner(conn, cursor):
    global _live_analyst_tables_ready
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS analyses (
        id SERIAL PRIMARY KEY,
        url TEXT,
        question TEXT,
        category TEXT,
        market_probability TEXT,
        system_probability TEXT,
        confidence TEXT,
        reasoning TEXT,
        main_scenario TEXT,
        alt_scenario TEXT,
        conclusion TEXT,
        created_at TEXT,
        user_id INTEGER DEFAULT 0
    )
    """)


    cursor.execute("""
    CREATE TABLE IF NOT EXISTS gemini_usage (
        id SERIAL PRIMARY KEY,
        usage_date DATE NOT NULL DEFAULT CURRENT_DATE,
        feature TEXT NOT NULL,
        user_id BIGINT NULL,
        chat_id BIGINT NULL,
        is_background BOOLEAN NOT NULL DEFAULT FALSE,
        calls INTEGER NOT NULL DEFAULT 0,
        units INTEGER NOT NULL DEFAULT 0,
        last_call_at TIMESTAMP DEFAULT NOW(),
        UNIQUE (usage_date, feature, user_id, chat_id, is_background)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gemini_usage_date ON gemini_usage(usage_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gemini_usage_feature_date ON gemini_usage(feature, usage_date)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS opportunities (
        id SERIAL PRIMARY KEY,
        url TEXT,
        question TEXT,
        category TEXT,
        market_probability TEXT,
        system_probability TEXT,
        confidence TEXT,
        reasoning TEXT,
        main_scenario TEXT,
        alt_scenario TEXT,
        conclusion TEXT,
        opportunity_score INTEGER,
        created_at TEXT,
        user_id INTEGER DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_analyst_profiles (
        user_id BIGINT PRIMARY KEY,
        risk_style TEXT NOT NULL DEFAULT 'balanced',
        answer_depth TEXT NOT NULL DEFAULT 'normal',
        primary_goal TEXT NOT NULL DEFAULT 'find_opportunities',
        preferred_domains TEXT NOT NULL DEFAULT 'crypto,sports,esports,politics,polymarket',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        token_balance INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        is_vip INTEGER DEFAULT 0,
        total_analyses INTEGER DEFAULT 0,
        total_opportunities INTEGER DEFAULT 0,
        referred_by BIGINT DEFAULT NULL,
        referral_earnings_ton REAL DEFAULT 0,
        total_referrals INTEGER DEFAULT 0,
        subscription_until TEXT DEFAULT NULL,
        daily_analyses INTEGER DEFAULT 0,
        daily_opportunities INTEGER DEFAULT 0,
        daily_reset_date TEXT DEFAULT NULL,
        free_analyses_used INTEGER DEFAULT 0,
        free_opportunities_used INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id SERIAL PRIMARY KEY,
        tx_hash TEXT UNIQUE,
        user_id BIGINT,
        ton_amount REAL,
        tokens_granted INTEGER,
        referral_bonus_ton REAL DEFAULT 0,
        referrer_id BIGINT DEFAULT NULL,
        created_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS web_sessions (
        session_token_hash TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        provider TEXT,
        created_at TEXT,
        expires_at TEXT,
        last_seen_at TEXT,
        user_agent TEXT,
        ip_hash TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS web_accounts (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        provider TEXT NOT NULL,
        provider_sub TEXT NOT NULL,
        email TEXT,
        name TEXT,
        avatar_url TEXT,
        created_at TEXT,
        updated_at TEXT,
        UNIQUE(provider, provider_sub)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS web_analysis_history (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        analysis_type TEXT NOT NULL,
        market_url TEXT NOT NULL,
        market_slug TEXT,
        question TEXT,
        display_prediction TEXT,
        market_probability TEXT,
        confidence TEXT,
        category TEXT,
        status TEXT NOT NULL,
        result_json TEXT,
        error TEXT,
        created_at TEXT
    )
    """)



    cursor.execute("""
    CREATE TABLE IF NOT EXISTS web_analysis_jobs (
        job_id TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        analysis_type TEXT NOT NULL,
        market_url TEXT NOT NULL,
        status TEXT NOT NULL,
        progress TEXT,
        history_id INTEGER,
        result_json TEXT,
        error TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pending_payments (
        user_id BIGINT PRIMARY KEY,
        amount REAL,
        payment_type TEXT DEFAULT 'tokens',
        created_at INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cashier_payment_wallets (
        id SERIAL PRIMARY KEY,
        wallet_address TEXT NOT NULL,
        seed_encrypted TEXT,
        network TEXT NOT NULL DEFAULT 'MAINNET',
        status TEXT NOT NULL DEFAULT 'active',
        created_by BIGINT,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
        seed_reveal_used BOOLEAN DEFAULT FALSE,
        seed_revealed_at TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cashier_payment_wallets_status ON cashier_payment_wallets(status)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_cashier_payment_wallets_wallet_address ON cashier_payment_wallets(wallet_address)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_cashier_payment_wallets_single_active ON cashier_payment_wallets((status)) WHERE status='active'")

    _init_live_analyst_tables(cursor)
    ensure_gemini_lockdown_tables(cursor)
    _live_analyst_tables_ready = True

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payment_intents (
        id SERIAL PRIMARY KEY,
        public_reference TEXT NOT NULL UNIQUE,
        user_id BIGINT NOT NULL,
        product_type TEXT NOT NULL,
        product_ref TEXT,
        expected_amount_nano BIGINT NOT NULL,
        treasury_wallet_id BIGINT NOT NULL,
        treasury_address TEXT NOT NULL,
        expected_sender_address TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        tx_hash TEXT UNIQUE,
        expires_at TIMESTAMP NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        verified_at TIMESTAMP,
        fulfilled_at TIMESTAMP,
        fail_reason TEXT,
        metadata_json TEXT,
        idempotency_key TEXT NOT NULL UNIQUE
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_intents_status ON payment_intents(status)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS treasury_payouts (
        id SERIAL PRIMARY KEY,
        payout_type TEXT NOT NULL,
        source_record_id BIGINT,
        recipient_user_id BIGINT NOT NULL,
        recipient_wallet_id BIGINT NOT NULL,
        recipient_wallet_address TEXT NOT NULL,
        treasury_wallet_id BIGINT NOT NULL,
        treasury_address TEXT NOT NULL,
        amount_nano BIGINT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        idempotency_key TEXT NOT NULL UNIQUE,
        tx_hash TEXT UNIQUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        approved_at TIMESTAMP,
        submitted_at TIMESTAMP,
        paid_at TIMESTAMP,
        fail_reason TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_treasury_payouts_status ON treasury_payouts(status)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS signal_history (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        question TEXT,
        created_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS signal_cache (
        category TEXT PRIMARY KEY,
        data TEXT,
        updated_at INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS token_packages (
        id SERIAL PRIMARY KEY,
        name TEXT,
        tokens INTEGER,
        price_ton REAL,
        discount_percent INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ton_jetton_assets (
        id SERIAL PRIMARY KEY,
        symbol TEXT NOT NULL,
        name TEXT,
        network TEXT DEFAULT 'mainnet',
        master_address TEXT UNIQUE NOT NULL,
        decimals INTEGER DEFAULT 9,
        is_enabled BOOLEAN DEFAULT TRUE,
        is_deepalpha_token BOOLEAN DEFAULT FALSE,
        sort_order INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_ton_jetton_balances (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        wallet_address TEXT NOT NULL,
        jetton_master_address TEXT NOT NULL,
        balance_raw TEXT DEFAULT '0',
        balance_display TEXT DEFAULT '0',
        last_checked_at TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ton_jetton_assets_network_enabled ON ton_jetton_assets(network, is_enabled)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ton_jetton_assets_master_address ON ton_jetton_assets(master_address)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_ton_jetton_balances_user_jetton ON user_ton_jetton_balances(user_id, jetton_master_address)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_ton_jetton_balances_wallet ON user_ton_jetton_balances(wallet_address)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS analysis_checks (
        id SERIAL PRIMARY KEY,
        code TEXT UNIQUE NOT NULL,
        created_by_user_id BIGINT,
        created_by_admin BOOLEAN DEFAULT FALSE,
        check_type TEXT NOT NULL,
        max_activations INTEGER DEFAULT 1,
        used_activations INTEGER DEFAULT 0,
        expires_at TEXT,
        require_channel_sub BOOLEAN DEFAULT FALSE,
        required_channel TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT,
        unit_price_tokens INTEGER DEFAULT 0,
        total_price_tokens INTEGER DEFAULT 0,
        refunded_tokens INTEGER DEFAULT 0,
        disabled_at TEXT
    )
    """)
    cursor.execute("ALTER TABLE analysis_checks ADD COLUMN IF NOT EXISTS unit_price_tokens INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE analysis_checks ADD COLUMN IF NOT EXISTS total_price_tokens INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE analysis_checks ADD COLUMN IF NOT EXISTS refunded_tokens INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE analysis_checks ADD COLUMN IF NOT EXISTS disabled_at TEXT")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS analysis_check_claims (
        id SERIAL PRIMARY KEY,
        check_id INTEGER NOT NULL,
        user_id BIGINT NOT NULL,
        status TEXT DEFAULT 'claimed',
        claimed_at TEXT,
        used_at TEXT,
        analysis_type TEXT,
        UNIQUE(check_id, user_id)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_checks_code ON analysis_checks(code)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_check_claims_user_status ON analysis_check_claims(user_id, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_check_claims_check_user ON analysis_check_claims(check_id, user_id)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS predictions_tracking (
        id SERIAL PRIMARY KEY,
        user_id BIGINT DEFAULT 0,
        market_slug TEXT,
        market_url TEXT,
        question TEXT,
        category TEXT,
        market_type TEXT,
        semantic_type TEXT,
        market_probability_yes REAL,
        market_probability_no REAL,
        market_leader TEXT,
        market_prob_value REAL,
        system_prediction TEXT,
        system_probability REAL,
        system_outcome TEXT,
        confidence TEXT,
        delta REAL,
        alpha_label TEXT,
        market_balance TEXT,
        display_prediction TEXT,
        created_at TEXT,
        market_end_date TEXT,
        resolved_at TEXT DEFAULT NULL,
        actual_outcome TEXT DEFAULT NULL,
        is_correct INTEGER DEFAULT NULL,
        brier_score REAL DEFAULT NULL,
        log_loss REAL DEFAULT NULL
    )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_slug ON predictions_tracking(market_slug)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_resolved ON predictions_tracking(resolved_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_user ON predictions_tracking(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_category ON predictions_tracking(category)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS watchlist (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        market_slug TEXT NOT NULL,
        market_url TEXT,
        question TEXT,
        category TEXT,
        initial_probability REAL,
        initial_market_prob_str TEXT,
        last_checked_probability REAL,
        last_probability_change REAL DEFAULT 0,
        market_end_date TEXT,
        notify_enabled INTEGER DEFAULT 1,
        notified_change INTEGER DEFAULT 0,
        notified_closing_soon INTEGER DEFAULT 0,
        notified_resolved INTEGER DEFAULT 0,
        is_closed INTEGER DEFAULT 0,
        extra_slot INTEGER DEFAULT 0,
        created_at TEXT,
        last_checked_at TEXT,
        billing_status TEXT DEFAULT 'active',
        paused_reason TEXT,
        paused_at TEXT,
        last_billed_at TEXT,
        tokens_spent INTEGER DEFAULT 0,
        autopilot_enabled INTEGER DEFAULT 1,
        ai_summary_enabled INTEGER DEFAULT 1
    )
    """)

    for migration in [
        "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS billing_status TEXT DEFAULT 'active'",
        "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS paused_reason TEXT",
        "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS paused_at TEXT",
        "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS last_billed_at TEXT",
        "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS tokens_spent INTEGER DEFAULT 0",
        "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS autopilot_enabled INTEGER DEFAULT 1",
        "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS ai_summary_enabled INTEGER DEFAULT 1",
    ]:
        try:
            cursor.execute(migration)
        except Exception:
            pass

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS watchlist_token_ledger (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        watchlist_id INTEGER NOT NULL,
        market_slug TEXT,
        event_type TEXT NOT NULL,
        event_fingerprint TEXT NOT NULL,
        tokens INTEGER NOT NULL,
        created_at TEXT,
        UNIQUE(user_id, watchlist_id, event_type, event_fingerprint)
    )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_slug ON watchlist(market_slug)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_closed ON watchlist(is_closed)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_billing_status ON watchlist(billing_status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_token_ledger_user ON watchlist_token_ledger(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_token_ledger_watchlist ON watchlist_token_ledger(watchlist_id)")

    # ═══ AUTHORS & POSTS ═══

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS author_posts (
        id SERIAL PRIMARY KEY,
        author_id BIGINT NOT NULL,
        market_slug TEXT,
        market_url TEXT,
        question TEXT,
        category TEXT,
        display_prediction TEXT,
        confidence TEXT,
        market_probability TEXT,
        alpha_label TEXT,
        author_comment TEXT,
        full_analysis_json TEXT,
        total_donations_ton REAL DEFAULT 0,
        total_donors INTEGER DEFAULT 0,
        created_at TEXT,
        is_deleted INTEGER DEFAULT 0
    )
    """)

    for stmt in (
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS title TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS event_question TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS article_type TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS thesis TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS reasoning TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS probability_view TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS risks TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS conclusion TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS source_type TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS source_ref_id TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'published'",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS views_count INTEGER DEFAULT 0",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS unique_views_count INTEGER DEFAULT 0",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS shares_count INTEGER DEFAULT 0",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS updated_at TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS body_text TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS cover_image_file_id TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS cover_image_url TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS attached_analysis_json TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS article_tags TEXT",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS article_language TEXT DEFAULT 'en'",
        "ALTER TABLE author_posts ADD COLUMN IF NOT EXISTS published_to_profile INTEGER DEFAULT 1",
    ):
        cursor.execute(stmt)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_author ON author_posts(author_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_created ON author_posts(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_deleted ON author_posts(is_deleted)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_status ON author_posts(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_source ON author_posts(source_type, source_ref_id)")

    # ═══ SUBSCRIPTIONS (бесплатные) ═══

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS author_subscriptions (
        id SERIAL PRIMARY KEY,
        subscriber_id BIGINT NOT NULL,
        author_id BIGINT NOT NULL,
        notifications_enabled INTEGER DEFAULT 1,
        created_at TEXT,
        UNIQUE(subscriber_id, author_id)
    )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subs_subscriber ON author_subscriptions(subscriber_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subs_author ON author_subscriptions(author_id)")

    # ═══ DONATIONS ═══

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS author_donations (
        id SERIAL PRIMARY KEY,
        donor_id BIGINT NOT NULL,
        author_id BIGINT NOT NULL,
        post_id INTEGER DEFAULT NULL,
        ton_amount REAL NOT NULL,
        platform_fee_ton REAL DEFAULT 0,
        author_received_ton REAL DEFAULT 0,
        tx_hash TEXT,
        status TEXT DEFAULT 'pending',
        comment TEXT,
        created_at TEXT
    )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_donations_author ON author_donations(author_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_donations_donor ON author_donations(donor_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_donations_post ON author_donations(post_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_donations_status ON author_donations(status)")

    # ═══ WITHDRAWAL REQUESTS ═══

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS withdrawal_requests (
        id SERIAL PRIMARY KEY,
        author_id BIGINT NOT NULL,
        amount_ton REAL NOT NULL,
        ton_wallet TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        admin_note TEXT,
        tx_hash TEXT,
        created_at TEXT,
        processed_at TEXT
    )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_withdrawals_author ON withdrawal_requests(author_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawal_requests(status)")


    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_ton_wallets (
        id SERIAL PRIMARY KEY,
        user_id BIGINT UNIQUE NOT NULL,
        network TEXT DEFAULT 'testnet',
        wallet_address TEXT UNIQUE NOT NULL,
        wallet_version TEXT DEFAULT 'v4r2',
        public_key TEXT,
        seed_encrypted TEXT NOT NULL,
        seed_revealed_at TEXT,
        seed_reveal_used BOOLEAN DEFAULT FALSE,
        status TEXT DEFAULT 'active',
        created_at TEXT,
        updated_at TEXT,
        last_balance_nano TEXT DEFAULT '0',
        last_balance_checked_at TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_ton_wallets_user_id ON user_ton_wallets(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_ton_wallets_wallet_address ON user_ton_wallets(wallet_address)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_ton_wallets_status ON user_ton_wallets(status)")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_ton_wallet_quarantine_archive (
        id SERIAL PRIMARY KEY,
        original_wallet_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        wallet_address TEXT,
        network TEXT,
        wallet_version TEXT,
        public_key TEXT,
        seed_encrypted TEXT NOT NULL,
        seed_revealed_at TEXT,
        seed_reveal_used BOOLEAN,
        status TEXT,
        created_at TEXT,
        updated_at TEXT,
        last_balance_nano TEXT,
        last_balance_checked_at TEXT,
        archived_at TEXT NOT NULL,
        archived_by BIGINT,
        canonical_wallet_id BIGINT,
        archive_reason TEXT NOT NULL,
        restored_at TEXT,
        restored_by TEXT,
        restore_status TEXT DEFAULT 'archived'
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_ton_wallet_archive_user_id ON user_ton_wallet_quarantine_archive(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_ton_wallet_archive_address ON user_ton_wallet_quarantine_archive(wallet_address)")
    for migration in [
        "ALTER TABLE user_ton_wallet_quarantine_archive ADD COLUMN IF NOT EXISTS restored_at TEXT",
        "ALTER TABLE user_ton_wallet_quarantine_archive ADD COLUMN IF NOT EXISTS restored_by TEXT",
        "ALTER TABLE user_ton_wallet_quarantine_archive ADD COLUMN IF NOT EXISTS restore_status TEXT DEFAULT 'archived'",
    ]:
        try:
            cursor.execute(migration)
        except Exception:
            pass
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_ton_wallet_quarantine_audit (
        id SERIAL PRIMARY KEY,
        original_wallet_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        wallet_address TEXT,
        network TEXT,
        wallet_version TEXT,
        status TEXT,
        last_balance_nano TEXT,
        seed_reveal_used BOOLEAN,
        original_created_at TEXT,
        action TEXT NOT NULL,
        canonical_wallet_id BIGINT,
        admin_user_id BIGINT,
        created_at TEXT NOT NULL
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_ton_wallet_quarantine_user_id ON user_ton_wallet_quarantine_audit(user_id)")
    _diagnose_and_enforce_user_ton_wallet_uniques(conn, cursor)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ton_wallet_transactions (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        wallet_address TEXT,
        direction TEXT NOT NULL,
        amount_nano TEXT NOT NULL,
        fee_nano TEXT DEFAULT '0',
        tx_hash TEXT,
        destination_address TEXT,
        source_address TEXT,
        status TEXT DEFAULT 'pending',
        comment TEXT,
        created_at TEXT,
        updated_at TEXT,
        confirmed_at TEXT,
        error TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ton_wallet_txs_user_created ON ton_wallet_transactions(user_id, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ton_wallet_txs_hash ON ton_wallet_transactions(tx_hash)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ton_wallet_txs_status ON ton_wallet_transactions(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ton_wallet_txs_wallet_address ON ton_wallet_transactions(wallet_address)")


    cursor.execute("ALTER TABLE ton_wallet_transactions ADD COLUMN IF NOT EXISTS product_type TEXT")
    cursor.execute("ALTER TABLE ton_wallet_transactions ADD COLUMN IF NOT EXISTS payment_intent_id BIGINT")
    cursor.execute("ALTER TABLE ton_wallet_transactions ADD COLUMN IF NOT EXISTS purchase_status TEXT")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ton_purchase_intents (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        product_type TEXT NOT NULL,
        wallet_address TEXT NOT NULL,
        project_wallet TEXT NOT NULL,
        expected_amount_nano TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'created',
        tx_hash TEXT,
        requested_tokens INTEGER DEFAULT 0,
        bonus_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        price_per_token_nano TEXT DEFAULT '0',
        subscription_days INTEGER DEFAULT 0,
        metadata_json TEXT DEFAULT '{}',
        created_at TEXT,
        submitted_at TEXT,
        fulfilled_at TEXT,
        failed_at TEXT,
        fail_reason TEXT,
        updated_at TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ton_purchase_intents_user ON ton_purchase_intents(user_id, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ton_purchase_intents_status ON ton_purchase_intents(status)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ton_purchase_intents_tx_hash_unique ON ton_purchase_intents(tx_hash) WHERE tx_hash IS NOT NULL AND tx_hash <> ''")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS referral_rewards (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        source_user_id BIGINT NOT NULL,
        purchase_type TEXT NOT NULL DEFAULT 'token_purchase',
        purchase_ref TEXT,
        purchase_amount_nano BIGINT NOT NULL DEFAULT 0,
        reward_percent NUMERIC NOT NULL DEFAULT 0,
        reward_nano BIGINT NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        unlock_at TIMESTAMP,
        withdrawal_request_id BIGINT,
        withdrawn_at TIMESTAMP,
        withdrawal_tx_hash TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cursor.execute("ALTER TABLE referral_rewards ADD COLUMN IF NOT EXISTS withdrawal_request_id BIGINT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_user_id ON referral_rewards(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_source_user_id ON referral_rewards(source_user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_status ON referral_rewards(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_unlock_at ON referral_rewards(unlock_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_withdrawal_request_id ON referral_rewards(withdrawal_request_id)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_rewards_purchase_ref_user ON referral_rewards(purchase_ref, user_id) WHERE purchase_ref IS NOT NULL")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS referral_reward_withdrawal_requests (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        amount_nano BIGINT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        processed_at TIMESTAMP,
        processed_by BIGINT,
        tx_hash TEXT,
        notes TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ref_reward_withdraw_reqs_user ON referral_reward_withdrawal_requests(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ref_reward_withdraw_reqs_status ON referral_reward_withdrawal_requests(status)")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS referral_payout_wallets (
        id SERIAL PRIMARY KEY,
        wallet_address TEXT NOT NULL,
        seed_encrypted TEXT NOT NULL,
        network TEXT NOT NULL DEFAULT 'MAINNET',
        status TEXT NOT NULL DEFAULT 'active',
        created_by BIGINT,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
        seed_reveal_used BOOLEAN DEFAULT FALSE,
        seed_revealed_at TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_referral_payout_wallets_status ON referral_payout_wallets(status)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_payout_wallets_wallet_address ON referral_payout_wallets(wallet_address)")

    migrations = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_earnings_ton REAL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS total_referrals INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_until TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_analyses INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_opportunities INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_reset_date TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS free_analyses_used INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS free_opportunities_used INTEGER DEFAULT 0",
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS referral_bonus_ton REAL DEFAULT 0",
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS referrer_id BIGINT DEFAULT NULL",
        "ALTER TABLE pending_payments ADD COLUMN IF NOT EXISTS payment_type TEXT DEFAULT 'tokens'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_author INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS author_balance_ton REAL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS author_withdrawn_ton REAL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS author_available_nano BIGINT DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS author_reserved_nano BIGINT DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS author_withdrawn_nano BIGINT DEFAULT 0",
        "ALTER TABLE author_donations ADD COLUMN IF NOT EXISTS gross_amount_nano BIGINT DEFAULT 0",
        "ALTER TABLE author_donations ADD COLUMN IF NOT EXISTS platform_fee_nano BIGINT DEFAULT 0",
        "ALTER TABLE author_donations ADD COLUMN IF NOT EXISTS author_net_amount_nano BIGINT DEFAULT 0",
        "ALTER TABLE author_donations ADD COLUMN IF NOT EXISTS payment_intent_id BIGINT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS author_bio TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS author_since TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS ton_wallet TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS inline_queries_count INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'ru'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS extra_watchlist_slots INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS total_subscribers INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS total_posts INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS posts_today INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS posts_reset_date TEXT DEFAULT NULL",
        "ALTER TABLE referral_rewards ADD COLUMN IF NOT EXISTS withdrawal_request_id BIGINT",
    ]
    for migration in migrations:
        try:
            cursor.execute(migration)
        except Exception:
            pass


    # PolyWar: Battle for Consensus additive tables and safe next-season profile bootstrap.
    try:
        from services.polywar_service import init_polywar_schema
        from services.polywar_map_service import bootstrap_compact_next_season_profile
        init_polywar_schema(conn)
        bootstrap_compact_next_season_profile(conn)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"init polywar schema error: {e}")

    cursor.execute("SELECT COUNT(*) FROM token_packages")
    count = cursor.fetchone()[0]
    if count == 0:
        default_packages = [
            ("Стартовый", 10, 0.5, 0, 1, 1),
            ("Популярный", 50, 2.0, 20, 1, 2),
            ("Профи", 100, 3.5, 30, 1, 3),
        ]
        for name, tokens, price, discount, is_active, sort_order in default_packages:
            cursor.execute("""
            INSERT INTO token_packages (name, tokens, price_ton, discount_percent, is_active, sort_order, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (name, tokens, price, discount, is_active, sort_order,
                  datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
        conn.commit()

    watchlist_defaults = [
        ("watchlist_enabled", "on"),
        ("watchlist_price_tokens", "5"),
        ("watchlist_limit_regular", "10"),
        ("watchlist_limit_vip", "50"),
        ("watchlist_extra_slots_price", "20"),
        ("watchlist_extra_slots_count", "5"),
        ("watchlist_probability_threshold", "10"),
        ("watchlist_closing_hours", "24"),
        ("watchlist_check_interval_hours", "3"),
        ("watchlist_token_billing_enabled", "on"),
        ("watchlist_probability_alert_cost_tokens", "5"),
        ("watchlist_closing_soon_cost_tokens", "3"),
        ("watchlist_resolved_recap_cost_tokens", "7"),
        ("watchlist_ai_deep_recap_cost_tokens", "10"),
        ("watchlist_ai_summary_enabled", "off"),
        ("watchlist_ai_summary_max_bullets", "3"),
    ]
    for key, value in watchlist_defaults:
        cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
        if not cursor.fetchone():
            cursor.execute("""
            INSERT INTO settings (key, value, updated_at)
            VALUES (%s, %s, %s)
            """, (key, value, datetime.utcnow().isoformat()))
    conn.commit()

    authors_defaults = [
        ("authors_enabled", "on"),
        ("donations_enabled", "on"),
        ("author_status_price_ton", "5"),
        ("platform_fee_percent", "20"),
        ("min_donation_ton", "0.1"),
        ("min_withdrawal_ton", "1"),
        ("max_posts_per_day", "5"),
    ]
    for key, value in authors_defaults:
        cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
        if not cursor.fetchone():
            cursor.execute("""
            INSERT INTO settings (key, value, updated_at)
            VALUES (%s, %s, %s)
            """, (key, value, datetime.utcnow().isoformat()))
    conn.commit()

    market_recap_defaults = [
        ("market_recap_enabled", "false"),
        ("market_recap_manual_enabled", "true"),
        ("market_recap_auto_enabled", "false"),
        ("market_recap_require_admin_approval", "true"),
        ("market_recap_times_per_day", "2"),
        ("market_recap_auto_times", "12:00,20:00"),
        ("market_recap_max_per_day", "2"),
        ("market_recap_language_mode", "user_language"),
        ("market_recap_min_volume", "0"),
        ("market_recap_send_to_all", "false"),
        ("market_recap_send_to_active_users", "true"),
        ("market_recap_categories", "all"),
    ]
    for key, value in market_recap_defaults:
        cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
        if not cursor.fetchone():
            cursor.execute("""
            INSERT INTO settings (key, value, updated_at)
            VALUES (%s, %s, %s)
            """, (key, value, datetime.utcnow().isoformat()))
    conn.commit()

    top_analysis_defaults = [
        ("top_analysis_enabled", "false"),
        ("top_analysis_price_tokens", "70"),
        ("top_analysis_research_enabled", "true"),
        ("top_analysis_chief_enabled", "true"),
        ("top_analysis_audit_enabled", "true"),
        ("top_analysis_social_enabled", "true"),
        ("top_analysis_timeout_sec", "120"),
    ]
    for key, value in top_analysis_defaults:
        cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
        if not cursor.fetchone():
            cursor.execute("""
            INSERT INTO settings (key, value, updated_at)
            VALUES (%s, %s, %s)
            """, (key, value, datetime.utcnow().isoformat()))
    conn.commit()
    polywar_defaults = [
        ("polywar_enabled", "true"),
        ("polywar_season_days", "30"),
        ("polywar_energy_max", "10"),
        ("polywar_energy_recharge_minutes", "60"),
        ("polywar_map_width", "32000"),
        ("polywar_map_height", "32000"),
        ("polywar_chunk_size", "64"),
        ("polywar_max_chunks_per_request", "9"),
        ("polywar_starting_area_size", "15"),
        ("polywar_mine_lock_minutes", "180"),
        ("polywar_mine_density_plain_bp", "400"),
        ("polywar_mine_density_forest_bp", "700"),
        ("polywar_mine_density_mountain_bp", "1000"),
        ("polywar_mine_density_swamp_bp", "900"),
        ("polywar_mine_density_desert_bp", "500"),
        ("polywar_mine_density_road_bp", "200"),
        ("polywar_mine_density_ruins_bp", "1400"),
        ("polywar_scan_3_energy_cost", "2"),
        ("polywar_scan_5_energy_cost", "4"),
        ("polywar_max_flags_per_player", "100"),
        ("polywar_enemy_attack_extra_energy", "1"),
        ("polywar_attack_progress_per_action", "50"),
        ("polywar_capture_progress_required", "100"),
        ("polywar_reinforce_energy_cost", "1"),
        ("polywar_reinforce_progress_per_action", "50"),
        ("polywar_sector_size", "100"),
        ("polywar_sector_min_claimed_cells", "25"),
        ("polywar_sector_control_percent", "60"),
        ("polywar_sector_influence_value", "100"),
        ("polywar_max_sectors_per_request", "100"),
        ("polywar_capital_siege_required", "1000"),
        ("polywar_capital_siege_progress_per_action", "100"),
        ("polywar_capital_siege_extra_energy", "2"),
        ("polywar_capital_repair_energy_cost", "2"),
        ("polywar_capital_repair_progress_per_action", "75"),
        ("polywar_capital_influence_value", "1000"),
        ("polywar_capital_order_duration_hours", "24"),
        ("polywar_capital_event_cooldown_seconds", "30"),
        ("polywar_commander_election_hours", "24"),
        ("polywar_commander_term_hours", "168"),
        ("polywar_commander_min_contribution", "5"),
        ("polywar_commander_min_members_for_election", "2"),
        ("polywar_commander_max_statement_length", "280"),
        ("polywar_commander_order_limit", "5"),
    ]
    for key, value in polywar_defaults:
        cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO settings (key, value, updated_at) VALUES (%s, %s, %s)", (key, value, datetime.utcnow().isoformat()))
    conn.commit()

    referral_defaults = [
        ("referral_rewards_enabled", "false"),
        ("referral_reward_percent", "10"),
        ("referral_reward_unlock_hours", "48"),
        ("referral_min_withdrawal_nano", str(1_000_000_000)),
        ("referral_daily_withdrawal_cap_nano", str(50_000_000_000)),
        ("referral_payout_wallet_enabled", "false"),
        ("referral_rewards_admin_approval_required", "false"),
        ("bot_moderation_mode_enabled", "false"),
        ("bot_moderation_tester_ids", ""),
    ]
    for key, value in referral_defaults:
        cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO settings (key, value, updated_at) VALUES (%s, %s, %s)", (key, value, datetime.utcnow().isoformat()))
    conn.commit()

# ═══════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════

def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
        row = cursor.fetchone()
        return row[0] if row else default
    except Exception:
        return default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO settings (key, value, updated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
        """, (key, value, datetime.utcnow().isoformat()))
        conn.commit()
    except Exception as e:
        print(f"set_setting error: {e}")
    finally:
        conn.close()


# ═══════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════

def ensure_user(user_id: int, username: str = "", first_name: str = "", referred_by: Optional[int] = None, source: str = "unknown") -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.utcnow().isoformat()
        incoming_referrer = int(referred_by) if referred_by else None
        cursor.execute("SELECT user_id, referred_by FROM users WHERE user_id = %s", (user_id,))
        existing = cursor.fetchone()

        if incoming_referrer == user_id:
            print(f"referral_ignored_self user_id={user_id}")
            incoming_referrer = None

        if existing:
            existing_referred_by = existing[1]
            cursor.execute("""
            UPDATE users SET username = %s, first_name = %s, updated_at = %s
            WHERE user_id = %s
            """, (username, first_name, now, user_id))

            if existing_referred_by:
                print(f"referral_preserved user_id={user_id} referred_by={existing_referred_by}")
                if incoming_referrer and incoming_referrer != existing_referred_by:
                    print(f"referral_ignored_existing user_id={user_id} existing_referred_by={existing_referred_by} incoming={incoming_referrer}")
            elif incoming_referrer:
                cursor.execute("""
                UPDATE users SET referred_by = %s WHERE user_id = %s
                """, (incoming_referrer, user_id))
                cursor.execute("""
                UPDATE users SET total_referrals = (
                    SELECT COUNT(*) FROM users u2 WHERE u2.referred_by = users.user_id
                )
                WHERE user_id = %s
                """, (incoming_referrer,))
                _insert_referral_relationship(cursor, incoming_referrer, user_id)
                print(f"referral_attached user_id={user_id} referred_by={incoming_referrer}")
        else:
            cursor.execute("""
            INSERT INTO users (user_id, username, first_name, referred_by, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """, (user_id, username, first_name, incoming_referrer, now, now))

            if incoming_referrer:
                cursor.execute("""
                UPDATE users SET total_referrals = (
                    SELECT COUNT(*) FROM users u2 WHERE u2.referred_by = users.user_id
                )
                WHERE user_id = %s
                """, (incoming_referrer,))
                _insert_referral_relationship(cursor, incoming_referrer, user_id)
                print(f"referral_attached user_id={user_id} referred_by={incoming_referrer}")

        conn.commit()
        print(f"user_registered_or_updated user_id={user_id} source={source}")
    except Exception as e:
        print(f"ensure_user error: {e}")
    finally:
        conn.close()


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"get_user error: {e}")
        return None
    finally:
        conn.close()


def get_all_users(limit: int = 1000) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT %s", (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_all_users error: {e}")
        return []
    finally:
        conn.close()


def get_users_page(limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (limit, offset),
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_users_page error: {e}")
        return []
    finally:
        conn.close()


def count_users() -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM users")
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        print(f"count_users error: {e}")
        return 0
    finally:
        conn.close()


def search_users(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        q = (query or "").strip()
        if not q:
            return []
        if q.isdigit():
            cursor.execute(
                "SELECT * FROM users WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                (int(q), limit),
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

        like_q = f"%{q}%"
        cursor.execute(
            """
            SELECT * FROM users
            WHERE COALESCE(username, '') ILIKE %s
               OR COALESCE(first_name, '') ILIKE %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (like_q, like_q, limit),
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"search_users error: {e}")
        return []
    finally:
        conn.close()


def _hash_session_token(raw_session_token: str) -> str:
    return hashlib.sha256(raw_session_token.encode("utf-8")).hexdigest()


def _hash_ip(ip: str) -> str:
    if not ip:
        return ""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


def create_web_session(user_id: int, provider: str, user_agent: str = "", ip: str = "") -> str:
    conn = get_connection()
    cursor = conn.cursor()
    raw_session_token = secrets.token_urlsafe(48)
    token_hash = _hash_session_token(raw_session_token)
    now = datetime.utcnow()
    expires = now + timedelta(days=30)
    try:
        cursor.execute("""
        INSERT INTO web_sessions
        (session_token_hash, user_id, provider, created_at, expires_at, last_seen_at, user_agent, ip_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token_hash, user_id, provider, now.isoformat(), expires.isoformat(),
            now.isoformat(), (user_agent or "")[:512], _hash_ip(ip),
        ))
        conn.commit()
    finally:
        conn.close()
    return raw_session_token


def get_user_by_session(raw_session_token: str) -> Optional[Dict[str, Any]]:
    if not raw_session_token:
        return None
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    token_hash = _hash_session_token(raw_session_token)
    now_iso = datetime.utcnow().isoformat()
    try:
        cursor.execute("""
        SELECT s.user_id, s.provider, s.expires_at, u.username, u.first_name,
               COALESCE(a.name, '') AS name,
               COALESCE(a.email, '') AS email
        FROM web_sessions s
        JOIN users u ON u.user_id = s.user_id
        LEFT JOIN web_accounts a ON a.user_id = s.user_id AND a.provider = s.provider
        WHERE s.session_token_hash = %s
        """, (token_hash,))
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        if data.get("expires_at") and data["expires_at"] < now_iso:
            cursor.execute("DELETE FROM web_sessions WHERE session_token_hash = %s", (token_hash,))
            conn.commit()
            return None
        cursor.execute(
            "UPDATE web_sessions SET last_seen_at = %s WHERE session_token_hash = %s",
            (now_iso, token_hash),
        )
        conn.commit()
        return data
    finally:
        conn.close()


def delete_web_session(raw_session_token: str) -> bool:
    if not raw_session_token:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM web_sessions WHERE session_token_hash = %s", (_hash_session_token(raw_session_token),))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def link_web_account(
    user_id: int,
    provider: str,
    provider_sub: str,
    email: str = "",
    name: str = "",
    avatar_url: str = "",
) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    try:
        cursor.execute("""
        INSERT INTO web_accounts
        (user_id, provider, provider_sub, email, name, avatar_url, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (provider, provider_sub)
        DO UPDATE SET user_id = EXCLUDED.user_id, email = EXCLUDED.email, name = EXCLUDED.name,
            avatar_url = EXCLUDED.avatar_url, updated_at = EXCLUDED.updated_at
        """, (user_id, provider, provider_sub, email, name, avatar_url, now, now))
        conn.commit()
    finally:
        conn.close()


def get_web_account(provider: str, provider_sub: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute(
            "SELECT * FROM web_accounts WHERE provider = %s AND provider_sub = %s",
            (provider, provider_sub),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def add_web_analysis_history(
    user_id: int,
    analysis_type: str,
    market_url: str,
    market_slug: str = "",
    question: str = "",
    display_prediction: str = "",
    market_probability: str = "",
    confidence: str = "",
    category: str = "",
    status: str = "success",
    result_json: Any = "",
    error: str = "",
) -> Optional[int]:
    conn = get_connection()
    cursor = conn.cursor()
    created_at = datetime.utcnow().isoformat()
    try:
        stored_result = ""
        if isinstance(result_json, (dict, list)):
            stored_result = json.dumps(result_json, ensure_ascii=False)
        elif result_json is not None:
            stored_result = str(result_json)
        cursor.execute("""
        INSERT INTO web_analysis_history
        (user_id, analysis_type, market_url, market_slug, question, display_prediction,
         market_probability, confidence, category, status, result_json, error, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """, (
            user_id, analysis_type, market_url, market_slug, question, display_prediction,
            market_probability, confidence, category, status, stored_result, error, created_at
        ))
        row = cursor.fetchone()
        conn.commit()
        return int(row[0]) if row else None
    except Exception as e:
        print(f"add_web_analysis_history error: {e}")
        return None
    finally:
        conn.close()


def get_web_analysis_history(user_id: int, limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    safe_limit = max(1, min(int(limit or 10), 30))
    safe_offset = max(0, int(offset or 0))
    try:
        cursor.execute("""
        SELECT id, analysis_type, market_url, market_slug, question,
               display_prediction, market_probability, confidence, category, status, created_at
        FROM web_analysis_history
        WHERE user_id = %s
        ORDER BY id DESC
        LIMIT %s OFFSET %s
        """, (user_id, safe_limit, safe_offset))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_web_analysis_history error: {e}")
        return []
    finally:
        conn.close()



def get_web_analysis_history_item(user_id: int, item_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT id, user_id, analysis_type, market_url, market_slug, question,
               display_prediction, market_probability, confidence, category, status,
               result_json, error, created_at
        FROM web_analysis_history
        WHERE user_id = %s AND id = %s
        LIMIT 1
        """, (user_id, item_id))
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        raw = data.get("result_json")
        if isinstance(raw, str) and raw:
            try:
                data["result"] = json.loads(raw)
            except Exception:
                data["result"] = {}
        elif isinstance(raw, dict):
            data["result"] = raw
        else:
            data["result"] = {}
        data.pop("result_json", None)
        data.pop("user_id", None)
        return data
    except Exception as e:
        print(f"get_web_analysis_history_item error: {e}")
        return None
    finally:
        conn.close()


def create_analysis_check(created_by_user_id, check_type, created_by_admin=False, max_activations=1, expires_at=None, require_channel_sub=False, required_channel="", unit_price_tokens=0, total_price_tokens=0) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    now = datetime.utcnow().isoformat()
    code = secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]
    try:
        cursor.execute("""
        INSERT INTO analysis_checks (code, created_by_user_id, created_by_admin, check_type, max_activations, used_activations, expires_at, require_channel_sub, required_channel, status, created_at, unit_price_tokens, total_price_tokens, refunded_tokens, disabled_at)
        VALUES (%s, %s, %s, %s, %s, 0, %s, %s, %s, 'active', %s, %s, %s, 0, NULL)
        RETURNING *
        """, (code, created_by_user_id, bool(created_by_admin), check_type, int(max_activations), expires_at, bool(require_channel_sub), required_channel or "", now, int(unit_price_tokens or 0), int(total_price_tokens or 0)))
        row = cursor.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception as e:
        conn.rollback()
        print(f"create_analysis_check error: {e}")
        return None
    finally:
        conn.close()


def get_analysis_check_by_code(code: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT * FROM analysis_checks WHERE code = %s LIMIT 1", (code,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_web_analysis_job(user_id: int, analysis_type: str, market_url: str) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    job_id = secrets.token_urlsafe(24)
    try:
        cursor.execute("""
        INSERT INTO web_analysis_jobs
        (job_id, user_id, analysis_type, market_url, status, progress, history_id, result_json, error, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (job_id, user_id, analysis_type, market_url, "queued", "", None, "", "", now, now))
        conn.commit()
        return job_id
    finally:
        conn.close()


def update_web_analysis_job(
    job_id: str,
    user_id: int,
    status: Optional[str] = None,
    progress: Optional[str] = None,
    history_id: Optional[int] = None,
    result_json: Any = None,
    error: Optional[str] = None,
) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    try:
        parts = ["updated_at = %s"]
        values: List[Any] = [now]
        if status is not None:
            parts.append("status = %s")
            values.append(str(status))
        if progress is not None:
            parts.append("progress = %s")
            values.append(str(progress))
        if history_id is not None:
            parts.append("history_id = %s")
            values.append(int(history_id))
        if result_json is not None:
            if isinstance(result_json, (dict, list)):
                stored = json.dumps(result_json, ensure_ascii=False)
            else:
                stored = str(result_json)
            parts.append("result_json = %s")
            values.append(stored)
        if error is not None:
            parts.append("error = %s")
            values.append(str(error))
        values.extend([job_id, user_id])
        cursor.execute(
            f"UPDATE web_analysis_jobs SET {', '.join(parts)} WHERE job_id = %s AND user_id = %s",
            tuple(values),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"update_web_analysis_job error: {e}")
        return False
    finally:
        conn.close()


def get_web_analysis_job(job_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT job_id, user_id, analysis_type, market_url, status, progress,
               history_id, result_json, error, created_at, updated_at
        FROM web_analysis_jobs
        WHERE job_id = %s AND user_id = %s
        LIMIT 1
        """, (job_id, user_id))
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        raw = data.get("result_json")
        if isinstance(raw, str) and raw:
            try:
                data["result"] = json.loads(raw)
            except Exception:
                data["result"] = {}
        elif isinstance(raw, dict):
            data["result"] = raw
        else:
            data["result"] = {}
        data.pop("result_json", None)
        data.pop("user_id", None)
        return data
    except Exception as e:
        print(f"get_web_analysis_job error: {e}")
        return None
    finally:
        conn.close()

def get_broadcast_users() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT user_id, is_banned, created_at, language
            FROM users
            WHERE user_id IS NOT NULL
            ORDER BY created_at DESC
        """)
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        print(f"get_broadcast_users error: {e}")
        return []
    finally:
        conn.close()


def get_all_user_ids() -> List[int]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT user_id FROM users ORDER BY created_at DESC")
        return [int(row[0]) for row in cursor.fetchall() if row and row[0] is not None]
    except Exception as e:
        print(f"get_all_user_ids error: {e}")
        return []
    finally:
        conn.close()


def is_user_banned(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user and user.get("is_banned"))


def is_user_vip(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user and user.get("is_vip"))


def set_user_ban(user_id: int, banned: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET is_banned = %s, updated_at = %s WHERE user_id = %s
        """, (1 if banned else 0, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"set_user_ban error: {e}")
    finally:
        conn.close()


def set_user_vip(user_id: int, vip: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET is_vip = %s, updated_at = %s WHERE user_id = %s
        """, (1 if vip else 0, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"set_user_vip error: {e}")
    finally:
        conn.close()


def add_tokens(user_id: int, amount: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET token_balance = token_balance + %s, updated_at = %s
        WHERE user_id = %s
        """, (amount, datetime.utcnow().isoformat(), user_id))
        conn.commit()
        cursor.execute("SELECT token_balance FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        print(f"add_tokens error: {e}")
        return 0
    finally:
        conn.close()


def set_tokens(user_id: int, amount: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET token_balance = %s, updated_at = %s WHERE user_id = %s
        """, (amount, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"set_tokens error: {e}")
    finally:
        conn.close()


def increment_user_stat(user_id: int, field: str) -> None:
    if field not in ("total_analyses", "total_opportunities"):
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
        UPDATE users SET {field} = {field} + 1, updated_at = %s WHERE user_id = %s
        """, (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"increment_user_stat error: {e}")
    finally:
        conn.close()



def create_ton_purchase_intent(user_id: int, product_type: str, wallet_address: str, project_wallet: str, expected_amount_nano: int, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    conn = get_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    now = datetime.utcnow().isoformat()
    try:
        cur.execute("""
        INSERT INTO ton_purchase_intents (user_id, product_type, wallet_address, project_wallet, expected_amount_nano, metadata_json,
            requested_tokens, bonus_tokens, total_tokens, price_per_token_nano, subscription_days, created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *
        """, (user_id, product_type, wallet_address, project_wallet, str(int(expected_amount_nano or 0)), json.dumps(metadata or {}, ensure_ascii=False),
              int((metadata or {}).get('requested_tokens') or 0), int((metadata or {}).get('bonus_tokens') or 0), int((metadata or {}).get('total_tokens') or 0), str((metadata or {}).get('price_per_token_nano') or '0'), int((metadata or {}).get('subscription_days') or 0), now, now))
        row = cur.fetchone(); conn.commit(); return dict(row) if row else None
    except Exception as e:
        print(f"create_ton_purchase_intent error: {e}"); return None
    finally:
        conn.close()

def submit_ton_purchase_intent(intent_id: int, tx_hash: str) -> Optional[Dict[str, Any]]:
    conn = get_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    now = datetime.utcnow().isoformat(); h = (tx_hash or '').strip()
    try:
        cur.execute("SELECT * FROM ton_purchase_intents WHERE id=%s FOR UPDATE", (intent_id,))
        row = cur.fetchone()
        if not row or row['status'] not in ('created','submitted'): conn.rollback(); return None
        if not h:
            cur.execute(
                "UPDATE ton_purchase_intents SET status='submitted', submitted_at=COALESCE(submitted_at,%s), updated_at=%s WHERE id=%s RETURNING *",
                (now, now, intent_id),
            )
            out = cur.fetchone()
            conn.commit()
            return dict(out) if out else None
        cur.execute("SELECT id FROM ton_purchase_intents WHERE tx_hash=%s AND id<>%s", (h, intent_id))
        if cur.fetchone(): conn.rollback(); return None
        cur.execute("UPDATE ton_purchase_intents SET status='submitted', tx_hash=%s, submitted_at=COALESCE(submitted_at,%s), updated_at=%s WHERE id=%s RETURNING *", (h, now, now, intent_id))
        out=cur.fetchone()
        cur.execute("UPDATE ton_wallet_transactions SET product_type=%s,payment_intent_id=%s,purchase_status='submitted',updated_at=%s WHERE tx_hash=%s", (row['product_type'], intent_id, now, h))
        conn.commit(); return dict(out) if out else None
    except Exception as e:
        print(f"submit_ton_purchase_intent error: {e}"); conn.rollback(); return None
    finally: conn.close()


def link_ton_wallet_tx_to_intent(tx_hash: str, intent_id: int, product_type: str = "token_purchase", purchase_status: str = "submitted") -> bool:
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    h = str(tx_hash or "").strip()
    if not h:
        conn.close()
        return False
    try:
        cur.execute(
            """
            UPDATE ton_wallet_transactions
            SET product_type=%s, payment_intent_id=%s, purchase_status=%s, updated_at=%s
            WHERE tx_hash=%s
            """,
            (str(product_type or "token_purchase"), int(intent_id), str(purchase_status or "submitted"), now, h),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        print(f"link_ton_wallet_tx_to_intent error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def fulfill_ton_purchase_intent(intent_id: int) -> Optional[Dict[str, Any]]:
    conn=get_connection(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); now=datetime.utcnow().isoformat()
    try:
        cur.execute("SELECT * FROM ton_purchase_intents WHERE id=%s FOR UPDATE", (intent_id,)); row=cur.fetchone()
        if not row:
            conn.rollback()
            return None
        if row['status'] == 'fulfilled':
            conn.commit()
            return dict(row)
        if row['status']!='submitted':
            conn.rollback()
            return None
        product_type = str(row.get('product_type') or '').strip()
        if product_type == 'token_purchase':
            credit = int(row.get('total_tokens') or row.get('requested_tokens') or 0)
            cur.execute(
                "UPDATE users SET token_balance = COALESCE(token_balance, 0) + %s, updated_at = %s WHERE user_id = %s",
                (credit, now, int(row['user_id']))
            )
        elif product_type == 'subscription':
            days = int(row.get('subscription_days') or 30)
            cur.execute("SELECT subscription_until FROM users WHERE user_id=%s FOR UPDATE", (int(row['user_id']),))
            urow = cur.fetchone()
            now_dt = datetime.utcnow()
            base = now_dt
            if urow and urow.get('subscription_until'):
                try:
                    current_dt = datetime.fromisoformat(str(urow.get('subscription_until')))
                    if current_dt > now_dt:
                        base = current_dt
                except Exception:
                    pass
            until = (base + timedelta(days=days)).isoformat()
            cur.execute("UPDATE users SET subscription_until=%s, updated_at=%s WHERE user_id=%s", (until, now, int(row['user_id'])))
        cur.execute("UPDATE ton_purchase_intents SET status='fulfilled', fulfilled_at=%s, updated_at=%s WHERE id=%s RETURNING *", (now, now, intent_id))
        out=cur.fetchone();
        cur.execute("UPDATE ton_wallet_transactions SET purchase_status='fulfilled',updated_at=%s WHERE payment_intent_id=%s", (now, intent_id))
        conn.commit(); return dict(out) if out else None
    except Exception as e:
        print(f"fulfill_ton_purchase_intent error: {e}"); conn.rollback(); return None
    finally: conn.close()

def fail_ton_purchase_intent(intent_id: int, reason: str) -> Optional[Dict[str, Any]]:
    conn=get_connection(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); now=datetime.utcnow().isoformat()
    try:
        cur.execute("SELECT * FROM ton_purchase_intents WHERE id=%s FOR UPDATE", (intent_id,)); row=cur.fetchone()
        if not row or row['status']=='fulfilled': conn.rollback(); return None
        cur.execute("UPDATE ton_purchase_intents SET status='failed', failed_at=%s, fail_reason=%s, updated_at=%s WHERE id=%s RETURNING *", (now, reason[:255], now, intent_id))
        out=cur.fetchone();
        cur.execute("UPDATE ton_wallet_transactions SET purchase_status='failed',updated_at=%s WHERE payment_intent_id=%s", (now, intent_id))
        conn.commit(); return dict(out) if out else None
    except Exception as e:
        print(f"fail_ton_purchase_intent error: {e}"); conn.rollback(); return None
    finally: conn.close()

# ═══════════════════════════════════════════
# REFERRALS
# ═══════════════════════════════════════════




def get_user_source_diagnostics() -> Dict[str, Any]:
    keywords = ("user", "referral", "ref", "profile", "balance", "transaction", "invite")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        tables = [t for t in _list_tables(cursor) if any(k in t.lower() for k in keywords)]
        other_user_like = []
        referral_like = []
        for table in tables:
            try:
                count = _count_table(cursor, table)
            except Exception:
                continue
            item = {"table": table, "count": count}
            low = table.lower()
            if table != "users" and any(k in low for k in ("user", "profile", "balance", "transaction")):
                other_user_like.append(item)
            if any(k in low for k in ("referral", "ref", "invite")):
                referral_like.append(item)
        return {
            "tables_checked": tables,
            "users_table_count": count_users(),
            "other_user_like_tables": other_user_like,
            "referral_like_tables": referral_like,
            "db_identifier_redacted": _db_identifier_redacted(),
        }
    except Exception as e:
        print(f"get_user_source_diagnostics error: {e}")
        return {"tables_checked": [], "users_table_count": count_users(), "other_user_like_tables": [], "referral_like_tables": [], "db_identifier_redacted": _db_identifier_redacted()}
    finally:
        conn.close()


def get_database_diagnostics() -> Dict[str, Any]:
    diag = get_user_source_diagnostics()
    return {
        "database": diag.get("db_identifier_redacted", "missing"),
        "users_count": diag.get("users_table_count", 0),
        "referrals_count": get_total_referral_relationships(),
    }


def get_total_referral_relationships() -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        rel = _referral_relationship_table(cursor)
        if rel:
            table, ref_col, user_col = rel
            cursor.execute(f"SELECT COUNT(DISTINCT {ref_col} || ':' || {user_col}) AS count FROM {table} WHERE {ref_col} IS NOT NULL AND {user_col} IS NOT NULL AND {ref_col} <> {user_col}")
        else:
            cursor.execute("SELECT COUNT(*) AS count FROM users WHERE referred_by IS NOT NULL")
        row = cursor.fetchone()
        return int(_first_scalar(row, 0) or 0)
    except Exception as e:
        print(f"get_total_referral_relationships error: {e}")
        return 0
    finally:
        conn.close()


def get_referral_diagnostics(user_id: int) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT user_id, username, first_name, total_referrals FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone() or {}
        if user and not isinstance(user, dict):
            user = dict(user)
        cursor.execute("SELECT COUNT(*) AS count FROM users WHERE referred_by = %s", (user_id,))
        users_count = int(_first_scalar(cursor.fetchone(), 0) or 0)
        legacy = int(user.get("total_referrals") or 0) if user else 0
        referral_table_count = 0
        source = "users.referred_by"
        rel = _referral_relationship_table(cursor)
        if rel:
            table, ref_col, user_col = rel
            cursor.execute(f"SELECT COUNT(DISTINCT {user_col}) AS count FROM {table} WHERE {ref_col} = %s AND {user_col} IS NOT NULL AND {user_col} <> {ref_col}", (user_id,))
            referral_table_count = int(_first_scalar(cursor.fetchone(), 0) or 0)
            if referral_table_count > 0:
                source = "referral_table"
        final = referral_table_count if source == "referral_table" else users_count
        if final == 0 and legacy > 0:
            final = legacy
            source = "legacy_total_referrals"
        refs = get_referrals(user_id)[:10] if source != "referral_table" else []
        mismatch = (legacy != users_count and legacy > 0) or (referral_table_count > 0 and referral_table_count != users_count)
        return {"user_id": user_id, "username": (user.get("username") if user else None), "users_referred_by_count": users_count, "legacy_total_referrals": legacy, "referral_table_count": referral_table_count, "final_referral_count": final, "source_used": source if (final or source) else "unknown", "referred_users": refs, "mismatch": bool(mismatch)}
    except Exception as e:
        print(f"get_referral_diagnostics error: {e}")
        return {"user_id": user_id, "username": None, "users_referred_by_count": 0, "legacy_total_referrals": 0, "referral_table_count": 0, "final_referral_count": 0, "source_used": "unknown", "referred_users": [], "mismatch": False, "error": str(e)}
    finally:
        conn.close()


def get_referral_count(user_id: int) -> int:
    d = get_referral_diagnostics(user_id)
    if d.get("mismatch"):
        print(f"referral_count_mismatch user_id={user_id} diagnostics={d}")
    return int(d.get("final_referral_count") or 0)


def sync_user_total_referrals(user_id: int) -> None:
    count = get_referral_count(user_id)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE users SET total_referrals = %s WHERE user_id = %s", (count, user_id))
        conn.commit()
    except Exception as e:
        print(f"sync_user_total_referrals error: {e}")
    finally:
        conn.close()


def sync_all_referral_counters() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT user_id FROM users")
        ids = [int(r[0]) for r in cursor.fetchall()]
        updated = 0
        cannot = 0
        skipped = 0
        errors = []
        for uid in ids:
            d = get_referral_diagnostics(uid)
            if d.get("error") or d.get("source_used") == "unknown":
                skipped += 1
                errors.append({"user_id": uid, "error": d.get("error", "unknown diagnostics source")})
                continue
            if d["source_used"] == "legacy_total_referrals" and d["users_referred_by_count"] == 0 and d["referral_table_count"] == 0:
                cannot += 1
                continue
            if int(d.get("legacy_total_referrals") or 0) > 0 and int(d.get("users_referred_by_count") or 0) == 0 and int(d.get("referral_table_count") or 0) == 0:
                cannot += 1
                continue
            cursor.execute("UPDATE users SET total_referrals = %s WHERE user_id = %s", (int(d["final_referral_count"]), uid))
            updated += 1
        conn.commit()
        result = {"updated": updated, "cannot_reconstruct": cannot, "skipped": skipped, "source": "referral_table" if _referral_relationship_table(cursor) else "users.referred_by"}
        if errors:
            result["warnings"] = errors
        return result
    except Exception as e:
        print(f"sync_all_referral_counters error: {e}")
        return {"updated": 0, "cannot_reconstruct": 0, "source": "unknown", "error": str(e)}
    finally:
        conn.close()


def get_referrals(user_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT user_id, username, first_name, total_analyses, created_at
        FROM users WHERE referred_by = %s ORDER BY created_at DESC
        """, (user_id,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_referrals error: {e}")
        return []
    finally:
        conn.close()


def get_top_referrers(limit: int = 10) -> List[Dict[str, Any]]:
    users = get_all_users(limit=10000)
    rows = []
    for u in users:
        d = get_referral_diagnostics(int(u["user_id"]))
        final = int(d.get("final_referral_count") or 0)
        legacy = int(d.get("legacy_total_referrals") or 0)
        if final > 0 or legacy > 0:
            row = dict(u)
            row["total_referrals"] = final
            row["source_used"] = d.get("source_used")
            row["legacy_mismatch"] = final == 0 and legacy > 0 or bool(d.get("mismatch"))
            rows.append(row)
    rows.sort(key=lambda r: (int(r.get("total_referrals") or 0), float(r.get("referral_earnings_ton") or 0)), reverse=True)
    return rows[:limit]



def get_referral_reward_settings() -> Dict[str, Any]:
    return {
        "enabled": str(get_setting("referral_rewards_enabled", "false")).lower() == "true",
        "reward_percent": float(str(get_setting("referral_reward_percent", "10") or "10").replace(",", ".")),
        "unlock_hours": int(str(get_setting("referral_reward_unlock_hours", "48") or "48")),
        "min_withdrawal_nano": int(str(get_setting("referral_min_withdrawal_nano", str(1_000_000_000)) or "1000000000")),
        "daily_withdrawal_cap_nano": int(str(get_setting("referral_daily_withdrawal_cap_nano", str(50_000_000_000)) or "50000000000")),
        "admin_approval_required": str(get_setting("referral_rewards_admin_approval_required", "false")).lower() == "true",
    }


def update_referral_reward_settings(**kwargs) -> None:
    for k, v in kwargs.items():
        set_setting(k, str(v))


def create_referral_reward(**kwargs) -> Optional[Dict[str, Any]]:
    conn = get_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO referral_rewards (user_id,source_user_id,purchase_type,purchase_ref,purchase_amount_nano,reward_percent,reward_nano,status,unlock_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT DO NOTHING
            RETURNING *
        """, (int(kwargs["user_id"]), int(kwargs["source_user_id"]), str(kwargs.get("purchase_type") or "token_purchase"),
              kwargs.get("purchase_ref"), int(kwargs.get("purchase_amount_nano") or 0), kwargs.get("reward_percent") or 0,
              int(kwargs.get("reward_nano") or 0), str(kwargs.get("status") or "pending"), kwargs.get("unlock_at")))
        row = cur.fetchone(); conn.commit(); return dict(row) if row else None
    except Exception as e:
        print(f"create_referral_reward error: {e}"); conn.rollback(); return None
    finally:
        conn.close()


def unlock_due_referral_rewards() -> int:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("UPDATE referral_rewards SET status='available', updated_at=NOW() WHERE status='pending' AND unlock_at IS NOT NULL AND unlock_at<=NOW()")
        n = cur.rowcount; conn.commit(); return int(n or 0)
    except Exception as e:
        print(f"unlock_due_referral_rewards error: {e}"); conn.rollback(); return 0
    finally:
        conn.close()


def get_user_referral_earnings_summary(user_id: int) -> Dict[str, Any]:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT
              COALESCE(SUM(reward_nano),0) FILTER (WHERE status IN ('pending','available','pending_admin_review','withdrawn')) AS total_earned_nano,
              COALESCE(SUM(reward_nano),0) FILTER (WHERE status='pending') AS pending_nano,
              COALESCE(SUM(reward_nano),0) FILTER (WHERE status='available') AS available_nano,
              COALESCE(SUM(reward_nano),0) FILTER (WHERE status='pending_admin_review') AS in_review_nano,
              COALESCE(SUM(reward_nano),0) FILTER (WHERE status='withdrawn') AS withdrawn_nano
            FROM referral_rewards WHERE user_id=%s
        """, (user_id,))
        row = cur.fetchone() or (0, 0, 0, 0, 0)
        return {
            "total_earned_nano": int(row[0] or 0),
            "pending_nano": int(row[1] or 0),
            "available_nano": int(row[2] or 0),
            "in_review_nano": int(row[3] or 0),
            "withdrawn_nano": int(row[4] or 0),
        }
    except Exception as e:
        print(f"get_user_referral_earnings_summary error: {e}"); return {"total_earned_nano": 0, "pending_nano": 0, "available_nano": 0, "in_review_nano": 0, "withdrawn_nano": 0}
    finally:
        conn.close()


def list_user_referral_rewards(user_id: int, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    conn = get_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM referral_rewards WHERE user_id=%s ORDER BY id DESC LIMIT %s OFFSET %s", (user_id, int(limit), int(offset)))
        return [dict(r) for r in (cur.fetchall() or [])]
    except Exception as e:
        print(f"list_user_referral_rewards error: {e}"); return []
    finally:
        conn.close()


def withdraw_available_referral_rewards_to_internal_wallet(user_id: int) -> Dict[str, Any]:
    settings = get_referral_reward_settings()
    conn = get_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT id, reward_nano FROM referral_rewards
            WHERE user_id=%s AND status='available'
            ORDER BY id ASC
            FOR UPDATE
        """, (user_id,))
        rows = cur.fetchall() or []
        available = sum(int(r["reward_nano"] or 0) for r in rows)
        if available < int(settings["min_withdrawal_nano"]):
            return {"ok": False, "error": "below_minimum", "available_nano": available, "minimum_nano": int(settings["min_withdrawal_nano"])}
        cur.execute("""
            SELECT COALESCE(SUM(reward_nano),0) AS total
            FROM referral_rewards
            WHERE user_id=%s AND status='withdrawn' AND withdrawn_at >= NOW() - INTERVAL '1 day'
        """, (user_id,))
        daily = int((cur.fetchone() or {}).get("total") or 0)
        if daily + available > int(settings["daily_withdrawal_cap_nano"]):
            return {"ok": False, "error": "daily_cap_exceeded", "available_nano": available}
        cur.execute("""
            SELECT id FROM referral_reward_withdrawal_requests
            WHERE user_id=%s AND status='pending'
            ORDER BY id DESC LIMIT 1
            FOR UPDATE
        """, (user_id,))
        existing = cur.fetchone()
        if existing:
            conn.rollback()
            return {"ok": False, "error": "pending_request_exists"}
        cur.execute("""
            INSERT INTO referral_reward_withdrawal_requests (user_id, amount_nano, status)
            VALUES (%s, %s, 'pending')
            RETURNING id
        """, (user_id, available))
        req = cur.fetchone() or {}
        reward_ids = [int(r["id"]) for r in rows]
        cur.execute("""
            UPDATE referral_rewards
            SET status='pending_admin_review', withdrawal_request_id=%s, updated_at=NOW()
            WHERE id = ANY(%s) AND status='available'
        """, (int(req.get("id") or 0), reward_ids))
        conn.commit()
        return {"ok": True, "mode": "manual_request", "amount_nano": available, "request_id": int(req.get("id") or 0)}
    except Exception as e:
        print(f"withdraw_available_referral_rewards_to_internal_wallet error: {e}"); conn.rollback(); return {"ok": False, "error": "withdraw_failed"}
    finally:
        conn.close()


def get_referral_reward_withdrawal_requests(status: str = "", limit: int = 50, statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    conn = get_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if statuses:
            clean_statuses = [str(s).strip() for s in statuses if str(s).strip()]
            if not clean_statuses:
                return []
            cur.execute("SELECT * FROM referral_reward_withdrawal_requests WHERE status = ANY(%s) ORDER BY id DESC LIMIT %s", (clean_statuses, int(limit)))
        elif status:
            cur.execute("SELECT * FROM referral_reward_withdrawal_requests WHERE status=%s ORDER BY id DESC LIMIT %s", (status, int(limit)))
        else:
            cur.execute("SELECT * FROM referral_reward_withdrawal_requests ORDER BY id DESC LIMIT %s", (int(limit),))
        return [dict(r) for r in (cur.fetchall() or [])]
    except Exception as e:
        print(f"get_referral_reward_withdrawal_requests error: {e}")
        return []
    finally:
        conn.close()


def get_referral_rewards_admin_stats() -> Dict[str, int]:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT
              COALESCE(SUM(reward_nano),0) FILTER (WHERE status='pending') AS pending_nano,
              COALESCE(SUM(reward_nano),0) FILTER (WHERE status='available') AS available_nano,
              COALESCE(SUM(reward_nano),0) FILTER (WHERE status='pending_admin_review') AS in_review_nano,
              COALESCE(SUM(reward_nano),0) FILTER (WHERE status='withdrawn') AS withdrawn_nano,
              COALESCE(COUNT(*),0) AS total_rewards_count,
              COALESCE(COUNT(DISTINCT user_id),0) AS total_referrers_count
            FROM referral_rewards
        """)
        row = cur.fetchone() or (0, 0, 0, 0, 0, 0)
        cur.execute("SELECT COALESCE(COUNT(*),0) FROM referral_reward_withdrawal_requests WHERE status='pending'")
        pending_requests = int((cur.fetchone() or [0])[0] or 0)
        return {
            "pending_nano": int(row[0] or 0),
            "available_nano": int(row[1] or 0),
            "in_review_nano": int(row[2] or 0),
            "withdrawn_nano": int(row[3] or 0),
            "pending_withdrawal_requests_count": pending_requests,
            "total_rewards_count": int(row[4] or 0),
            "total_referrers_count": int(row[5] or 0),
        }
    except Exception as e:
        print(f"get_referral_rewards_admin_stats error: {e}")
        return {"pending_nano": 0, "available_nano": 0, "in_review_nano": 0, "withdrawn_nano": 0, "pending_withdrawal_requests_count": 0, "total_rewards_count": 0, "total_referrers_count": 0}
    finally:
        conn.close()


def get_referral_withdrawal_request(request_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM referral_reward_withdrawal_requests WHERE id=%s LIMIT 1", (int(request_id),))
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"get_referral_withdrawal_request error: {e}")
        return None
    finally:
        conn.close()

def get_active_referral_payout_wallet() -> Optional[Dict[str, Any]]:
    conn = get_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM referral_payout_wallets WHERE status='active' ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"get_active_referral_payout_wallet error: {e}")
        return None
    finally:
        conn.close()


def get_active_cashier_payment_wallet() -> Optional[Dict[str, Any]]:
    conn = get_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM cashier_payment_wallets WHERE status='active' ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"get_active_cashier_payment_wallet error: {e}")
        return None
    finally:
        conn.close()


def create_referral_payout_wallet_record(wallet_address: str, seed_encrypted: str, network: str, admin_user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("UPDATE referral_payout_wallets SET status='inactive', updated_at=NOW() WHERE status='active'")
        cur.execute("""
            INSERT INTO referral_payout_wallets (wallet_address, seed_encrypted, network, status, created_by)
            VALUES (%s, %s, %s, 'active', %s)
            RETURNING *
        """, (wallet_address, seed_encrypted, network, int(admin_user_id)))
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception as e:
        print(f"create_referral_payout_wallet_record error: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def mark_referral_withdrawal_request_paid(request_id: int, admin_user_id: int, tx_hash: Optional[str] = None) -> bool:
    conn = get_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM referral_reward_withdrawal_requests WHERE id=%s FOR UPDATE", (int(request_id),))
        req = cur.fetchone()
        if not req or req["status"] != "pending":
            conn.rollback()
            return False
        final_tx_hash = tx_hash or "manual_admin_paid"
        cur.execute("""
            UPDATE referral_reward_withdrawal_requests
            SET status='paid', processed_at=NOW(), processed_by=%s, tx_hash=%s
            WHERE id=%s AND status='pending'
        """, (int(admin_user_id), final_tx_hash, int(request_id)))
        cur.execute("""
            UPDATE referral_rewards
            SET status='withdrawn', withdrawn_at=NOW(), withdrawal_tx_hash=%s, updated_at=NOW()
            WHERE withdrawal_request_id=%s AND status='pending_admin_review'
        """, (final_tx_hash, int(request_id)))
        conn.commit()
        return True
    except Exception as e:
        print(f"mark_referral_withdrawal_request_paid error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def reject_referral_withdrawal_request(request_id: int, admin_user_id: int, notes: Optional[str] = None) -> bool:
    conn = get_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM referral_reward_withdrawal_requests WHERE id=%s FOR UPDATE", (int(request_id),))
        req = cur.fetchone()
        if not req or req["status"] != "pending":
            conn.rollback()
            return False
        cur.execute("""
            UPDATE referral_reward_withdrawal_requests
            SET status='rejected', processed_at=NOW(), processed_by=%s, notes=%s
            WHERE id=%s AND status='pending'
        """, (int(admin_user_id), (notes or "")[:500], int(request_id)))
        cur.execute("""
            UPDATE referral_rewards
            SET status='available', withdrawal_request_id=NULL, updated_at=NOW()
            WHERE withdrawal_request_id=%s AND status='pending_admin_review'
        """, (int(request_id),))
        conn.commit()
        return True
    except Exception as e:
        print(f"reject_referral_withdrawal_request error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def add_referral_earnings(user_id: int, amount_ton: float) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET referral_earnings_ton = COALESCE(referral_earnings_ton, 0) + %s,
               updated_at = %s WHERE user_id = %s
        """, (amount_ton, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"add_referral_earnings error: {e}")
    finally:
        conn.close()


# ═══════════════════════════════════════════
# SUBSCRIPTIONS (платные — paid plan)
# ═══════════════════════════════════════════

def set_subscription(user_id: int, days: int = 30) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        current = get_subscription_until(user_id)
        now = datetime.utcnow()
        if current:
            try:
                current_dt = datetime.fromisoformat(current)
                if current_dt > now:
                    base = current_dt
                else:
                    base = now
            except Exception:
                base = now
        else:
            base = now
        until = (base + timedelta(days=days)).isoformat()
        cursor.execute("""
        UPDATE users SET subscription_until = %s, updated_at = %s WHERE user_id = %s
        """, (until, datetime.utcnow().isoformat(), user_id))
        conn.commit()
        return until
    except Exception as e:
        print(f"set_subscription error: {e}")
        return ""
    finally:
        conn.close()


def get_subscription_until(user_id: int) -> Optional[str]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT subscription_until FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None
    finally:
        conn.close()


def is_subscribed(user_id: int) -> bool:
    until = get_subscription_until(user_id)
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.utcnow()
    except Exception:
        return False


def get_subscribed_users() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        now = datetime.utcnow().isoformat()
        cursor.execute("""
        SELECT user_id, username, first_name FROM users
        WHERE subscription_until > %s
        """, (now,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_subscribed_users error: {e}")
        return []
    finally:
        conn.close()


# ═══════════════════════════════════════════
# DAILY LIMITS
# ═══════════════════════════════════════════

def _reset_daily_if_needed(user_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        cursor.execute("SELECT daily_reset_date FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        if not row or row[0] != today:
            cursor.execute("""
            UPDATE users SET daily_analyses = 0, daily_opportunities = 0,
                   daily_reset_date = %s WHERE user_id = %s
            """, (today, user_id))
            conn.commit()
    except Exception as e:
        print(f"_reset_daily_if_needed error: {e}")
    finally:
        conn.close()


def check_daily_limit(user_id: int, kind: str) -> bool:
    _reset_daily_if_needed(user_id)
    user = get_user(user_id)
    if not user:
        return False
    if kind == "analyses":
        limit = int(get_setting("sub_daily_analyses", "15"))
        used = user.get("daily_analyses", 0) or 0
    else:
        limit = int(get_setting("sub_daily_opportunities", "3"))
        used = user.get("daily_opportunities", 0) or 0
    return used < limit


def increment_daily(user_id: int, field: str) -> None:
    if field not in ("daily_analyses", "daily_opportunities"):
        return
    _reset_daily_if_needed(user_id)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
        UPDATE users SET {field} = {field} + 1, updated_at = %s WHERE user_id = %s
        """, (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"increment_daily error: {e}")
    finally:
        conn.close()


def get_daily_usage(user_id: int) -> Dict[str, int]:
    _reset_daily_if_needed(user_id)
    user = get_user(user_id)
    if not user:
        return {"analyses": 0, "opportunities": 0}
    return {
        "analyses": user.get("daily_analyses", 0) or 0,
        "opportunities": user.get("daily_opportunities", 0) or 0,
    }


# ═══════════════════════════════════════════
# FREE TRIAL
# ═══════════════════════════════════════════

def can_use_free_trial(user_id: int, kind: str) -> bool:
    if get_setting("free_trial_enabled", "on") != "on":
        return False
    user = get_user(user_id)
    if not user:
        return False
    if kind == "analyses":
        limit = int(get_setting("free_trial_analyses", "1"))
        used = user.get("free_analyses_used", 0) or 0
    else:
        limit = int(get_setting("free_trial_opportunities", "1"))
        used = user.get("free_opportunities_used", 0) or 0
    return used < limit


def use_free_trial(user_id: int, kind: str) -> None:
    field = "free_analyses_used" if kind == "analyses" else "free_opportunities_used"
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
        UPDATE users SET {field} = COALESCE({field}, 0) + 1, updated_at = %s
        WHERE user_id = %s
        """, (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"use_free_trial error: {e}")
    finally:
        conn.close()


def get_free_trial_status(user_id: int) -> Dict[str, int]:
    user = get_user(user_id)
    if not user:
        return {"analyses_used": 0, "analyses_limit": 0, "opportunities_used": 0, "opportunities_limit": 0}
    return {
        "analyses_used": user.get("free_analyses_used", 0) or 0,
        "analyses_limit": int(get_setting("free_trial_analyses", "1")),
        "opportunities_used": user.get("free_opportunities_used", 0) or 0,
        "opportunities_limit": int(get_setting("free_trial_opportunities", "1")),
    }


# ═══════════════════════════════════════════
# ANALYSES / OPPORTUNITIES
# ═══════════════════════════════════════════

def save_analysis(data: Dict[str, Any], user_id: int = 0) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    safe_user_id = int(user_id) if user_id else 0

    try:
        cursor.execute("""
        INSERT INTO analyses (
            url, question, category, market_probability, system_probability,
            confidence, reasoning, main_scenario, alt_scenario, conclusion,
            created_at, user_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """, (
            data.get("url", ""),
            data.get("question", ""),
            data.get("category", ""),
            data.get("market_probability", ""),
            data.get("probability", ""),
            data.get("confidence", ""),
            data.get("reasoning", ""),
            data.get("main_scenario", ""),
            data.get("alt_scenario", ""),
            data.get("conclusion", ""),
            datetime.utcnow().isoformat(),
            safe_user_id,
        ))

        row = cursor.fetchone()
        analysis_id = row[0] if row else 0

        conn.commit()
        return analysis_id

    except Exception as e:
        print(f"save_analysis error: {e}")
        return 0

    finally:
        conn.close()


def save_opportunity(data: Dict[str, Any], user_id: int = 0) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO opportunities (url, question, category, market_probability, system_probability,
                                    confidence, reasoning, main_scenario, alt_scenario, conclusion,
                                    opportunity_score, created_at, user_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """, (
            data.get("url", ""),
            data.get("question", ""),
            data.get("category", ""),
            data.get("market_probability", ""),
            data.get("probability", ""),
            data.get("confidence", ""),
            data.get("reasoning", ""),
            data.get("main_scenario", ""),
            data.get("alt_scenario", ""),
            data.get("conclusion", ""),
            data.get("opportunity_score", 0),
            datetime.utcnow().isoformat(),
            user_id,
        ))
        opp_id = cursor.fetchone()[0]
        conn.commit()
        return opp_id
    except Exception as e:
        print(f"save_opportunity error: {e}")
        return 0
    finally:
        conn.close()


def get_recent_analyses(limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT * FROM analyses ORDER BY created_at DESC LIMIT %s", (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_recent_analyses error: {e}")
        return []
    finally:
        conn.close()


def get_top_opportunities(limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT * FROM opportunities ORDER BY opportunity_score DESC LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_top_opportunities error: {e}")
        return []
    finally:
        conn.close()


def get_user_analyses(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT * FROM analyses WHERE user_id = %s ORDER BY created_at DESC LIMIT %s
        """, (user_id, limit))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_user_analyses error: {e}")
        return []
    finally:
        conn.close()


# ═══════════════════════════════════════════
# TRANSACTIONS / PAYMENTS
# ═══════════════════════════════════════════

def is_tx_processed(tx_hash: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM transactions WHERE tx_hash = %s", (tx_hash,))
        return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        conn.close()


def save_transaction(
    tx_hash: str, user_id: int, ton_amount: float, tokens_granted: int,
    referral_bonus_ton: float = 0, referrer_id: Optional[int] = None
) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO transactions (tx_hash, user_id, ton_amount, tokens_granted,
                                   referral_bonus_ton, referrer_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tx_hash) DO NOTHING
        """, (tx_hash, user_id, ton_amount, tokens_granted,
              referral_bonus_ton, referrer_id, datetime.utcnow().isoformat()))
        conn.commit()
    except Exception as e:
        print(f"save_transaction error: {e}")
    finally:
        conn.close()


def add_pending(user_id: int, amount: float, payment_type: str = "tokens") -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO pending_payments (user_id, amount, payment_type, created_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET amount = EXCLUDED.amount,
            payment_type = EXCLUDED.payment_type, created_at = EXCLUDED.created_at
        """, (user_id, amount, payment_type, int(time.time())))
        conn.commit()
    except Exception as e:
        print(f"add_pending error: {e}")
    finally:
        conn.close()


def get_all_pending() -> Dict[int, Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT user_id, amount, payment_type, created_at FROM pending_payments")
        rows = cursor.fetchall()
        return {
            int(r[0]): {
                "amount": float(r[1]),
                "payment_type": r[2] or "tokens",
                "timestamp": int(r[3]) if r[3] else 0,
            } for r in rows
        }
    except Exception as e:
        print(f"get_all_pending error: {e}")
        return {}
    finally:
        conn.close()


def delete_pending(user_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM pending_payments WHERE user_id = %s", (user_id,))
        conn.commit()
    except Exception as e:
        print(f"delete_pending error: {e}")
    finally:
        conn.close()


# ═══════════════════════════════════════════
# SIGNAL CACHE
# ═══════════════════════════════════════════

def save_signal_cache(category: str, data: Dict[str, Any]) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO signal_cache (category, data, updated_at) VALUES (%s, %s, %s)
        ON CONFLICT (category) DO UPDATE SET data = EXCLUDED.data, updated_at = EXCLUDED.updated_at
        """, (category, json.dumps(data), int(time.time())))
        conn.commit()
    except Exception as e:
        print(f"save_signal_cache error: {e}")
    finally:
        conn.close()


def get_signal_cache(category: str, max_age_seconds: int = 7200) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT data, updated_at FROM signal_cache WHERE category = %s", (category,))
        row = cursor.fetchone()
        if not row:
            return None
        age = int(time.time()) - int(row[1] or 0)
        if age > max_age_seconds:
            return None
        return json.loads(row[0])
    except Exception as e:
        print(f"get_signal_cache error: {e}")
        return None
    finally:
        conn.close()


def get_all_cache_status() -> Dict[str, Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    result = {}
    try:
        cursor.execute("SELECT category, updated_at FROM signal_cache")
        rows = cursor.fetchall()
        now = int(time.time())
        for r in rows:
            cat = r[0]
            updated = int(r[1] or 0)
            age_seconds = now - updated
            result[cat] = {
                "age_minutes": age_seconds // 60,
                "is_fresh": age_seconds < 3600,
                "updated_at": updated,
            }
    except Exception as e:
        print(f"get_all_cache_status error: {e}")
    finally:
        conn.close()
    return result


# ═══════════════════════════════════════════
# SIGNAL HISTORY
# ═══════════════════════════════════════════

def add_to_signal_history(user_id: int, question: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO signal_history (user_id, question, created_at)
        VALUES (%s, %s, %s)
        """, (user_id, question, datetime.utcnow().isoformat()))
        conn.commit()
    except Exception as e:
        print(f"add_to_signal_history error: {e}")
    finally:
        conn.close()


def get_signal_history(user_id: int, limit: int = 50) -> List[str]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT question FROM signal_history WHERE user_id = %s
        ORDER BY created_at DESC LIMIT %s
        """, (user_id, limit))
        rows = cursor.fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        print(f"get_signal_history error: {e}")
        return []
    finally:
        conn.close()


# ═══════════════════════════════════════════
# TOKEN PACKAGES
# ═══════════════════════════════════════════

def get_token_packages(active_only: bool = True) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if active_only:
            cursor.execute("""
            SELECT * FROM token_packages WHERE is_active = 1 ORDER BY sort_order, id
            """)
        else:
            cursor.execute("SELECT * FROM token_packages ORDER BY sort_order, id")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_token_packages error: {e}")
        return []
    finally:
        conn.close()


def get_token_package(package_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT * FROM token_packages WHERE id = %s", (package_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def create_token_package(name: str, tokens: int, price_ton: float, discount_percent: int = 0) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.utcnow().isoformat()
        cursor.execute("""
        INSERT INTO token_packages (name, tokens, price_ton, discount_percent, is_active, sort_order, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 1, 99, %s, %s)
        RETURNING id
        """, (name, tokens, price_ton, discount_percent, now, now))
        pid = cursor.fetchone()[0]
        conn.commit()
        return pid
    except Exception as e:
        print(f"create_token_package error: {e}")
        return 0
    finally:
        conn.close()


def update_token_package(package_id: int, name: str, tokens: int, price_ton: float,
                          discount_percent: int, is_active: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE token_packages SET name = %s, tokens = %s, price_ton = %s,
               discount_percent = %s, is_active = %s, updated_at = %s
        WHERE id = %s
        """, (name, tokens, price_ton, discount_percent,
              1 if is_active else 0, datetime.utcnow().isoformat(), package_id))
        conn.commit()
    except Exception as e:
        print(f"update_token_package error: {e}")
    finally:
        conn.close()


def delete_token_package(package_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM token_packages WHERE id = %s", (package_id,))
        conn.commit()
    except Exception as e:
        print(f"delete_token_package error: {e}")
    finally:
        conn.close()


def find_package_by_amount(ton_amount: float, tolerance: float = 0.05) -> Optional[Dict[str, Any]]:
    packages = get_token_packages(active_only=True)
    for p in packages:
        if abs(p["price_ton"] - ton_amount) <= tolerance:
            return p
    return None


# ═══════════════════════════════════════════
# PREDICTIONS TRACKING
# ═══════════════════════════════════════════

def save_prediction(data: Dict[str, Any]) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO predictions_tracking (
            user_id, market_slug, market_url, question, category,
            market_type, semantic_type,
            market_probability_yes, market_probability_no,
            market_leader, market_prob_value,
            system_prediction, system_probability, system_outcome,
            confidence, delta, alpha_label, market_balance,
            display_prediction, created_at, market_end_date
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """, (
            data.get("user_id", 0),
            data.get("market_slug", ""),
            data.get("market_url", ""),
            data.get("question", ""),
            data.get("category", ""),
            data.get("market_type", ""),
            data.get("semantic_type", ""),
            data.get("market_probability_yes"),
            data.get("market_probability_no"),
            data.get("market_leader", ""),
            data.get("market_prob_value"),
            data.get("system_prediction", ""),
            data.get("system_probability"),
            data.get("system_outcome", ""),
            data.get("confidence", ""),
            data.get("delta"),
            data.get("alpha_label", ""),
            data.get("market_balance", ""),
            data.get("display_prediction", ""),
            datetime.utcnow().isoformat(),
            data.get("market_end_date"),
        ))
        pid = cursor.fetchone()[0]
        conn.commit()
        return pid
    except Exception as e:
        print(f"save_prediction error: {e}")
        return 0
    finally:
        conn.close()


def get_unresolved_predictions(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT * FROM predictions_tracking
        WHERE resolved_at IS NULL
        ORDER BY created_at ASC LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_unresolved_predictions error: {e}")
        return []
    finally:
        conn.close()


def update_resolution(prediction_id: int, actual_outcome: str, is_correct: bool,
                      brier_score: float, log_loss: float) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE predictions_tracking SET
            resolved_at = %s, actual_outcome = %s,
            is_correct = %s, brier_score = %s, log_loss = %s
        WHERE id = %s
        """, (datetime.utcnow().isoformat(), actual_outcome,
              1 if is_correct else 0, brier_score, log_loss, prediction_id))
        conn.commit()
    except Exception as e:
        print(f"update_resolution error: {e}")
    finally:
        conn.close()


def get_accuracy_stats() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT COUNT(*), SUM(is_correct), AVG(brier_score), AVG(log_loss)
        FROM predictions_tracking WHERE resolved_at IS NOT NULL
        """)
        row = cursor.fetchone()
        total = row[0] or 0
        correct = row[1] or 0
        avg_brier = row[2]
        avg_log_loss = row[3]
        accuracy = (correct / total * 100) if total > 0 else 0

        def _breakdown(field: str) -> Dict[str, Dict[str, Any]]:
            cursor.execute(f"""
            SELECT {field}, COUNT(*), SUM(is_correct), AVG(brier_score)
            FROM predictions_tracking
            WHERE resolved_at IS NOT NULL AND {field} IS NOT NULL
            GROUP BY {field}
            """)
            result = {}
            for r in cursor.fetchall():
                name = r[0] or "unknown"
                t = r[1] or 0
                c = r[2] or 0
                result[name] = {
                    "total": t,
                    "correct": c,
                    "accuracy": (c / t * 100) if t > 0 else 0,
                    "avg_brier": r[3],
                }
            return result

        by_confidence = _breakdown("confidence")
        by_type = _breakdown("market_type")
        by_alpha = _breakdown("alpha_label")
        by_category = _breakdown("category")

        return {
            "total": total,
            "correct": correct,
            "accuracy": accuracy,
            "avg_brier": avg_brier,
            "avg_log_loss": avg_log_loss,
            "by_confidence": by_confidence,
            "by_type": by_type,
            "by_alpha": by_alpha,
            "by_category": by_category,
        }
    except Exception as e:
        print(f"get_accuracy_stats error: {e}")
        return {"total": 0, "correct": 0, "accuracy": 0,
                "avg_brier": None, "avg_log_loss": None,
                "by_confidence": {}, "by_type": {},
                "by_alpha": {}, "by_category": {}}
    finally:
        conn.close()


# ═══════════════════════════════════════════
# AUTHOR PROFILE
# ═══════════════════════════════════════════

def set_author_status(user_id: int, is_author_flag: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.utcnow().isoformat()
        if is_author_flag:
            cursor.execute("""
            UPDATE users SET is_author = 1,
                   author_since = COALESCE(author_since, %s),
                   updated_at = %s WHERE user_id = %s
            """, (now, now, user_id))
        else:
            cursor.execute("""
            UPDATE users SET is_author = 0, updated_at = %s WHERE user_id = %s
            """, (now, user_id))
        conn.commit()
    except Exception as e:
        print(f"set_author_status error: {e}")
    finally:
        conn.close()


def is_author(user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT is_author FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return bool(row[0]) if row and row[0] else False
    except Exception:
        return False
    finally:
        conn.close()


def get_author_profile(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT user_id, username, first_name, is_author, author_balance_ton,
               author_withdrawn_ton, author_bio, author_since, ton_wallet,
               total_analyses, total_opportunities, total_subscribers, total_posts
        FROM users WHERE user_id = %s
        """, (user_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "user_id": row[0],
            "username": row[1],
            "first_name": row[2],
            "is_author": bool(row[3]) if row[3] else False,
            "author_balance_ton": row[4] or 0,
            "author_withdrawn_ton": row[5] or 0,
            "author_bio": row[6] or "",
            "author_since": row[7],
            "ton_wallet": row[8] or "",
            "total_analyses": row[9] or 0,
            "total_opportunities": row[10] or 0,
            "total_subscribers": row[11] or 0,
            "total_posts": row[12] or 0,
        }
    except Exception as e:
        print(f"get_author_profile error: {e}")
        return None
    finally:
        conn.close()


def set_author_bio(user_id: int, bio: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET author_bio = %s, updated_at = %s WHERE user_id = %s
        """, (bio, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"set_author_bio error: {e}")
    finally:
        conn.close()


def set_ton_wallet(user_id: int, wallet: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET ton_wallet = %s, updated_at = %s WHERE user_id = %s
        """, (wallet, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"set_ton_wallet error: {e}")
    finally:
        conn.close()


def add_author_balance(user_id: int, amount_ton: float) -> float:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET author_balance_ton = author_balance_ton + %s,
               updated_at = %s WHERE user_id = %s
        """, (amount_ton, datetime.utcnow().isoformat(), user_id))
        conn.commit()
        cursor.execute("SELECT author_balance_ton FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return float(row[0]) if row else 0.0
    except Exception as e:
        print(f"add_author_balance error: {e}")
        return 0.0
    finally:
        conn.close()


def withdraw_author_balance(user_id: int, amount_ton: float) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET
            author_balance_ton = author_balance_ton - %s,
            author_withdrawn_ton = author_withdrawn_ton + %s,
            updated_at = %s
        WHERE user_id = %s AND author_balance_ton >= %s
        """, (amount_ton, amount_ton, datetime.utcnow().isoformat(), user_id, amount_ton))
        success = cursor.rowcount > 0
        conn.commit()
        return success
    except Exception as e:
        print(f"withdraw_author_balance error: {e}")
        return False
    finally:
        conn.close()


def get_all_authors(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT user_id, username, first_name, author_balance_ton,
               author_withdrawn_ton, author_since, total_analyses,
               total_subscribers, total_posts, author_bio
        FROM users WHERE is_author = 1
        ORDER BY (author_balance_ton + author_withdrawn_ton) DESC, total_subscribers DESC
        LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [{
            "user_id": r[0], "username": r[1], "first_name": r[2],
            "author_balance_ton": r[3] or 0,
            "author_withdrawn_ton": r[4] or 0,
            "author_since": r[5],
            "total_analyses": r[6] or 0,
            "total_subscribers": r[7] or 0,
            "total_posts": r[8] or 0,
            "author_bio": r[9] or "",
        } for r in rows]
    except Exception as e:
        print(f"get_all_authors error: {e}")
        return []
    finally:
        conn.close()


def get_top_authors_by_donations(limit: int = 10) -> List[Dict[str, Any]]:
    """Топ авторов по сумме полученных донатов."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT user_id, username, first_name,
               (author_balance_ton + author_withdrawn_ton) as total_earned,
               total_subscribers
        FROM users WHERE is_author = 1
          AND (author_balance_ton + author_withdrawn_ton) > 0
        ORDER BY total_earned DESC LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [{
            "user_id": r[0], "username": r[1], "first_name": r[2],
            "total_earned": r[3] or 0,
            "total_subscribers": r[4] or 0,
        } for r in rows]
    except Exception as e:
        print(f"get_top_authors_by_donations error: {e}")
        return []
    finally:
        conn.close()


# ═══════════════════════════════════════════
# AUTHOR POSTS (публикации прогнозов)
# ═══════════════════════════════════════════

def _reset_posts_today_if_needed(author_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        cursor.execute("SELECT posts_reset_date FROM users WHERE user_id = %s", (author_id,))
        row = cursor.fetchone()
        if not row or row[0] != today:
            cursor.execute("""
            UPDATE users SET posts_today = 0, posts_reset_date = %s
            WHERE user_id = %s
            """, (today, author_id))
            conn.commit()
    except Exception as e:
        print(f"_reset_posts_today_if_needed error: {e}")
    finally:
        conn.close()


def can_author_post_today(author_id: int) -> bool:
    """Проверяет не превышен ли дневной лимит публикаций."""
    _reset_posts_today_if_needed(author_id)
    user = get_user(author_id)
    if not user:
        return False
    max_per_day = int(get_setting("max_posts_per_day", "5"))
    posts_today = user.get("posts_today", 0) or 0
    return posts_today < max_per_day


def create_author_post(
    author_id: int,
    market_slug: str,
    market_url: str,
    question: str,
    category: str,
    display_prediction: str,
    confidence: str,
    market_probability: str,
    alpha_label: str,
    author_comment: str,
    full_analysis: Dict[str, Any],
) -> Optional[int]:
    """
    Создаёт пост автора (публикует анализ как прогноз).
    Возвращает id поста или None.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.utcnow().isoformat()
        cursor.execute("""
        INSERT INTO author_posts (
            author_id, market_slug, market_url, question, category,
            display_prediction, confidence, market_probability,
            alpha_label, author_comment, full_analysis_json,
            created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """, (
            author_id, market_slug, market_url, question, category,
            display_prediction, confidence, market_probability,
            alpha_label, author_comment, json.dumps(full_analysis),
            now,
        ))
        post_id = cursor.fetchone()[0]

        cursor.execute("""
        UPDATE users SET
            total_posts = COALESCE(total_posts, 0) + 1,
            posts_today = COALESCE(posts_today, 0) + 1,
            updated_at = %s
        WHERE user_id = %s
        """, (now, author_id))

        conn.commit()
        return post_id
    except Exception as e:
        print(f"create_author_post error: {e}")
        return None
    finally:
        conn.close()


def get_author_post(post_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT * FROM author_posts WHERE id = %s AND is_deleted = 0 AND COALESCE(status, 'published') = 'published'
        """, (post_id,))
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        if data.get("full_analysis_json"):
            try:
                data["full_analysis"] = json.loads(data["full_analysis_json"])
            except Exception:
                data["full_analysis"] = {}
        return data
    except Exception as e:
        print(f"get_author_post error: {e}")
        return None
    finally:
        conn.close()


def get_author_posts(author_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """Список постов автора."""
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT id, author_id, market_slug, market_url, question, category,
               display_prediction, confidence, market_probability, alpha_label, author_comment,
               title, body_text, event_question, article_type, thesis, reasoning, probability_view,
               risks, conclusion, cover_image_file_id, cover_image_url, attached_analysis_json,
               article_tags, article_language, published_to_profile, source_type, status, shares_count,
               views_count, unique_views_count, total_donations_ton, total_donors, created_at
        FROM author_posts
        WHERE author_id = %s AND is_deleted = 0
          AND COALESCE(status, 'published') = 'published'
          AND COALESCE(published_to_profile, 1) = 1
        ORDER BY created_at DESC LIMIT %s
        """, (author_id, limit))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_author_posts error: {e}")
        return []
    finally:
        conn.close()


def delete_author_post(post_id: int, author_id: int) -> bool:
    """Мягкое удаление — меняет флаг is_deleted."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE author_posts SET is_deleted = 1
        WHERE id = %s AND author_id = %s
        """, (post_id, author_id))
        success = cursor.rowcount > 0

        if success:
            cursor.execute("""
            UPDATE users SET total_posts = GREATEST(COALESCE(total_posts, 0) - 1, 0)
            WHERE user_id = %s
            """, (author_id,))

        conn.commit()
        return success
    except Exception as e:
        print(f"delete_author_post error: {e}")
        return False
    finally:
        conn.close()


def update_post_donations(post_id: int, ton_amount: float, is_new_donor: bool) -> None:
    """Обновляет счётчики доната у поста."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if is_new_donor:
            cursor.execute("""
            UPDATE author_posts SET
                total_donations_ton = total_donations_ton + %s,
                total_donors = total_donors + 1
            WHERE id = %s
            """, (ton_amount, post_id))
        else:
            cursor.execute("""
            UPDATE author_posts SET
                total_donations_ton = total_donations_ton + %s
            WHERE id = %s
            """, (ton_amount, post_id))
        conn.commit()
    except Exception as e:
        print(f"update_post_donations error: {e}")
    finally:
        conn.close()

def create_event_article(author_id: int, article: Dict[str, Any]) -> Optional[int]:
    """Publishes an Event Article using the existing author_posts/donation model."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.utcnow().isoformat()
        cursor.execute("""
        INSERT INTO author_posts (
            author_id, market_slug, market_url, question, category,
            display_prediction, confidence, market_probability,
            alpha_label, author_comment, full_analysis_json,
            title, event_question, article_type, thesis, reasoning,
            probability_view, risks, conclusion, source_type, source_ref_id,
            status, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """, (
            author_id,
            article.get("market_slug", ""), article.get("market_url", ""),
            article.get("event_question") or article.get("title", ""),
            article.get("article_type", "event_analysis"),
            article.get("thesis", ""), "", article.get("probability_view", ""),
            "", "", json.dumps(article.get("full_analysis") or article),
            article.get("title", ""), article.get("event_question", ""),
            article.get("article_type", "event_analysis"), article.get("thesis", ""),
            article.get("reasoning", ""), article.get("probability_view", ""),
            article.get("risks", ""), article.get("conclusion", ""),
            article.get("source_type", "manual"), article.get("source_ref_id"),
            article.get("status", "published"), now, now,
        ))
        post_id = cursor.fetchone()[0]
        cursor.execute("""
        UPDATE users SET total_posts = COALESCE(total_posts, 0) + 1,
            posts_today = COALESCE(posts_today, 0) + 1, updated_at = %s
        WHERE user_id = %s
        """, (now, author_id))
        conn.commit()
        return post_id
    except Exception as e:
        print(f"create_event_article error: {e}")
        return None
    finally:
        conn.close()



def create_manual_article(author_id: int, article_payload: Dict[str, Any]) -> Optional[int]:
    """Publish a standalone manual article in author_posts."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        from services.event_article_service import sanitize_article_text
        now = datetime.utcnow().isoformat()
        title = sanitize_article_text(article_payload.get("title", ""))[:160]
        body_text = sanitize_article_text(article_payload.get("body_text", ""))[:12000]
        if not title or not body_text:
            conn.rollback()
            return None
        attached = article_payload.get("attached_analysis") or article_payload.get("attached_analysis_json")
        if isinstance(attached, (dict, list)):
            attached_json = json.dumps(attached, ensure_ascii=False)
        else:
            attached_json = attached or None
        full = attached_json or json.dumps(article_payload, ensure_ascii=False)
        cursor.execute("""
        INSERT INTO author_posts (
            author_id, market_slug, market_url, question, category, display_prediction,
            confidence, market_probability, alpha_label, author_comment, full_analysis_json,
            title, event_question, article_type, thesis, reasoning, probability_view, risks,
            conclusion, source_type, source_ref_id, status, created_at, updated_at, body_text,
            cover_image_file_id, cover_image_url, attached_analysis_json, article_tags,
            article_language, published_to_profile
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
        """, (
            author_id, article_payload.get("market_slug", ""), article_payload.get("market_url", ""),
            title, article_payload.get("category", "manual"),
            article_payload.get("thesis", ""), "", "", "", "", full,
            title, article_payload.get("event_question", ""),
            article_payload.get("article_type", "manual"), article_payload.get("thesis", ""),
            article_payload.get("reasoning", ""), article_payload.get("probability_view", ""),
            article_payload.get("risks", ""), article_payload.get("conclusion", ""),
            article_payload.get("source_type", "manual_editor"), article_payload.get("source_ref_id"),
            article_payload.get("status", "published"), now, now, body_text,
            article_payload.get("cover_image_file_id"), article_payload.get("cover_image_url"), attached_json,
            article_payload.get("article_tags", ""), article_payload.get("article_language", "en"),
            int(article_payload.get("published_to_profile", 1)),
        ))
        post_id = cursor.fetchone()[0]
        cursor.execute("""
        UPDATE users SET total_posts = COALESCE(total_posts, 0) + 1,
            posts_today = COALESCE(posts_today, 0) + 1, updated_at = %s
        WHERE user_id = %s
        """, (now, author_id))
        conn.commit()
        return post_id
    except Exception as e:
        conn.rollback()
        print(f"create_manual_article error: {e}")
        return None
    finally:
        conn.close()


def update_article_fields(post_id: int, author_id: int, fields: Dict[str, Any]) -> bool:
    allowed = {"title", "body_text", "market_url", "cover_image_file_id", "cover_image_url", "attached_analysis_json", "article_tags", "article_language", "status"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if "attached_analysis_json" in updates and isinstance(updates["attached_analysis_json"], (dict, list)):
        updates["attached_analysis_json"] = json.dumps(updates["attached_analysis_json"], ensure_ascii=False)
    if not updates:
        return False
    conn = get_connection(); cursor = conn.cursor()
    try:
        parts = [f"{k} = %s" for k in updates]
        values = list(updates.values()) + [datetime.utcnow().isoformat(), post_id, author_id]
        cursor.execute(f"UPDATE author_posts SET {', '.join(parts)}, updated_at = %s WHERE id = %s AND author_id = %s", values)
        ok = cursor.rowcount > 0
        conn.commit(); return ok
    except Exception as e:
        conn.rollback()
        print(f"update_article_fields error: {e}"); return False
    finally:
        conn.close()


def _article_row_to_dict(row):
    data = dict(row)
    for key in ("full_analysis_json", "attached_analysis_json"):
        if data.get(key):
            try: data[key.replace("_json", "")] = json.loads(data[key])
            except Exception: pass
    return data


def list_public_articles(limit: int = 20, offset: int = 0, category=None, tag=None, author_id=None, search=None, sort: str = "new") -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 50))
    offset = max(0, int(offset or 0))
    conn = get_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        where = ["p.is_deleted = 0", "COALESCE(p.status, 'published') = 'published'", "(COALESCE(p.title,'') <> '' OR COALESCE(p.body_text,'') <> '' OR COALESCE(p.thesis,'') <> '')"]
        params = []
        if category and category not in ("all", "new", "popular"):
            where.append("(LOWER(COALESCE(p.category,'')) = LOWER(%s) OR LOWER(COALESCE(p.article_type,'')) = LOWER(%s))"); params += [category, category]
        if tag:
            where.append("LOWER(COALESCE(p.article_tags,'')) LIKE LOWER(%s)"); params.append(f"%{tag}%")
        if author_id:
            where.append("p.author_id = %s"); params.append(int(author_id))
        if search:
            where.append("(LOWER(COALESCE(p.title,'')) LIKE LOWER(%s) OR LOWER(COALESCE(p.body_text,'')) LIKE LOWER(%s) OR LOWER(COALESCE(p.thesis,'')) LIKE LOWER(%s))"); params += [f"%{search}%"]*3
        order = {"popular": "COALESCE(p.views_count,0) DESC", "donations": "COALESCE(p.total_donations_ton,0) DESC", "shares": "COALESCE(p.shares_count,0) DESC"}.get(sort, "p.created_at DESC")
        cursor.execute(f"""SELECT p.*, u.username AS author_username, u.first_name AS author_first_name FROM author_posts p LEFT JOIN users u ON u.user_id=p.author_id WHERE {' AND '.join(where)} ORDER BY {order} LIMIT %s OFFSET %s""", params + [limit, offset])
        return [_article_row_to_dict(r) for r in cursor.fetchall()]
    except Exception as e:
        print(f"list_public_articles error: {e}"); return []
    finally:
        conn.close()


def get_public_article(post_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection(); cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""SELECT p.*, u.username AS author_username, u.first_name AS author_first_name FROM author_posts p LEFT JOIN users u ON u.user_id=p.author_id WHERE p.id=%s AND p.is_deleted=0 AND COALESCE(p.status,'published')='published'""", (post_id,))
        row=cursor.fetchone(); return _article_row_to_dict(row) if row else None
    except Exception as e:
        print(f"get_public_article error: {e}"); return None
    finally:
        conn.close()

def increment_post_share(post_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE author_posts SET shares_count = COALESCE(shares_count, 0) + 1,
            updated_at = %s
        WHERE id = %s AND is_deleted = 0 AND COALESCE(status, 'published') = 'published'
        """, (datetime.utcnow().isoformat(), post_id))
        ok = cursor.rowcount > 0
        conn.commit()
        return ok
    except Exception as e:
        print(f"increment_post_share error: {e}")
        return False
    finally:
        conn.close()


# ═══════════════════════════════════════════
# AUTHOR SUBSCRIPTIONS (бесплатные)
# ═══════════════════════════════════════════

def subscribe_to_author(subscriber_id: int, author_id: int) -> bool:
    """Подписка на автора (бесплатная). Возвращает True если подписка создана."""
    if subscriber_id == author_id:
        return False

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO author_subscriptions (subscriber_id, author_id, created_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (subscriber_id, author_id) DO NOTHING
        """, (subscriber_id, author_id, datetime.utcnow().isoformat()))
        created = cursor.rowcount > 0

        if created:
            cursor.execute("""
            UPDATE users SET total_subscribers = COALESCE(total_subscribers, 0) + 1
            WHERE user_id = %s
            """, (author_id,))

        conn.commit()
        return created
    except Exception as e:
        print(f"subscribe_to_author error: {e}")
        return False
    finally:
        conn.close()


def unsubscribe_from_author(subscriber_id: int, author_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        DELETE FROM author_subscriptions
        WHERE subscriber_id = %s AND author_id = %s
        """, (subscriber_id, author_id))
        deleted = cursor.rowcount > 0

        if deleted:
            cursor.execute("""
            UPDATE users SET total_subscribers = GREATEST(COALESCE(total_subscribers, 0) - 1, 0)
            WHERE user_id = %s
            """, (author_id,))

        conn.commit()
        return deleted
    except Exception as e:
        print(f"unsubscribe_from_author error: {e}")
        return False
    finally:
        conn.close()


def is_subscribed_to_author(subscriber_id: int, author_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT 1 FROM author_subscriptions
        WHERE subscriber_id = %s AND author_id = %s
        """, (subscriber_id, author_id))
        return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        conn.close()


def get_user_subscriptions(subscriber_id: int) -> List[Dict[str, Any]]:
    """Все авторы на которых подписан юзер."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT s.author_id, s.notifications_enabled, s.created_at,
               u.username, u.first_name, u.is_author,
               u.total_posts, u.total_subscribers
        FROM author_subscriptions s
        JOIN users u ON u.user_id = s.author_id
        WHERE s.subscriber_id = %s
        ORDER BY s.created_at DESC
        """, (subscriber_id,))
        rows = cursor.fetchall()
        return [{
            "author_id": r[0],
            "notifications_enabled": bool(r[1]) if r[1] is not None else True,
            "subscribed_at": r[2],
            "username": r[3],
            "first_name": r[4],
            "is_author": bool(r[5]) if r[5] else False,
            "total_posts": r[6] or 0,
            "total_subscribers": r[7] or 0,
        } for r in rows]
    except Exception as e:
        print(f"get_user_subscriptions error: {e}")
        return []
    finally:
        conn.close()


def get_author_subscribers(author_id: int, notifications_only: bool = True) -> List[int]:
    """Список user_id подписчиков автора."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if notifications_only:
            cursor.execute("""
            SELECT subscriber_id FROM author_subscriptions
            WHERE author_id = %s AND notifications_enabled = 1
            """, (author_id,))
        else:
            cursor.execute("""
            SELECT subscriber_id FROM author_subscriptions
            WHERE author_id = %s
            """, (author_id,))
        rows = cursor.fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        print(f"get_author_subscribers error: {e}")
        return []
    finally:
        conn.close()


def toggle_subscription_notifications(subscriber_id: int, author_id: int, enabled: bool) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE author_subscriptions SET notifications_enabled = %s
        WHERE subscriber_id = %s AND author_id = %s
        """, (1 if enabled else 0, subscriber_id, author_id))
        success = cursor.rowcount > 0
        conn.commit()
        return success
    except Exception as e:
        print(f"toggle_subscription_notifications error: {e}")
        return False
    finally:
        conn.close()


def get_subscription_feed(subscriber_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """Лента прогнозов от авторов на которых подписан юзер."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT p.id, p.author_id, p.question, p.category,
               p.display_prediction, p.confidence, p.market_probability,
               p.alpha_label, p.author_comment,
               p.total_donations_ton, p.total_donors, p.created_at,
               u.username, u.first_name, p.title, p.body_text, p.article_type, p.cover_image_file_id, p.cover_image_url
        FROM author_posts p
        JOIN author_subscriptions s ON s.author_id = p.author_id
        JOIN users u ON u.user_id = p.author_id
        WHERE s.subscriber_id = %s AND p.is_deleted = 0
          AND COALESCE(p.status, 'published') = 'published'
          AND COALESCE(p.published_to_profile, 1) = 1
        ORDER BY p.created_at DESC LIMIT %s
        """, (subscriber_id, limit))
        rows = cursor.fetchall()
        return [{
            "id": r[0], "author_id": r[1], "question": r[2],
            "category": r[3], "display_prediction": r[4],
            "confidence": r[5], "market_probability": r[6],
            "alpha_label": r[7], "author_comment": r[8],
            "total_donations_ton": r[9] or 0,
            "total_donors": r[10] or 0,
            "created_at": r[11],
            "author_username": r[12],
            "author_first_name": r[13],
            "title": r[14], "body_text": r[15], "article_type": r[16],
            "cover_image_file_id": r[17], "cover_image_url": r[18],
        } for r in rows]
    except Exception as e:
        print(f"get_subscription_feed error: {e}")
        return []
    finally:
        conn.close()


# ═══════════════════════════════════════════
# DONATIONS
# ═══════════════════════════════════════════

def create_donation(
    donor_id: int,
    author_id: int,
    ton_amount: float,
    post_id: Optional[int] = None,
    comment: str = "",
    tx_hash: str = "",
    status: str = "pending",
) -> Optional[int]:
    """Создаёт запись доната. Статус 'pending' пока не пришла TON транзакция."""
    platform_fee_percent = float(get_setting("platform_fee_percent", "20"))
    platform_fee = round(ton_amount * platform_fee_percent / 100, 6)
    author_received = round(ton_amount - platform_fee, 6)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO author_donations (
            donor_id, author_id, post_id, ton_amount,
            platform_fee_ton, author_received_ton,
            tx_hash, status, comment, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """, (
            donor_id, author_id, post_id, ton_amount,
            platform_fee, author_received,
            tx_hash, status, comment,
            datetime.utcnow().isoformat(),
        ))
        donation_id = cursor.fetchone()[0]
        conn.commit()
        return donation_id
    except Exception as e:
        print(f"create_donation error: {e}")
        return None
    finally:
        conn.close()


def complete_donation(donation_id: int, tx_hash: str) -> bool:
    """
    Завершает донат: статус -> 'completed', зачисляет автору на баланс,
    обновляет счётчики у поста.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT donor_id, author_id, post_id, ton_amount,
               author_received_ton, status
        FROM author_donations WHERE id = %s
        """, (donation_id,))
        row = cursor.fetchone()
        if not row:
            return False

        donor_id, author_id, post_id, ton_amount, author_received, status = row

        if status == "completed":
            return True  # уже обработано

        # Помечаем донат как завершённый
        cursor.execute("""
        UPDATE author_donations SET
            status = 'completed',
            tx_hash = %s
        WHERE id = %s
        """, (tx_hash, donation_id))

        # Зачисляем автору
        cursor.execute("""
        UPDATE users SET
            author_balance_ton = author_balance_ton + %s,
            updated_at = %s
        WHERE user_id = %s
        """, (author_received, datetime.utcnow().isoformat(), author_id))

        conn.commit()

        # Обновляем счётчики поста (если указан)
        if post_id:
            # Проверяем — новый ли донор для этого поста
            cursor.execute("""
            SELECT COUNT(*) FROM author_donations
            WHERE post_id = %s AND donor_id = %s AND status = 'completed' AND id != %s
            """, (post_id, donor_id, donation_id))
            existing = cursor.fetchone()[0]
            is_new_donor = existing == 0

            update_post_donations(post_id, ton_amount, is_new_donor)
            try:
                from services.airdrop_points_service import award_article_donation_received_points
                award_article_donation_received_points(author_id, donor_id, article_id=post_id, donation_id=donation_id, metadata={"ton_amount": ton_amount})
            except Exception as exc:
                print(f"award_article_donation_received_points error: {type(exc).__name__}")

        return True
    except Exception as e:
        print(f"complete_donation error: {e}")
        return False
    finally:
        conn.close()


def get_donation(donation_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT * FROM author_donations WHERE id = %s", (donation_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def get_pending_donations(limit: int = 50) -> List[Dict[str, Any]]:
    """Донаты ожидающие подтверждения — для TON-воркера."""
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT * FROM author_donations
        WHERE status = 'pending' ORDER BY created_at ASC LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_pending_donations error: {e}")
        return []
    finally:
        conn.close()


def get_author_donations_list(author_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """Все донаты полученные автором."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT d.id, d.donor_id, d.ton_amount, d.author_received_ton,
               d.comment, d.status, d.created_at,
               u.username, u.first_name
        FROM author_donations d
        LEFT JOIN users u ON u.user_id = d.donor_id
        WHERE d.author_id = %s AND d.status = 'completed'
        ORDER BY d.created_at DESC LIMIT %s
        """, (author_id, limit))
        rows = cursor.fetchall()
        return [{
            "id": r[0], "donor_id": r[1],
            "ton_amount": r[2] or 0,
            "author_received_ton": r[3] or 0,
            "comment": r[4] or "",
            "status": r[5],
            "created_at": r[6],
            "donor_username": r[7],
            "donor_first_name": r[8],
        } for r in rows]
    except Exception as e:
        print(f"get_author_donations_list error: {e}")
        return []
    finally:
        conn.close()


def get_donation_stats() -> Dict[str, Any]:
    """Глобальная статистика донатов для админки."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT COUNT(*), COALESCE(SUM(ton_amount), 0),
               COALESCE(SUM(platform_fee_ton), 0),
               COALESCE(SUM(author_received_ton), 0)
        FROM author_donations WHERE status = 'completed'
        """)
        row = cursor.fetchone()
        total_count = row[0] or 0
        total_ton = row[1] or 0
        total_fee = row[2] or 0
        total_to_authors = row[3] or 0

        cursor.execute("SELECT COUNT(DISTINCT donor_id) FROM author_donations WHERE status = 'completed'")
        unique_donors = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(DISTINCT author_id) FROM author_donations WHERE status = 'completed'")
        unique_authors = cursor.fetchone()[0] or 0

        return {
            "total_donations": total_count,
            "total_ton": total_ton,
            "platform_revenue_ton": total_fee,
            "authors_received_ton": total_to_authors,
            "unique_donors": unique_donors,
            "unique_authors": unique_authors,
        }
    except Exception as e:
        print(f"get_donation_stats error: {e}")
        return {
            "total_donations": 0, "total_ton": 0,
            "platform_revenue_ton": 0, "authors_received_ton": 0,
            "unique_donors": 0, "unique_authors": 0,
        }
    finally:
        conn.close()


# ═══════════════════════════════════════════
# WITHDRAWAL REQUESTS (заявки на вывод)
# ═══════════════════════════════════════════

def create_withdrawal_request(author_id: int, amount_ton: float, ton_wallet: str) -> Optional[int]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO withdrawal_requests (
            author_id, amount_ton, ton_wallet, status, created_at
        ) VALUES (%s, %s, %s, 'pending', %s)
        RETURNING id
        """, (author_id, amount_ton, ton_wallet, datetime.utcnow().isoformat()))
        wid = cursor.fetchone()[0]
        conn.commit()
        return wid
    except Exception as e:
        print(f"create_withdrawal_request error: {e}")
        return None
    finally:
        conn.close()


def get_pending_withdrawals(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT w.id, w.author_id, w.amount_ton, w.ton_wallet,
               w.status, w.created_at,
               u.username, u.first_name, u.author_balance_ton
        FROM withdrawal_requests w
        LEFT JOIN users u ON u.user_id = w.author_id
        WHERE w.status = 'pending'
        ORDER BY w.created_at ASC LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [{
            "id": r[0], "author_id": r[1],
            "amount_ton": r[2] or 0,
            "ton_wallet": r[3] or "",
            "status": r[4],
            "created_at": r[5],
            "author_username": r[6],
            "author_first_name": r[7],
            "current_balance": r[8] or 0,
        } for r in rows]
    except Exception as e:
        print(f"get_pending_withdrawals error: {e}")
        return []
    finally:
        conn.close()


def approve_withdrawal(withdrawal_id: int, tx_hash: str, admin_note: str = "") -> bool:
    """Админ подтверждает выплату. Списывает с баланса автора."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT author_id, amount_ton, status
        FROM withdrawal_requests WHERE id = %s
        """, (withdrawal_id,))
        row = cursor.fetchone()
        if not row or row[2] != "pending":
            return False

        author_id, amount_ton, _ = row

        # Списываем с баланса (атомарно)
        cursor.execute("""
        UPDATE users SET
            author_balance_ton = author_balance_ton - %s,
            author_withdrawn_ton = author_withdrawn_ton + %s,
            updated_at = %s
        WHERE user_id = %s AND author_balance_ton >= %s
        """, (amount_ton, amount_ton, datetime.utcnow().isoformat(), author_id, amount_ton))

        if cursor.rowcount == 0:
            conn.rollback()
            return False  # недостаточно баланса

        cursor.execute("""
        UPDATE withdrawal_requests SET
            status = 'approved',
            tx_hash = %s,
            admin_note = %s,
            processed_at = %s
        WHERE id = %s
        """, (tx_hash, admin_note, datetime.utcnow().isoformat(), withdrawal_id))
        conn.commit()
        return True
    except Exception as e:
        print(f"approve_withdrawal error: {e}")
        return False
    finally:
        conn.close()


def reject_withdrawal(withdrawal_id: int, admin_note: str = "") -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE withdrawal_requests SET
            status = 'rejected',
            admin_note = %s,
            processed_at = %s
        WHERE id = %s AND status = 'pending'
        """, (admin_note, datetime.utcnow().isoformat(), withdrawal_id))
        success = cursor.rowcount > 0
        conn.commit()
        return success
    except Exception as e:
        print(f"reject_withdrawal error: {e}")
        return False
    finally:
        conn.close()


def get_author_withdrawals(author_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT * FROM withdrawal_requests
        WHERE author_id = %s ORDER BY created_at DESC LIMIT %s
        """, (author_id, limit))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_author_withdrawals error: {e}")
        return []
    finally:
        conn.close()


# ═══════════════════════════════════════════
# USER LANGUAGE
# ═══════════════════════════════════════════

def set_user_language(user_id: int, lang: str) -> None:
    if lang not in ("ru", "en"):
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET language = %s, updated_at = %s WHERE user_id = %s
        """, (lang, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"set_user_language error: {e}")
    finally:
        conn.close()


def get_user_language(user_id: int) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT language FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    finally:
        conn.close()
    return "ru"


# ═══════════════════════════════════════════
# INLINE QUERIES COUNTER
# ═══════════════════════════════════════════

def increment_inline_queries(user_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET inline_queries_count = COALESCE(inline_queries_count, 0) + 1,
               updated_at = %s WHERE user_id = %s
        """, (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"increment_inline_queries error: {e}")
    finally:
        conn.close()


def get_inline_queries_count(user_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT inline_queries_count FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] else 0
    except Exception:
        return 0
    finally:
        conn.close()


# ═══════════════════════════════════════════
# WATCHLIST
# ═══════════════════════════════════════════

def add_to_watchlist(
    user_id: int,
    market_slug: str,
    market_url: str,
    question: str,
    category: str,
    initial_probability: float,
    initial_market_prob_str: str,
    market_end_date: Optional[str] = None,
    is_extra_slot: bool = False,
) -> Optional[int]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT id FROM watchlist
        WHERE user_id = %s AND market_slug = %s AND is_closed = 0
        """, (user_id, market_slug))
        if cursor.fetchone():
            return None

        now = datetime.utcnow().isoformat()
        cursor.execute("""
        INSERT INTO watchlist (
            user_id, market_slug, market_url, question, category,
            initial_probability, initial_market_prob_str,
            last_checked_probability, last_probability_change,
            market_end_date, extra_slot, created_at, last_checked_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s)
        RETURNING id
        """, (
            user_id, market_slug, market_url, question, category,
            initial_probability, initial_market_prob_str, initial_probability,
            market_end_date, 1 if is_extra_slot else 0, now, now,
        ))
        wid = cursor.fetchone()[0]
        conn.commit()
        return wid
    except Exception as e:
        print(f"add_to_watchlist error: {e}")
        return None
    finally:
        conn.close()


def remove_from_watchlist(user_id: int, watchlist_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        DELETE FROM watchlist WHERE id = %s AND user_id = %s
        """, (watchlist_id, user_id))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    except Exception as e:
        print(f"remove_from_watchlist error: {e}")
        return False
    finally:
        conn.close()


def get_user_watchlist(user_id: int, include_closed: bool = False) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if include_closed:
            cursor.execute("""
            SELECT id, market_slug, market_url, question, category,
                   initial_probability, last_checked_probability,
                   last_probability_change, market_end_date,
                   notify_enabled, is_closed, extra_slot,
                   created_at, last_checked_at, billing_status, paused_reason,
                   paused_at, last_billed_at, tokens_spent, autopilot_enabled,
                   ai_summary_enabled
            FROM watchlist WHERE user_id = %s ORDER BY id DESC
            """, (user_id,))
        else:
            cursor.execute("""
            SELECT id, market_slug, market_url, question, category,
                   initial_probability, last_checked_probability,
                   last_probability_change, market_end_date,
                   notify_enabled, is_closed, extra_slot,
                   created_at, last_checked_at, billing_status, paused_reason,
                   paused_at, last_billed_at, tokens_spent, autopilot_enabled,
                   ai_summary_enabled
            FROM watchlist WHERE user_id = %s AND is_closed = 0 ORDER BY id DESC
            """, (user_id,))
        rows = cursor.fetchall()
        return [{
            "id": r[0], "market_slug": r[1], "market_url": r[2],
            "question": r[3], "category": r[4],
            "initial_probability": r[5] or 0,
            "last_checked_probability": r[6] or 0,
            "last_probability_change": r[7] or 0,
            "market_end_date": r[8],
            "notify_enabled": bool(r[9]) if r[9] is not None else True,
            "is_closed": bool(r[10]) if r[10] else False,
            "extra_slot": bool(r[11]) if r[11] else False,
            "created_at": r[12],
            "last_checked_at": r[13],
            "billing_status": r[14] or "active",
            "paused_reason": r[15],
            "paused_at": r[16],
            "last_billed_at": r[17],
            "tokens_spent": int(r[18] or 0),
            "autopilot_enabled": bool(r[19]) if r[19] is not None else True,
            "ai_summary_enabled": bool(r[20]) if r[20] is not None else True,
        } for r in rows]
    except Exception as e:
        print(f"get_user_watchlist error: {e}")
        return []
    finally:
        conn.close()


def count_user_watchlist(user_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT COUNT(*) FROM watchlist
        WHERE user_id = %s AND is_closed = 0
        """, (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


def get_user_watchlist_limit(user_id: int) -> int:
    user = get_user(user_id)
    if not user:
        return 0
    if user.get("is_vip") or is_subscribed(user_id):
        base_limit = int(get_setting("watchlist_limit_vip", "50"))
    else:
        base_limit = int(get_setting("watchlist_limit_regular", "10"))
    extra_slots = user.get("extra_watchlist_slots", 0) or 0
    return base_limit + extra_slots


def can_add_to_watchlist(user_id: int) -> Dict[str, Any]:
    current = count_user_watchlist(user_id)
    limit = get_user_watchlist_limit(user_id)
    if current >= limit:
        return {"allowed": False, "reason": "limit_reached", "current": current, "limit": limit}
    return {"allowed": True, "reason": None, "current": current, "limit": limit}


def add_watchlist_extra_slots(user_id: int, count: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET
            extra_watchlist_slots = COALESCE(extra_watchlist_slots, 0) + %s,
            updated_at = %s WHERE user_id = %s
        """, (count, datetime.utcnow().isoformat(), user_id))
        conn.commit()
        cursor.execute("SELECT extra_watchlist_slots FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] else 0
    except Exception as e:
        print(f"add_watchlist_extra_slots error: {e}")
        return 0
    finally:
        conn.close()


def toggle_watchlist_notifications(user_id: int, watchlist_id: int, enabled: bool) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE watchlist SET notify_enabled = %s
        WHERE id = %s AND user_id = %s
        """, (1 if enabled else 0, watchlist_id, user_id))
        success = cursor.rowcount > 0
        conn.commit()
        return success
    except Exception as e:
        print(f"toggle_watchlist_notifications error: {e}")
        return False
    finally:
        conn.close()


def get_watchlist_event_cost(event_type: str) -> int:
    key_map = {
        "probability_change": "watchlist_probability_alert_cost_tokens",
        "closing_soon": "watchlist_closing_soon_cost_tokens",
        "resolved_recap": "watchlist_resolved_recap_cost_tokens",
        "ai_deep_recap": "watchlist_ai_deep_recap_cost_tokens",
    }
    default_map = {
        "probability_change": "5",
        "closing_soon": "3",
        "resolved_recap": "7",
        "ai_deep_recap": "10",
    }
    try:
        return max(0, int(get_setting(key_map.get(event_type, "watchlist_ai_deep_recap_cost_tokens"), default_map.get(event_type, "10"))))
    except Exception:
        return int(default_map.get(event_type, "10"))


def pause_watchlist_item(watchlist_id: int, reason: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE watchlist
        SET billing_status = %s, paused_reason = %s, paused_at = %s
        WHERE id = %s
        """, (f"paused_{reason}" if not str(reason).startswith("paused_") else reason,
              reason, datetime.utcnow().isoformat(), watchlist_id))
        ok = cursor.rowcount > 0
        conn.commit()
        return ok
    except Exception as e:
        print(f"pause_watchlist_item error: {e}")
        return False
    finally:
        conn.close()


def resume_watchlist_item(user_id: int, watchlist_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE watchlist
        SET billing_status = 'active', paused_reason = NULL, paused_at = NULL
        WHERE id = %s AND user_id = %s AND is_closed = 0
        """, (watchlist_id, user_id))
        ok = cursor.rowcount > 0
        conn.commit()
        return ok
    except Exception as e:
        print(f"resume_watchlist_item error: {e}")
        return False
    finally:
        conn.close()


def charge_watchlist_event(user_id: int, watchlist_id: int, market_slug: str, event_type: str, event_fingerprint: str) -> dict:
    if str(get_setting("watchlist_token_billing_enabled", "on")).lower() != "on":
        return {"charged": False, "reason": "billing_disabled"}
    if is_user_vip(user_id):
        return {"charged": False, "reason": "vip"}

    cost = get_watchlist_event_cost(event_type)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT id FROM watchlist_token_ledger
        WHERE user_id = %s AND watchlist_id = %s AND event_type = %s AND event_fingerprint = %s
        """, (user_id, watchlist_id, event_type, event_fingerprint))
        if cursor.fetchone():
            return {"charged": False, "reason": "duplicate", "cost": cost}

        cursor.execute("SELECT COALESCE(token_balance, 0) FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
        row = cursor.fetchone()
        balance = int(row[0] or 0) if row else 0
        now = datetime.utcnow().isoformat()
        if balance < cost:
            cursor.execute("""
            UPDATE watchlist
            SET billing_status = 'paused_insufficient_tokens',
                paused_reason = 'insufficient_tokens',
                paused_at = %s
            WHERE id = %s AND user_id = %s
            """, (now, watchlist_id, user_id))
            conn.commit()
            return {"charged": False, "reason": "insufficient_tokens", "cost": cost, "balance": balance}

        new_balance = balance - cost
        cursor.execute("""
        UPDATE users SET token_balance = %s, updated_at = %s WHERE user_id = %s
        """, (new_balance, now, user_id))
        cursor.execute("""
        INSERT INTO watchlist_token_ledger
            (user_id, watchlist_id, market_slug, event_type, event_fingerprint, tokens, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, watchlist_id, event_type, event_fingerprint) DO NOTHING
        """, (user_id, watchlist_id, market_slug, event_type, event_fingerprint, cost, now))
        if cursor.rowcount == 0:
            conn.rollback()
            return {"charged": False, "reason": "duplicate", "cost": cost, "balance": balance}
        cursor.execute("""
        UPDATE watchlist
        SET tokens_spent = COALESCE(tokens_spent, 0) + %s, last_billed_at = %s
        WHERE id = %s AND user_id = %s
        """, (cost, now, watchlist_id, user_id))
        conn.commit()
        return {"charged": True, "reason": "charged", "cost": cost, "balance": new_balance}
    except Exception as e:
        conn.rollback()
        print(f"charge_watchlist_event error: {e}")
        return {"charged": False, "reason": "error", "cost": cost, "error": str(e)}
    finally:
        conn.close()


def get_watchlist_billing_summary(user_id: int) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT COUNT(*), COALESCE(SUM(tokens_spent), 0),
               SUM(CASE WHEN billing_status = 'active' OR billing_status IS NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN billing_status LIKE 'paused%%' THEN 1 ELSE 0 END)
        FROM watchlist WHERE user_id = %s AND is_closed = 0
        """, (user_id,))
        r = cursor.fetchone() or (0, 0, 0, 0)
        return {"items": int(r[0] or 0), "tokens_spent": int(r[1] or 0),
                "active": int(r[2] or 0), "paused": int(r[3] or 0)}
    except Exception as e:
        print(f"get_watchlist_billing_summary error: {e}")
        return {"items": 0, "tokens_spent": 0, "active": 0, "paused": 0}
    finally:
        conn.close()


def get_active_watchlist_items(limit: int = 500) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT DISTINCT market_slug, market_url, question, category, market_end_date
        FROM watchlist
        WHERE is_closed = 0
        LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [{
            "market_slug": r[0], "market_url": r[1],
            "question": r[2], "category": r[3], "market_end_date": r[4],
        } for r in rows]
    except Exception as e:
        print(f"get_active_watchlist_items error: {e}")
        return []
    finally:
        conn.close()


def get_watchlist_subscribers(market_slug: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT id, user_id, initial_probability, last_checked_probability,
               notify_enabled, notified_change, notified_closing_soon,
               notified_resolved, market_end_date, billing_status,
               tokens_spent, autopilot_enabled
        FROM watchlist
        WHERE market_slug = %s AND is_closed = 0
          AND COALESCE(notify_enabled, 1) = 1
          AND COALESCE(autopilot_enabled, 1) = 1
          AND (billing_status IS NULL OR billing_status = 'active')
        """, (market_slug,))
        rows = cursor.fetchall()
        return [{
            "id": r[0], "user_id": r[1],
            "initial_probability": r[2] or 0,
            "last_checked_probability": r[3] or 0,
            "notify_enabled": bool(r[4]) if r[4] is not None else True,
            "notified_change": bool(r[5]) if r[5] else False,
            "notified_closing_soon": bool(r[6]) if r[6] else False,
            "notified_resolved": bool(r[7]) if r[7] else False,
            "market_end_date": r[8],
            "billing_status": r[9] or "active",
            "tokens_spent": int(r[10] or 0),
            "autopilot_enabled": bool(r[11]) if r[11] is not None else True,
        } for r in rows]
    except Exception as e:
        print(f"get_watchlist_subscribers error: {e}")
        return []
    finally:
        conn.close()


def update_watchlist_probability(watchlist_id: int, new_probability: float) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE watchlist SET last_checked_probability = %s, last_checked_at = %s
        WHERE id = %s
        """, (new_probability, datetime.utcnow().isoformat(), watchlist_id))
        conn.commit()
    except Exception as e:
        print(f"update_watchlist_probability error: {e}")
    finally:
        conn.close()


def mark_watchlist_notified(watchlist_id: int, notification_type: str) -> None:
    valid_types = {"change", "closing_soon", "resolved"}
    if notification_type not in valid_types:
        return
    field_map = {
        "change": "notified_change",
        "closing_soon": "notified_closing_soon",
        "resolved": "notified_resolved",
    }
    field = field_map[notification_type]
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"UPDATE watchlist SET {field} = 1 WHERE id = %s", (watchlist_id,))
        conn.commit()
    except Exception as e:
        print(f"mark_watchlist_notified error: {e}")
    finally:
        conn.close()


def reset_watchlist_change_notification(watchlist_id: int, new_probability: float) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE watchlist SET
            notified_change = 0, initial_probability = %s,
            last_checked_probability = %s, last_checked_at = %s
        WHERE id = %s
        """, (new_probability, new_probability, datetime.utcnow().isoformat(), watchlist_id))
        conn.commit()
    except Exception as e:
        print(f"reset_watchlist_change_notification error: {e}")
    finally:
        conn.close()


def close_watchlist_market(market_slug: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE watchlist SET is_closed = 1
        WHERE market_slug = %s AND is_closed = 0
        """, (market_slug,))
        count = cursor.rowcount
        conn.commit()
        return count
    except Exception as e:
        print(f"close_watchlist_market error: {e}")
        return 0
    finally:
        conn.close()


def cleanup_old_closed_watchlist(days: int = 30) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor.execute("""
        DELETE FROM watchlist
        WHERE is_closed = 1 AND last_checked_at < %s
        """, (cutoff,))
        count = cursor.rowcount
        conn.commit()
        return count
    except Exception as e:
        print(f"cleanup_old_closed_watchlist error: {e}")
        return 0
    finally:
        conn.close()


def get_watchlist_stats() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM watchlist WHERE is_closed = 0")
        active = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM watchlist WHERE is_closed = 0")
        unique_users = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(DISTINCT market_slug) FROM watchlist WHERE is_closed = 0")
        unique_markets = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM watchlist WHERE is_closed = 1")
        closed = cursor.fetchone()[0] or 0
        cursor.execute("""
        SELECT SUM(extra_watchlist_slots) FROM users WHERE extra_watchlist_slots > 0
        """)
        extra_sum = cursor.fetchone()[0] or 0
        return {
            "active": active, "unique_users": unique_users,
            "unique_markets": unique_markets, "closed": closed,
            "total_extra_slots_purchased": extra_sum,
        }
    except Exception as e:
        print(f"get_watchlist_stats error: {e}")
        return {"active": 0, "unique_users": 0, "unique_markets": 0,
                "closed": 0, "total_extra_slots_purchased": 0}
    finally:
        conn.close()


def get_watchlist_by_id(watchlist_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT id, user_id, market_slug, market_url, question, category,
               initial_probability, last_checked_probability,
               last_probability_change, market_end_date,
               notify_enabled, is_closed, extra_slot,
               created_at, last_checked_at, billing_status, paused_reason,
               paused_at, last_billed_at, tokens_spent, autopilot_enabled,
               ai_summary_enabled
        FROM watchlist WHERE id = %s
        """, (watchlist_id,))
        r = cursor.fetchone()
        if not r:
            return None
        return {
            "id": r[0], "user_id": r[1], "market_slug": r[2],
            "market_url": r[3], "question": r[4], "category": r[5],
            "initial_probability": r[6] or 0,
            "last_checked_probability": r[7] or 0,
            "last_probability_change": r[8] or 0,
            "market_end_date": r[9],
            "notify_enabled": bool(r[10]) if r[10] is not None else True,
            "is_closed": bool(r[11]) if r[11] else False,
            "extra_slot": bool(r[12]) if r[12] else False,
            "created_at": r[13],
            "last_checked_at": r[14],
            "billing_status": r[15] or "active",
            "paused_reason": r[16],
            "paused_at": r[17],
            "last_billed_at": r[18],
            "tokens_spent": int(r[19] or 0),
            "autopilot_enabled": bool(r[20]) if r[20] is not None else True,
            "ai_summary_enabled": bool(r[21]) if r[21] is not None else True,
        }
    except Exception as e:
        print(f"get_watchlist_by_id error: {e}")
        return None
    finally:
        conn.close()


def get_web_analysis_history_item(user_id: int, item_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT id, user_id, analysis_type, market_url, market_slug, question,
               display_prediction, market_probability, confidence, category,
               status, result_json, error, created_at
        FROM web_analysis_history
        WHERE id = %s AND user_id = %s
        LIMIT 1
        """, (item_id, user_id))
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        raw = data.get("result_json")
        if isinstance(raw, str) and raw:
            try:
                data["result_json"] = json.loads(raw)
            except Exception:
                pass
        return data
    except Exception as e:
        print(f"get_web_analysis_history_item error: {e}")
        return None
    finally:
        conn.close()

# ═══════════════════════════════════════════
# LIVE ANALYST MODE
# ═══════════════════════════════════════════

_live_analyst_tables_ready = False

def init_live_analyst_tables() -> None:
    global _live_analyst_tables_ready
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _init_live_analyst_tables(cursor)
        ensure_gemini_lockdown_tables(cursor)
        conn.commit()
        _live_analyst_tables_ready = True
    except Exception as e:
        print(f"init_live_analyst_tables error: {e}")
    finally:
        conn.close()


def _init_live_analyst_tables(cursor) -> None:
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS live_analyst_sessions (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        status TEXT DEFAULT 'active',
        current_market_url TEXT NULL,
        current_market_title TEXT NULL,
        last_analysis_summary TEXT NULL,
        last_image_summary TEXT NULL,
        memory_summary TEXT NULL,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW(),
        closed_at TIMESTAMP NULL
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_live_analyst_sessions_user_status ON live_analyst_sessions(user_id, status)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS live_analyst_messages (
        id SERIAL PRIMARY KEY,
        session_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        role TEXT NOT NULL,
        message_type TEXT NOT NULL,
        content TEXT,
        image_file_id TEXT NULL,
        tokens_charged INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_live_analyst_messages_session_created ON live_analyst_messages(session_id, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_live_analyst_messages_user_created ON live_analyst_messages(user_id, created_at)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS live_analyst_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS live_analyst_usage (
        user_id BIGINT NOT NULL,
        usage_date DATE NOT NULL DEFAULT CURRENT_DATE,
        gemini_vision_calls INTEGER NOT NULL DEFAULT 0,
        live_analyst_calls INTEGER NOT NULL DEFAULT 0,
        last_call_at TIMESTAMP DEFAULT NOW(),
        PRIMARY KEY (user_id, usage_date)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_live_analyst_usage_date ON live_analyst_usage(usage_date)")

    defaults = [
        ("live_enabled", "true"),
        ("text_request_cost", "1"),
        ("image_request_cost", "3"),
        ("memory_message_limit", "12"),
        ("max_daily_live_messages", "20"),
        ("image_analysis_enabled", "true"),
        ("max_image_size_mb", "8"),
    ]
    for key, value in defaults:
        cursor.execute("""
        INSERT INTO live_analyst_settings (key, value, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (key) DO NOTHING
        """, (key, value))


def _ensure_live_analyst_tables(conn, cursor) -> None:
    global _live_analyst_tables_ready
    if _live_analyst_tables_ready:
        return
    try:
        _init_live_analyst_tables(cursor)
        ensure_gemini_lockdown_tables(cursor)
        conn.commit()
        _live_analyst_tables_ready = True
    except Exception:
        conn.rollback()
        raise


def get_live_analyst_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_live_analyst_tables(conn, cursor)
        cursor.execute("SELECT value FROM live_analyst_settings WHERE key = %s", (key,))
        row = cursor.fetchone()
        return row[0] if row else default
    except Exception as e:
        print(f"get_live_analyst_setting error: {e}")
        return default
    finally:
        conn.close()


def set_live_analyst_setting(key: str, value: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_live_analyst_tables(conn, cursor)
        cursor.execute("""
        INSERT INTO live_analyst_settings (key, value, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
        """, (key, value))
        conn.commit()
    except Exception as e:
        print(f"set_live_analyst_setting error: {e}")
    finally:
        conn.close()


def get_live_analyst_active_session(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_live_analyst_tables(conn, cursor)
        cursor.execute("""
        SELECT * FROM live_analyst_sessions
        WHERE user_id = %s AND status = 'active'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"get_live_analyst_active_session error: {e}")
        return None
    finally:
        conn.close()


def create_live_analyst_session(user_id: int) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_live_analyst_tables(conn, cursor)
        cursor.execute("""
        INSERT INTO live_analyst_sessions (user_id, status, created_at, updated_at)
        VALUES (%s, 'active', NOW(), NOW())
        RETURNING *
        """, (user_id,))
        row = cursor.fetchone()
        conn.commit()
        return dict(row) if row else {"user_id": user_id, "status": "active"}
    except Exception as e:
        print(f"create_live_analyst_session error: {e}")
        return {"user_id": user_id, "status": "active"}
    finally:
        conn.close()


def update_live_analyst_session(session_id: int, **fields) -> bool:
    allowed = {
        "status", "current_market_url", "current_market_title", "last_analysis_summary",
        "last_image_summary", "memory_summary", "closed_at",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if "attached_analysis_json" in updates and isinstance(updates["attached_analysis_json"], (dict, list)):
        updates["attached_analysis_json"] = json.dumps(updates["attached_analysis_json"], ensure_ascii=False)
    if not updates:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_live_analyst_tables(conn, cursor)
        parts = ["updated_at = NOW()"]
        values: List[Any] = []
        for key, value in updates.items():
            parts.append(f"{key} = %s")
            values.append(value)
        values.append(session_id)
        cursor.execute(f"UPDATE live_analyst_sessions SET {', '.join(parts)} WHERE id = %s", tuple(values))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"update_live_analyst_session error: {e}")
        return False
    finally:
        conn.close()


def close_live_analyst_session(session_id: int) -> bool:
    return update_live_analyst_session(session_id, status="closed", closed_at=datetime.utcnow())


def append_live_analyst_message(
    session_id: int,
    user_id: int,
    role: str,
    message_type: str,
    content: str,
    image_file_id: Optional[str] = None,
    tokens_charged: int = 0,
) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_live_analyst_tables(conn, cursor)
        cursor.execute("""
        INSERT INTO live_analyst_messages
        (session_id, user_id, role, message_type, content, image_file_id, tokens_charged, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        RETURNING *
        """, (session_id, user_id, role, message_type, content, image_file_id, int(tokens_charged or 0)))
        row = cursor.fetchone()
        cursor.execute("UPDATE live_analyst_sessions SET updated_at = NOW() WHERE id = %s", (session_id,))
        conn.commit()
        return dict(row) if row else None
    except Exception as e:
        print(f"append_live_analyst_message error: {e}")
        return None
    finally:
        conn.close()


def get_live_analyst_recent_messages(session_id: int, limit: int = 12) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_live_analyst_tables(conn, cursor)
        safe_limit = max(1, min(int(limit or 12), 50))
        cursor.execute("""
        SELECT * FROM (
            SELECT * FROM live_analyst_messages
            WHERE session_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
        ) recent
        ORDER BY created_at ASC, id ASC
        """, (session_id, safe_limit))
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        print(f"get_live_analyst_recent_messages error: {e}")
        return []
    finally:
        conn.close()


def count_live_analyst_messages_today(user_id: int, role: Optional[str] = None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_live_analyst_tables(conn, cursor)
        if role:
            cursor.execute("""
            SELECT COUNT(*) FROM live_analyst_messages
            WHERE user_id = %s AND role = %s AND created_at >= CURRENT_DATE
            """, (user_id, role))
        else:
            cursor.execute("""
            SELECT COUNT(*) FROM live_analyst_messages
            WHERE user_id = %s AND created_at >= CURRENT_DATE
            """, (user_id,))
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        print(f"count_live_analyst_messages_today error: {e}")
        return 0
    finally:
        conn.close()




def count_live_analyst_usage_today(user_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_live_analyst_tables(conn, cursor)
        cursor.execute("""
        SELECT gemini_vision_calls FROM live_analyst_usage
        WHERE user_id = %s AND usage_date = CURRENT_DATE
        """, (user_id,))
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        print(f"count_live_analyst_usage_today error: {e}")
        raise
    finally:
        conn.close()


def record_live_analyst_usage(user_id: int, gemini_vision_calls: int = 1, live_analyst_calls: int = 1) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_live_analyst_tables(conn, cursor)
        cursor.execute("""
        INSERT INTO live_analyst_usage (user_id, usage_date, gemini_vision_calls, live_analyst_calls, last_call_at)
        VALUES (%s, CURRENT_DATE, %s, %s, NOW())
        ON CONFLICT (user_id, usage_date) DO UPDATE SET
            gemini_vision_calls = live_analyst_usage.gemini_vision_calls + EXCLUDED.gemini_vision_calls,
            live_analyst_calls = live_analyst_usage.live_analyst_calls + EXCLUDED.live_analyst_calls,
            last_call_at = NOW()
        RETURNING gemini_vision_calls
        """, (user_id, int(gemini_vision_calls or 0), int(live_analyst_calls or 0)))
        row = cursor.fetchone()
        conn.commit()
        calls_today = int(row[0]) if row else 0
        print(f"gemini_usage_recorded user_id={user_id} calls_today={calls_today}")
        return calls_today
    except Exception as e:
        print(f"record_live_analyst_usage error: {e}")
        raise
    finally:
        conn.close()

def get_live_analyst_stats() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        _ensure_live_analyst_tables(conn, cursor)
        cursor.execute("SELECT COUNT(*) AS total_sessions FROM live_analyst_sessions")
        total_sessions = int((cursor.fetchone() or {}).get("total_sessions") or 0)
        cursor.execute("SELECT COUNT(*) AS active_sessions FROM live_analyst_sessions WHERE status = 'active'")
        active_sessions = int((cursor.fetchone() or {}).get("active_sessions") or 0)
        cursor.execute("""
        SELECT
            COUNT(*) AS total_messages,
            SUM(CASE WHEN message_type = 'text' AND role = 'user' THEN 1 ELSE 0 END) AS text_requests,
            SUM(CASE WHEN message_type = 'image' AND role = 'user' THEN 1 ELSE 0 END) AS image_requests,
            COALESCE(SUM(tokens_charged), 0) AS tokens_spent
        FROM live_analyst_messages
        """)
        message_stats = dict(cursor.fetchone() or {})
        cursor.execute("""
        SELECT user_id, COUNT(*) AS messages, COALESCE(SUM(tokens_charged), 0) AS tokens_spent
        FROM live_analyst_messages
        WHERE role = 'user'
        GROUP BY user_id
        ORDER BY messages DESC, tokens_spent DESC
        LIMIT 5
        """)
        top_users = [dict(r) for r in cursor.fetchall()]
        cursor.execute("""
        SELECT
            SUM(CASE WHEN message_type = 'text' AND role = 'user' THEN 1 ELSE 0 END) AS live_text_requests_7d,
            SUM(CASE WHEN message_type = 'image' AND role = 'user' THEN 1 ELSE 0 END) AS live_image_requests_7d,
            COALESCE(SUM(tokens_charged), 0) AS live_tokens_spent_7d,
            COUNT(DISTINCT CASE WHEN role = 'user' THEN user_id ELSE NULL END) AS users_using_live_7d
        FROM live_analyst_messages
        WHERE created_at >= NOW() - INTERVAL '7 days'
        """)
        weekly = dict(cursor.fetchone() or {})
        return {
            "total_sessions": total_sessions,
            "active_sessions": active_sessions,
            "total_messages": int(message_stats.get("total_messages") or 0),
            "text_requests": int(message_stats.get("text_requests") or 0),
            "image_requests": int(message_stats.get("image_requests") or 0),
            "tokens_spent": int(message_stats.get("tokens_spent") or 0),
            "top_users": top_users,
            "live_sessions_total": total_sessions,
            "live_messages_total": int(message_stats.get("total_messages") or 0),
            "live_text_requests_7d": int(weekly.get("live_text_requests_7d") or 0),
            "live_image_requests_7d": int(weekly.get("live_image_requests_7d") or 0),
            "live_tokens_spent_7d": int(weekly.get("live_tokens_spent_7d") or 0),
            "users_using_live_7d": int(weekly.get("users_using_live_7d") or 0),
        }
    except Exception as e:
        print(f"get_live_analyst_stats error: {e}")
        return {}
    finally:
        conn.close()

_gemini_usage_table_ready = False


def _init_gemini_usage_table(cursor) -> None:
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS gemini_usage (
        id SERIAL PRIMARY KEY,
        usage_date DATE NOT NULL DEFAULT CURRENT_DATE,
        feature TEXT NOT NULL,
        user_id BIGINT NULL,
        chat_id BIGINT NULL,
        is_background BOOLEAN NOT NULL DEFAULT FALSE,
        calls INTEGER NOT NULL DEFAULT 0,
        units INTEGER NOT NULL DEFAULT 0,
        last_call_at TIMESTAMP DEFAULT NOW(),
        UNIQUE (usage_date, feature, user_id, chat_id, is_background)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gemini_usage_date ON gemini_usage(usage_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gemini_usage_feature_date ON gemini_usage(feature, usage_date)")


def _ensure_gemini_usage_table(conn, cursor) -> None:
    global _gemini_usage_table_ready
    if _gemini_usage_table_ready:
        return
    try:
        _init_gemini_usage_table(cursor)
        conn.commit()
        _gemini_usage_table_ready = True
    except Exception:
        conn.rollback()
        raise


def count_gemini_usage_today(feature: Optional[str] = None, is_background: Optional[bool] = None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_gemini_usage_table(conn, cursor)
        clauses = ["usage_date = CURRENT_DATE"]
        params: List[Any] = []
        if feature is not None:
            clauses.append("feature = %s")
            params.append(feature)
        if is_background is not None:
            clauses.append("is_background = %s")
            params.append(bool(is_background))
        cursor.execute(f"SELECT COALESCE(SUM(calls), 0) FROM gemini_usage WHERE {' AND '.join(clauses)}", tuple(params))
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        print(f"count_gemini_usage_today error: {e}")
        raise
    finally:
        conn.close()


def record_gemini_usage(feature, user_id=None, chat_id=None, is_background=False, units=1) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_gemini_usage_table(conn, cursor)
        cursor.execute("""
        SELECT id FROM gemini_usage
        WHERE usage_date = CURRENT_DATE
          AND feature = %s
          AND user_id IS NOT DISTINCT FROM %s
          AND chat_id IS NOT DISTINCT FROM %s
          AND is_background = %s
        LIMIT 1
        """, (feature, user_id, chat_id, bool(is_background)))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("""
            UPDATE gemini_usage
            SET calls = calls + 1, units = units + %s, last_call_at = NOW()
            WHERE id = %s
            RETURNING calls
            """, (int(units or 1), existing[0]))
        else:
            cursor.execute("""
            INSERT INTO gemini_usage (usage_date, feature, user_id, chat_id, is_background, calls, units, last_call_at)
            VALUES (CURRENT_DATE, %s, %s, %s, %s, 1, %s, NOW())
            RETURNING calls
            """, (feature, user_id, chat_id, bool(is_background), int(units or 1)))
        row = cursor.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    except Exception as e:
        print(f"record_gemini_usage error: {e}")
        raise
    finally:
        conn.close()

# GEMINI LOCKDOWN

def ensure_gemini_lockdown_tables(cursor):
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS gemini_call_attempts (
        id SERIAL PRIMARY KEY,
        request_id TEXT NOT NULL,
        cycle_id TEXT,
        job_id TEXT,
        feature TEXT NOT NULL,
        origin TEXT,
        user_id BIGINT,
        chat_id BIGINT,
        is_background BOOLEAN DEFAULT FALSE,
        worker_id TEXT,
        model TEXT,
        status TEXT NOT NULL DEFAULT 'reserved',
        http_status INTEGER,
        reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at TIMESTAMPTZ,
        duration_ms INTEGER,
        provider_request_id TEXT,
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        total_tokens INTEGER,
        estimated_cost NUMERIC DEFAULT 0
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gemini_call_attempts_created ON gemini_call_attempts(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gemini_call_attempts_request ON gemini_call_attempts(request_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gemini_call_attempts_cycle ON gemini_call_attempts(cycle_id)")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS distributed_locks (
        lock_name TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)


def reserve_gemini_attempt(request_id, cycle_id=None, job_id=None, feature='', origin='', user_id=None, chat_id=None, is_background=False, worker_id='', model=''):
    from services.gemini_gateway import env_int
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("gemini_attempts",))
        daily_limit = env_int("GEMINI_DAILY_HTTP_ATTEMPT_LIMIT", env_int("GEMINI_DAILY_CALL_LIMIT", 0))
        bg_limit = env_int("GEMINI_BACKGROUND_DAILY_HTTP_ATTEMPT_LIMIT", 0)
        req_limit = env_int("GEMINI_MAX_ATTEMPTS_PER_REQUEST", 0)
        cycle_limit = env_int("GEMINI_MAX_ATTEMPTS_PER_CYCLE", 0)
        cur.execute("SELECT COUNT(*) FROM gemini_call_attempts WHERE created_at >= date_trunc('day', NOW()) AND status <> 'blocked'")
        if daily_limit <= 0 or int(cur.fetchone()[0] or 0) >= daily_limit: raise RuntimeError("daily_limit_exceeded")
        if is_background:
            cur.execute("SELECT COUNT(*) FROM gemini_call_attempts WHERE created_at >= date_trunc('day', NOW()) AND is_background = TRUE AND status <> 'blocked'")
            if bg_limit <= 0 or int(cur.fetchone()[0] or 0) >= bg_limit: raise RuntimeError("background_limit_exceeded")
        if req_limit > 0:
            cur.execute("SELECT COUNT(*) FROM gemini_call_attempts WHERE request_id=%s AND status <> 'blocked'", (request_id,))
            if int(cur.fetchone()[0] or 0) >= req_limit: raise RuntimeError("request_limit_exceeded")
        if cycle_id and cycle_limit > 0:
            cur.execute("SELECT COUNT(*) FROM gemini_call_attempts WHERE cycle_id=%s AND status <> 'blocked'", (cycle_id,))
            if int(cur.fetchone()[0] or 0) >= cycle_limit: raise RuntimeError("cycle_limit_exceeded")
        cur.execute("""
          INSERT INTO gemini_call_attempts (request_id, cycle_id, job_id, feature, origin, user_id, chat_id, is_background, worker_id, model, status)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reserved') RETURNING id
        """, (request_id, cycle_id, job_id, feature, origin, user_id, chat_id, bool(is_background), worker_id, model))
        attempt_id = cur.fetchone()[0]; conn.commit(); return attempt_id
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()


def finalize_gemini_attempt(attempt_id, status, http_status=None, reason='', duration_ms=None, provider_request_id=None, prompt_tokens=None, completion_tokens=None, total_tokens=None, estimated_cost=0):
    conn=get_connection(); cur=conn.cursor()
    try:
        cur.execute("""UPDATE gemini_call_attempts SET status=%s,http_status=%s,reason=%s,finished_at=NOW(),duration_ms=%s,provider_request_id=%s,prompt_tokens=%s,completion_tokens=%s,total_tokens=%s,estimated_cost=%s WHERE id=%s""", (status,http_status,reason,duration_ms,provider_request_id,prompt_tokens,completion_tokens,total_tokens,estimated_cost,attempt_id))
        conn.commit()
    finally:
        cur.close(); conn.close()


def record_gemini_blocked_request(**kwargs):
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("""
          INSERT INTO gemini_call_attempts (request_id, cycle_id, job_id, feature, origin, user_id, chat_id, is_background, worker_id, model, status, reason, estimated_cost)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'blocked',%s,0)
        """, (
            kwargs.get("request_id"), kwargs.get("cycle_id"), kwargs.get("job_id"), kwargs.get("feature") or "",
            kwargs.get("origin") or "", kwargs.get("user_id"), kwargs.get("chat_id"), bool(kwargs.get("is_background")),
            kwargs.get("worker_id") or "", kwargs.get("model") or "", kwargs.get("reason") or "blocked",
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close(); conn.close()


def acquire_distributed_lock(lock_name: str, owner: str, ttl_seconds: int = 600) -> bool:
    conn=get_connection(); cur=conn.cursor()
    try:
        cur.execute("""
        INSERT INTO distributed_locks(lock_name, owner, expires_at) VALUES(%s,%s,NOW() + (%s || ' seconds')::interval)
        ON CONFLICT(lock_name) DO UPDATE SET owner=EXCLUDED.owner, expires_at=EXCLUDED.expires_at, updated_at=NOW()
        WHERE distributed_locks.expires_at < NOW() OR distributed_locks.owner = EXCLUDED.owner
        RETURNING owner
        """, (lock_name, owner, int(ttl_seconds)))
        ok = cur.fetchone() is not None; conn.commit(); return ok
    except Exception:
        conn.rollback(); return False
    finally:
        cur.close(); conn.close()


def release_distributed_lock(lock_name: str, owner: str) -> bool:
    conn=get_connection(); cur=conn.cursor()
    try:
        cur.execute("DELETE FROM distributed_locks WHERE lock_name=%s AND owner=%s", (lock_name, owner))
        ok = cur.rowcount > 0; conn.commit(); return ok
    except Exception:
        conn.rollback(); return False
    finally:
        cur.close(); conn.close()

# ═══════════════════════════════════════════
# TREASURY SECURITY HOTFIX HELPERS
# ═══════════════════════════════════════════

def get_active_treasury_wallet():
    from services.treasury_service import get_active_treasury_wallet as _svc
    return _svc()


def create_treasury_payout(payout_type: str, source_record_id: int, recipient_user_id: int, amount_nano: int, idempotency_key: str, conn=None):
    from services.treasury_service import resolve_internal_payout_wallet
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id,wallet_address FROM cashier_payment_wallets WHERE status='active' ORDER BY id ASC FOR UPDATE")
        treasuries = cur.fetchall() or []
        if len(treasuries) == 0:
            if own_conn: conn.rollback()
            return {"ok": False, "error": "treasury_not_configured"}
        if len(treasuries) > 1:
            if own_conn: conn.rollback()
            return {"ok": False, "error": "treasury_conflict"}
        treasury_wallet_id, treasury_address = int(treasuries[0][0]), str(treasuries[0][1])
        wallet = resolve_internal_payout_wallet(int(recipient_user_id), conn=conn, for_update=True)
        if not wallet.get("ok"):
            if own_conn: conn.rollback()
            return wallet
        cur.execute("""
            INSERT INTO treasury_payouts (payout_type,source_record_id,recipient_user_id,recipient_wallet_id,
              recipient_wallet_address,treasury_wallet_id,treasury_address,amount_nano,status,idempotency_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
        """, (payout_type, int(source_record_id or 0), int(recipient_user_id), int(wallet["wallet_id"]), wallet["wallet_address"], treasury_wallet_id, treasury_address, int(amount_nano), idempotency_key))
        row = cur.fetchone()
        if not row:
            if own_conn: conn.rollback()
            return {"ok": False, "error": "idempotency_conflict"}
        if own_conn: conn.commit()
        return {"ok": True, "payout_id": row[0], "recipient_wallet_address": wallet["wallet_address"]}
    except Exception as e:
        if own_conn: conn.rollback()
        print(f"create_treasury_payout error: {e}"); return {"ok": False, "error": "payout_create_failed"}
    finally:
        if own_conn: conn.close()


def create_author_withdrawal_to_treasury_payout(author_id: int, amount_nano: int, withdrawal_request_id: int = 0):
    from services.treasury_service import get_public_treasury_address, resolve_internal_payout_wallet
    conn = get_connection(); cur = conn.cursor()
    try:
        treasury = get_public_treasury_address()
        if not treasury.get("ok"):
            return treasury
        wallet = resolve_internal_payout_wallet(int(author_id), conn=conn, for_update=True)
        if not wallet.get("ok"):
            conn.rollback(); return wallet
        source_id = int(withdrawal_request_id or 0)
        idem = f"author_withdrawal:{source_id}" if source_id else f"author_user:{int(author_id)}:{int(amount_nano)}"
        cur.execute("""
            UPDATE users SET author_available_nano=author_available_nano-%s, author_reserved_nano=author_reserved_nano+%s
            WHERE user_id=%s AND author_available_nano >= %s
        """, (int(amount_nano), int(amount_nano), int(author_id), int(amount_nano)))
        if cur.rowcount != 1:
            conn.rollback(); return {"ok": False, "error": "insufficient_author_balance"}
        cur.execute("""
            INSERT INTO treasury_payouts (payout_type,source_record_id,recipient_user_id,recipient_wallet_id,recipient_wallet_address,treasury_wallet_id,treasury_address,amount_nano,status,idempotency_key)
            VALUES ('author',%s,%s,%s,%s,%s,%s,%s,'pending',%s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
        """, (source_id, int(author_id), int(wallet["wallet_id"]), wallet["wallet_address"], int(treasury["wallet_id"]), treasury["address"], int(amount_nano), idem))
        row = cur.fetchone()
        if not row:
            conn.rollback(); return {"ok": False, "error": "idempotency_conflict"}
        conn.commit(); return {"ok": True, "payout_id": row[0], "recipient_wallet_address": wallet["wallet_address"]}
    except Exception as e:
        conn.rollback(); print(f"create_author_withdrawal_to_treasury_payout error: {e}"); return {"ok": False, "error": "payout_create_failed"}
    finally:
        conn.close()

def create_referral_withdrawal_to_treasury_payout(request_id: int, user_id: int, amount_nano: int):
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM treasury_payouts WHERE payout_type='referral' AND source_record_id=%s ORDER BY id DESC LIMIT 1 FOR UPDATE", (int(request_id),))
        existing = cur.fetchone()
        if existing:
            conn.rollback(); return {"ok": True, "payout_id": existing[0], "existing": True}
        cur.execute("SELECT id,user_id,amount_nano,status FROM referral_reward_withdrawal_requests WHERE id=%s FOR UPDATE", (int(request_id),))
        req = cur.fetchone()
        if not req:
            conn.rollback(); return {"ok": False, "error": "request_not_found"}
        if str(req[3] or '') == 'processing':
            cur.execute("SELECT id FROM treasury_payouts WHERE payout_type='referral' AND source_record_id=%s ORDER BY id DESC LIMIT 1", (int(request_id),))
            row = cur.fetchone()
            conn.rollback(); return ({"ok": True, "payout_id": row[0], "existing": True} if row else {"ok": False, "error": "payout_create_incomplete"})
        if str(req[3] or '') != 'pending':
            conn.rollback(); return {"ok": False, "error": "request_not_pending"}
        cur.execute("UPDATE referral_reward_withdrawal_requests SET status='processing',processed_by=NULL WHERE id=%s AND status='pending'", (int(request_id),))
        if cur.rowcount != 1:
            conn.rollback(); return {"ok": False, "error": "request_not_pending"}
        created = create_treasury_payout("referral", int(request_id), int(user_id), int(amount_nano), f"referral:{request_id}", conn=conn)
        if not created.get("ok"):
            conn.rollback(); return created
        conn.commit(); return created
    except Exception:
        conn.rollback(); return {"ok": False, "error": "request_update_failed"}
    finally:
        conn.close()


def approve_and_send_treasury_payout(payout_id: int, admin_user_id: int):
    from services.treasury_service import get_public_treasury_address, resolve_internal_payout_wallet, send_from_treasury
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id,status,recipient_user_id,recipient_wallet_id,recipient_wallet_address,amount_nano,treasury_address,payout_type FROM treasury_payouts WHERE id=%s FOR UPDATE", (int(payout_id),))
        row = cur.fetchone()
        if not row: return {"ok": False, "error": "payout_not_found"}
        if row[1] in ("processing", "submitted", "paid", "payout_sent_reconcile_required"):
            return {"ok": False, "error": "payout_already_processing"}
        wallet = resolve_internal_payout_wallet(int(row[2]), conn=conn, for_update=True)
        treasury = get_public_treasury_address()
        if not wallet.get("ok") or not treasury.get("ok") or wallet.get("wallet_id") != row[3] or wallet.get("wallet_address") != row[4] or treasury.get("address") != row[6]:
            cur.execute("UPDATE treasury_payouts SET status='recipient_revalidation_required',fail_reason='recipient_or_treasury_changed' WHERE id=%s", (int(payout_id),)); conn.commit(); return {"ok": False, "error": "recipient_revalidation_required"}
        cur.execute("UPDATE treasury_payouts SET status='processing',approved_at=NOW() WHERE id=%s AND status IN ('pending','approved')", (int(payout_id),))
        if cur.rowcount != 1: conn.rollback(); return {"ok": False, "error": "concurrent_payout"}
        conn.commit()
    finally:
        conn.close()
    sent = send_from_treasury(str(row[4]), int(row[5]), comment=f"payout:{payout_id}")
    conn = get_connection(); cur = conn.cursor()
    try:
        if sent.get("ok"):
            cur.execute("UPDATE treasury_payouts SET status='submitted',submitted_at=NOW(),tx_hash=%s WHERE id=%s AND status='processing'", (sent.get("tx_hash"), int(payout_id)))
            if cur.rowcount != 1:
                conn.rollback()
                recover_conn = get_connection(); recover_cur = recover_conn.cursor()
                try:
                    recover_cur.execute("UPDATE treasury_payouts SET status='payout_sent_reconcile_required',tx_hash=%s,fail_reason=%s WHERE id=%s", (sent.get("tx_hash"), "TON sent but submitted update rowcount was zero", int(payout_id)))
                    recover_conn.commit()
                except Exception:
                    recover_conn.rollback()
                finally:
                    recover_conn.close()
                return {"ok": False, "error": "payout_sent_reconcile_required", "tx_hash": sent.get("tx_hash")}
            conn.commit(); return {"ok": True, "tx_hash": sent.get("tx_hash")}
        cur.execute("UPDATE treasury_payouts SET status='failed',fail_reason=%s WHERE id=%s AND status='processing'", (sent.get("error"), int(payout_id)))
        if str(row[7] or '') == 'author':
            cur.execute("UPDATE users SET author_reserved_nano=author_reserved_nano-%s, author_available_nano=author_available_nano+%s WHERE user_id=%s", (int(row[5]), int(row[5]), int(row[2])))
        conn.commit(); return sent
    except Exception:
        conn.rollback()
        if sent.get("ok") and sent.get("tx_hash"):
            recover_conn = get_connection(); recover_cur = recover_conn.cursor()
            try:
                recover_cur.execute("UPDATE treasury_payouts SET status='payout_sent_reconcile_required',tx_hash=%s,fail_reason=%s WHERE id=%s", (sent.get("tx_hash"), "TON sent but DB finalization failed", int(payout_id)))
                recover_conn.commit()
            except Exception:
                recover_conn.rollback()
            finally:
                recover_conn.close()
        return {"ok": False, "error": "payout_sent_reconcile_required", "tx_hash": sent.get("tx_hash")}
    finally:
        conn.close()


def create_donation_with_payment_intent(donor_id: int, author_id: int, amount_nano: int, post_id: Optional[int] = None, comment: str = "") -> Dict[str, Any]:
    """Atomically creates pending donation + immutable treasury payment intent. No orphan donation on intent failure."""
    from services.treasury_service import incoming_enabled, build_ton_text_comment_payload_boc, ton_network_id
    if not incoming_enabled():
        return {"ok": False, "error": "treasury_incoming_disabled"}
    gross = int(amount_nano)
    if gross <= 0:
        return {"ok": False, "error": "invalid_amount"}
    fee_percent = float(get_setting("platform_fee_percent", "20"))
    platform_fee_nano = int(gross * fee_percent / 100)
    author_net_nano = gross - platform_fee_nano
    ton_amount = gross / 1_000_000_000
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id,wallet_address,network FROM cashier_payment_wallets WHERE status='active' ORDER BY id ASC FOR UPDATE")
        treasuries = cur.fetchall() or []
        if len(treasuries) == 0:
            conn.rollback(); return {"ok": False, "error": "treasury_not_configured"}
        if len(treasuries) > 1:
            conn.rollback(); return {"ok": False, "error": "treasury_conflict"}
        tr = treasuries[0]; treasury_id, treasury_address = int(tr[0]), str(tr[1])
        platform_fee_ton = platform_fee_nano / 1_000_000_000
        author_received_ton = author_net_nano / 1_000_000_000
        cur.execute("""
            INSERT INTO author_donations (donor_id,author_id,post_id,ton_amount,platform_fee_ton,author_received_ton,
              gross_amount_nano,platform_fee_nano,author_net_amount_nano,status,comment,created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s)
            RETURNING id
        """, (int(donor_id), int(author_id), post_id, ton_amount, platform_fee_ton, author_received_ton, gross, platform_fee_nano, author_net_nano, comment, datetime.utcnow().isoformat()))
        donation_id = int(cur.fetchone()[0])
        public_reference = "pay_" + __import__('uuid').uuid4().hex
        payload_boc = build_ton_text_comment_payload_boc(public_reference)
        idempotency_key = f"donation:{donation_id}"
        cur.execute("""
            INSERT INTO payment_intents (public_reference,user_id,product_type,product_ref,expected_amount_nano,
              treasury_wallet_id,treasury_address,status,expires_at,metadata_json,idempotency_key)
            VALUES (%s,%s,'donation',%s,%s,%s,%s,'pending',NOW() + INTERVAL '30 minutes',%s,%s)
            RETURNING id
        """, (public_reference, int(donor_id), str(donation_id), gross, treasury_id, treasury_address, json.dumps({"donation_id": donation_id, "author_id": int(author_id), "post_id": post_id}, ensure_ascii=False), idempotency_key))
        intent_id = int(cur.fetchone()[0])
        cur.execute("UPDATE author_donations SET payment_intent_id=%s WHERE id=%s", (intent_id, donation_id))
        conn.commit()
        return {"ok": True, "donation_id": donation_id, "payment_intent": {"id": intent_id, "public_reference": public_reference, "treasury_address": treasury_address, "amount_nano": gross, "payload_boc": payload_boc, "network_id": ton_network_id(os.getenv("TON_NETWORK", "mainnet"))}}
    except Exception as e:
        conn.rollback(); print(f"create_donation_with_payment_intent error: {type(e).__name__}"); return {"ok": False, "error": "intent_create_failed"}
    finally:
        conn.close()



def _calculate_tokens_for_amount_conn(cur, ton_amount: float) -> int:
    try:
        cur.execute("SELECT tokens,price_ton FROM token_packages WHERE is_active=1 ORDER BY sort_order,id")
        for tokens, price in (cur.fetchall() or []):
            try:
                if abs(float(price) - float(ton_amount)) <= 0.05:
                    return int(tokens)
            except Exception:
                continue
        cur.execute("SELECT value FROM settings WHERE key='token_price_ton'")
        row = cur.fetchone()
        token_price = float(row[0]) if row and row[0] else 0.1
        if token_price <= 0:
            token_price = 0.1
        return int(float(ton_amount) / token_price)
    except Exception:
        return int(float(ton_amount) / 0.1)


def _set_subscription_conn(cur, user_id: int, days: int) -> str:
    now = datetime.utcnow()
    cur.execute("SELECT subscription_until FROM users WHERE user_id=%s FOR UPDATE", (int(user_id),))
    row = cur.fetchone()
    base = now
    if row and row[0]:
        try:
            current_dt = datetime.fromisoformat(str(row[0]))
            if current_dt > now:
                base = current_dt
        except Exception:
            pass
    until = (base + timedelta(days=int(days))).isoformat()
    cur.execute("UPDATE users SET subscription_until=%s,updated_at=%s WHERE user_id=%s", (until, datetime.utcnow().isoformat(), int(user_id)))
    return until


def fulfill_verified_payment_intent(intent_id: int) -> Dict[str, Any]:
    """Atomically deliver a verified payment intent exactly once."""
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id,user_id,product_type,product_ref,expected_amount_nano,status,tx_hash,metadata_json FROM payment_intents WHERE id=%s FOR UPDATE", (int(intent_id),))
        intent = cur.fetchone()
        if not intent:
            conn.rollback(); return {"ok": False, "error": "intent_not_found"}
        iid, user_id, product_type, product_ref, amount_nano, status, tx_hash, metadata_json = intent
        if status == 'fulfilled':
            conn.rollback(); return {"ok": True, "already_fulfilled": True}
        if status != 'verified':
            conn.rollback(); return {"ok": False, "error": "intent_not_verified"}
        tx_hash = str(tx_hash or '').strip()
        if not tx_hash:
            conn.rollback(); return {"ok": False, "error": "tx_hash_missing"}
        ton_amount = int(amount_nano or 0) / 1_000_000_000
        metadata = {}
        try:
            metadata = json.loads(metadata_json or '{}') if metadata_json else {}
        except Exception:
            metadata = {}
        cur.execute("""
            INSERT INTO transactions (tx_hash,user_id,ton_amount,tokens_granted,referral_bonus_ton,referrer_id,created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tx_hash) DO NOTHING
        """, (tx_hash, int(user_id), ton_amount, 0, 0, None, datetime.utcnow().isoformat()))
        inserted_ledger = cur.rowcount == 1
        if not inserted_ledger:
            cur.execute("UPDATE payment_intents SET status='fulfilled',fulfilled_at=COALESCE(fulfilled_at,NOW()) WHERE id=%s AND status='verified'", (int(iid),))
            conn.commit(); return {"ok": True, "already_fulfilled": True}
        tokens_granted = 0; referral_bonus_ton = 0; referrer_id = None; result = {"ok": True, "product_type": product_type}
        if product_type == 'donation':
            donation_id = int(product_ref)
            cur.execute("SELECT donor_id,author_id,author_net_amount_nano,status FROM author_donations WHERE id=%s FOR UPDATE", (donation_id,))
            d = cur.fetchone()
            if not d:
                raise RuntimeError('donation_not_found')
            if str(d[3] or '') != 'paid':
                cur.execute("UPDATE author_donations SET status='paid',tx_hash=%s WHERE id=%s AND status='pending'", (tx_hash, donation_id))
                if cur.rowcount == 1:
                    cur.execute("UPDATE users SET author_available_nano=author_available_nano+%s,author_balance_ton=author_balance_ton+(%s::float/1000000000.0),updated_at=%s WHERE user_id=%s", (int(d[2] or 0), int(d[2] or 0), datetime.utcnow().isoformat(), int(d[1])))
            result["donation_id"] = donation_id
        elif product_type == 'subscription':
            days = int(str(get_setting('subscription_days', '30') or '30'))
            result["subscription_until"] = _set_subscription_conn(cur, int(user_id), days)
        elif product_type == 'author_status':
            cur.execute("UPDATE users SET is_author=1,author_status=1,updated_at=%s WHERE user_id=%s", (datetime.utcnow().isoformat(), int(user_id)))
        else:
            tokens_granted = int(metadata.get('tokens') or metadata.get('total_tokens') or 0)
            if tokens_granted <= 0:
                tokens_granted = _calculate_tokens_for_amount_conn(cur, ton_amount)
            if tokens_granted > 0:
                cur.execute("UPDATE users SET token_balance=token_balance+%s,updated_at=%s WHERE user_id=%s", (tokens_granted, datetime.utcnow().isoformat(), int(user_id)))
        if product_type in ('tokens', 'subscription'):
            cur.execute("SELECT referred_by FROM users WHERE user_id=%s", (int(user_id),))
            u = cur.fetchone()
            if u and u[0]:
                referrer_id = int(u[0])
                cur.execute("SELECT value FROM settings WHERE key='referral_percent'")
                rr = cur.fetchone()
                try: ref_percent = float(rr[0]) if rr and rr[0] else 10.0
                except Exception: ref_percent = 10.0
                referral_bonus_ton = round(ton_amount * ref_percent / 100, 6)
                if referral_bonus_ton > 0:
                    cur.execute("UPDATE users SET referral_earnings_ton=COALESCE(referral_earnings_ton,0)+%s,updated_at=%s WHERE user_id=%s", (referral_bonus_ton, datetime.utcnow().isoformat(), referrer_id))
        cur.execute("UPDATE transactions SET tokens_granted=%s,referral_bonus_ton=%s,referrer_id=%s WHERE tx_hash=%s", (tokens_granted, referral_bonus_ton, referrer_id, tx_hash))
        cur.execute("UPDATE payment_intents SET status='fulfilled',fulfilled_at=NOW() WHERE id=%s AND status='verified'", (int(iid),))
        if cur.rowcount != 1:
            raise RuntimeError('intent_state_changed')
        conn.commit(); result.update({"tokens_granted": tokens_granted, "referral_bonus_ton": referral_bonus_ton, "referrer_id": referrer_id, "tx_hash": tx_hash}); return result
    except Exception as e:
        conn.rollback(); print(f"fulfill_verified_payment_intent error: {type(e).__name__}"); return {"ok": False, "error": "fulfillment_failed"}
    finally:
        conn.close()

def fulfill_verified_donation_intent(intent_id: int) -> Dict[str, Any]:
    """Backward-compatible wrapper for donation intents; uses unified exactly-once fulfillment."""
    return fulfill_verified_payment_intent(intent_id)


def set_treasury_transaction_cursor(last_lt: str, last_hash: str, backlog_lt: str = "", backlog_hash: str = "") -> None:
    """Atomically persist treasury scan cursor and optional backlog page cursor."""
    conn = get_connection(); cur = conn.cursor()
    try:
        now = datetime.utcnow().isoformat()
        rows = [
            ("treasury_last_processed_lt", str(last_lt or ""), now),
            ("treasury_last_processed_hash", str(last_hash or ""), now),
            ("treasury_scan_page_lt", str(backlog_lt or ""), now),
            ("treasury_scan_page_hash", str(backlog_hash or ""), now),
        ]
        cur.executemany("""
            INSERT INTO settings (key,value,updated_at) VALUES (%s,%s,%s)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at
        """, rows)
        conn.commit()
    except Exception as e:
        conn.rollback(); print(f"set_treasury_transaction_cursor error: {e}")
        raise
    finally:
        conn.close()


def get_pending_payment_intents(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT id,public_reference,user_id,product_type,product_ref,expected_amount_nano,treasury_address,expected_sender_address,status,expires_at,tx_hash
                       FROM payment_intents WHERE ((status='pending' AND expires_at > NOW()) OR status='verified') ORDER BY created_at ASC LIMIT %s""", (int(limit),))
        rows = cur.fetchall() or []
        return [{"id": r[0], "public_reference": r[1], "user_id": int(r[2]), "product_type": r[3], "product_ref": r[4], "expected_amount_nano": int(r[5]), "treasury_address": r[6], "expected_sender_address": r[7], "status": r[8], "expires_at": r[9], "tx_hash": (r[10] if len(r) > 10 else None)} for r in rows]
    except Exception as e:
        print(f"get_pending_payment_intents error: {e}"); return []
    finally:
        conn.close()

def finalize_treasury_payout_reconciliation(payout_id: int, admin_user_id: int = 0) -> Dict[str, Any]:
    """Complete accounting for a payout already sent on-chain; never resends."""
    from services.treasury_service import verify_treasury_payout_onchain
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("""SELECT id,payout_type,source_record_id,recipient_user_id,recipient_wallet_id,recipient_wallet_address,
                          treasury_wallet_id,treasury_address,amount_nano,status,tx_hash
                       FROM treasury_payouts WHERE id=%s FOR UPDATE""", (int(payout_id),))
        row = cur.fetchone()
        if not row:
            conn.rollback(); return {"ok": False, "error": "payout_not_found"}
        payout = {
            "id": row[0], "payout_type": row[1], "source_record_id": row[2], "recipient_user_id": row[3],
            "recipient_wallet_id": row[4], "recipient_wallet_address": row[5], "treasury_wallet_id": row[6],
            "treasury_address": row[7], "amount_nano": int(row[8] or 0), "status": row[9], "tx_hash": row[10],
        }
        if payout["status"] == 'paid':
            conn.rollback(); return {"ok": True, "already_paid": True}
        if payout["status"] not in ('submitted', 'payout_sent_reconcile_required'):
            conn.rollback(); return {"ok": False, "error": "invalid_status"}
        chain = verify_treasury_payout_onchain(payout)
        if not chain.get("ok"):
            cur.execute("UPDATE treasury_payouts SET status='payout_sent_reconcile_required',fail_reason=%s WHERE id=%s", (str(chain.get("error") or "onchain_mismatch"), int(payout_id)))
            conn.commit(); return {"ok": False, "error": chain.get("error") or "onchain_mismatch"}
        cur.execute("UPDATE treasury_payouts SET status='paid',paid_at=NOW(),fail_reason=NULL WHERE id=%s AND status IN ('submitted','payout_sent_reconcile_required')", (int(payout_id),))
        if cur.rowcount != 1:
            conn.rollback(); return {"ok": False, "error": "payout_state_changed"}
        if str(payout["payout_type"] or '') == 'author':
            cur.execute("UPDATE users SET author_reserved_nano=author_reserved_nano-%s,author_withdrawn_nano=author_withdrawn_nano+%s,author_withdrawn_ton=author_withdrawn_ton+(%s::float/1000000000.0) WHERE user_id=%s AND author_reserved_nano >= %s", (payout["amount_nano"], payout["amount_nano"], payout["amount_nano"], int(payout["recipient_user_id"]), payout["amount_nano"]))
            if cur.rowcount != 1:
                conn.rollback(); return {"ok": False, "error": "accounting_mismatch"}
        if str(payout["payout_type"] or '') == 'referral':
            src = int(payout["source_record_id"] or 0)
            cur.execute("UPDATE referral_reward_withdrawal_requests SET status='paid',tx_hash=%s,processed_at=NOW(),processed_by=%s WHERE id=%s AND status='processing'", (str(payout["tx_hash"]), int(admin_user_id or 0), src))
            if cur.rowcount != 1:
                conn.rollback(); return {"ok": False, "error": "referral_request_state_changed"}
            cur.execute("UPDATE referral_rewards SET status='withdrawn',withdrawn_at=NOW(),withdrawal_tx_hash=%s,updated_at=NOW() WHERE withdrawal_request_id=%s AND status='pending_admin_review'", (str(payout["tx_hash"]), src))
        conn.commit(); return {"ok": True, "tx_hash": str(payout["tx_hash"])}
    except Exception:
        conn.rollback(); return {"ok": False, "error": "reconciliation_failed"}
    finally:
        conn.close()


def mark_payment_intent_fulfilled(intent_id: int) -> bool:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("UPDATE payment_intents SET status='fulfilled',fulfilled_at=NOW() WHERE id=%s AND status='verified'", (int(intent_id),))
        ok = cur.rowcount == 1
        conn.commit()
        return ok
    except Exception:
        conn.rollback(); return False
    finally:
        conn.close()
