import os
import json
import time
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2 import errors

DATABASE_URL = os.getenv("DATABASE_URL", "")


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing")
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

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
        last_checked_at TEXT
    )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_slug ON watchlist(market_slug)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_closed ON watchlist(is_closed)")

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

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_author ON author_posts(author_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_created ON author_posts(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_deleted ON author_posts(is_deleted)")

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
    ]
    for migration in migrations:
        try:
            cursor.execute(migration)
        except Exception:
            pass

    conn.commit()

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

    conn.close()


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

def ensure_user(user_id: int, username: str = "", first_name: str = "", referred_by: Optional[int] = None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.utcnow().isoformat()
        cursor.execute("SELECT user_id, referred_by FROM users WHERE user_id = %s", (user_id,))
        existing = cursor.fetchone()

        if existing:
            cursor.execute("""
            UPDATE users SET username = %s, first_name = %s, updated_at = %s
            WHERE user_id = %s
            """, (username, first_name, now, user_id))

            if referred_by and not existing[1] and referred_by != user_id:
                cursor.execute("""
                UPDATE users SET referred_by = %s WHERE user_id = %s
                """, (referred_by, user_id))
                cursor.execute("""
                UPDATE users SET total_referrals = COALESCE(total_referrals, 0) + 1
                WHERE user_id = %s
                """, (referred_by,))
        else:
            cursor.execute("""
            INSERT INTO users (user_id, username, first_name, referred_by, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """, (user_id, username, first_name, referred_by, now, now))

            if referred_by and referred_by != user_id:
                cursor.execute("""
                UPDATE users SET total_referrals = COALESCE(total_referrals, 0) + 1
                WHERE user_id = %s
                """, (referred_by,))

        conn.commit()
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
        cur.execute("SELECT id FROM ton_purchase_intents WHERE tx_hash=%s AND id<>%s", (h, intent_id))
        if cur.fetchone(): conn.rollback(); return None
        cur.execute("UPDATE ton_purchase_intents SET status='submitted', tx_hash=%s, submitted_at=COALESCE(submitted_at,%s), updated_at=%s WHERE id=%s RETURNING *", (h, now, now, intent_id))
        out=cur.fetchone()
        cur.execute("UPDATE ton_wallet_transactions SET product_type=%s,payment_intent_id=%s,purchase_status='submitted',updated_at=%s WHERE tx_hash=%s", (row['product_type'], intent_id, now, h))
        conn.commit(); return dict(out) if out else None
    except Exception as e:
        print(f"submit_ton_purchase_intent error: {e}"); conn.rollback(); return None
    finally: conn.close()

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
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT user_id, username, first_name, total_referrals, referral_earnings_ton
        FROM users WHERE total_referrals > 0
        ORDER BY total_referrals DESC, referral_earnings_ton DESC LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_top_referrers error: {e}")
        return []
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
        SELECT * FROM author_posts WHERE id = %s AND is_deleted = 0
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
               display_prediction, confidence, market_probability,
               alpha_label, author_comment,
               total_donations_ton, total_donors, created_at
        FROM author_posts
        WHERE author_id = %s AND is_deleted = 0
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
               u.username, u.first_name
        FROM author_posts p
        JOIN author_subscriptions s ON s.author_id = p.author_id
        JOIN users u ON u.user_id = p.author_id
        WHERE s.subscriber_id = %s AND p.is_deleted = 0
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
                   created_at, last_checked_at
            FROM watchlist WHERE user_id = %s ORDER BY id DESC
            """, (user_id,))
        else:
            cursor.execute("""
            SELECT id, market_slug, market_url, question, category,
                   initial_probability, last_checked_probability,
                   last_probability_change, market_end_date,
                   notify_enabled, is_closed, extra_slot,
                   created_at, last_checked_at
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


def get_active_watchlist_items(limit: int = 500) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT DISTINCT market_slug, market_url, question, category, market_end_date
        FROM watchlist WHERE is_closed = 0 LIMIT %s
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
               notified_resolved, market_end_date
        FROM watchlist WHERE market_slug = %s AND is_closed = 0
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
               created_at, last_checked_at
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
