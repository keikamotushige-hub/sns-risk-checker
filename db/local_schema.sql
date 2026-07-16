-- Local SQLite schema for VirtualBox / offline deploy
-- Applied automatically on first app start when using local auth.

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS risk_checks (
  id TEXT PRIMARY KEY,
  user_id TEXT,
  input_text TEXT NOT NULL,
  result TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Lifetime free-trial counter (3 uses per account)
CREATE TABLE IF NOT EXISTS user_trial_usage (
  user_id TEXT PRIMARY KEY,
  use_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
