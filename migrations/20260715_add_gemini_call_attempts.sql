CREATE TABLE IF NOT EXISTS gemini_call_attempts (
    attempt_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    cycle_id TEXT,
    job_id TEXT,
    feature TEXT NOT NULL,
    origin TEXT NOT NULL,
    user_id BIGINT,
    chat_id BIGINT,
    is_background INTEGER NOT NULL DEFAULT 0,
    worker_id TEXT,
    replica_id TEXT,
    model TEXT,
    status TEXT NOT NULL,
    status_code INTEGER,
    reason TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms INTEGER,
    estimated_cost_usd REAL DEFAULT 0,
    provider_request_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_gemini_attempts_started ON gemini_call_attempts(started_at);
CREATE INDEX IF NOT EXISTS idx_gemini_attempts_request ON gemini_call_attempts(request_id);
CREATE INDEX IF NOT EXISTS idx_gemini_attempts_cycle ON gemini_call_attempts(cycle_id);
CREATE TABLE IF NOT EXISTS background_locks (
    lock_name TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
