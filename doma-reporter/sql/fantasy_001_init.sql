-- Doma Fantasy League — initial schema (10-day round model).
--
-- Each round is a self-contained 10-day contest:
--   - 3 days drafting (users build their 10-domain portfolio)
--   - 7 days scoring (held positions tracked against live FDV)
-- Three rounds per calendar month, back-to-back. No swaps, no carryover.
-- Each round has its own snapshot, budget, holdings, scores, and leaderboard.
--
-- Run against the Supabase project that hosts doma_reporter_messages.
-- Idempotent: safe to re-run.

-- ============================================================
-- Cross-game user table.
-- discord_id is the durable identity. wallet_addr is collected
-- just-in-time via Discord modal when a user wins; nullable.
-- ============================================================
CREATE TABLE IF NOT EXISTS fantasy_users (
  discord_id      TEXT PRIMARY KEY,
  wallet_addr     TEXT,
  x_handle        TEXT,
  total_rep_score INT NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- A round = one 10-day contest. Operator creates these in advance
-- (or via !fantasy create-round-now for ad-hoc testing).
-- budget_usd is locked at round creation, computed as 35% of the
-- top-10 FDV sum from the round's snapshot.
-- ============================================================
CREATE TABLE IF NOT EXISTS fantasy_rounds (
  round_id        SERIAL PRIMARY KEY,
  name            TEXT NOT NULL,
  draft_opens_at  TIMESTAMPTZ NOT NULL,
  draft_locks_at  TIMESTAMPTZ NOT NULL,
  resolves_at     TIMESTAMPTZ NOT NULL,
  budget_usd      NUMERIC NOT NULL,
  status          TEXT NOT NULL DEFAULT 'UPCOMING'
                  CHECK (status IN ('UPCOMING','DRAFTING','ACTIVE','COMPLETE')),
  pool_filter     JSONB NOT NULL DEFAULT
                  '{"min_holders":25,"min_volume_usd":50,"or_min_fdv_usd":1000}'::jsonb,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (draft_opens_at < draft_locks_at AND draft_locks_at < resolves_at)
);

CREATE INDEX IF NOT EXISTS fantasy_rounds_status_idx
  ON fantasy_rounds(status, draft_opens_at);

-- ============================================================
-- One snapshot per round, taken at draft_opens_at. The prices in
-- this snapshot are the cost-basis users pay during drafting.
-- ============================================================
CREATE TABLE IF NOT EXISTS fantasy_pool_snapshots (
  snapshot_id          SERIAL PRIMARY KEY,
  round_id             INT NOT NULL REFERENCES fantasy_rounds(round_id) ON DELETE CASCADE,
  snapshot_at          TIMESTAMPTZ NOT NULL,
  total_market_fdv_usd NUMERIC,
  top10_fdv_sum_usd    NUMERIC,
  eligible_count       INT NOT NULL,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (round_id)
);

-- Per-token prices for that snapshot. token_address is CAIP-10.
CREATE TABLE IF NOT EXISTS fantasy_pool_prices (
  snapshot_id    INT NOT NULL REFERENCES fantasy_pool_snapshots(snapshot_id) ON DELETE CASCADE,
  token_address  TEXT NOT NULL,
  domain_name    TEXT NOT NULL,
  fdv_usd        NUMERIC NOT NULL,
  price_usd      NUMERIC,
  volume_usd     NUMERIC,
  holder_count   INT,
  status         TEXT,
  tier           TEXT CHECK (tier IN ('PREMIUM','UPPER_MID','MID','SMALL')),
  PRIMARY KEY (snapshot_id, token_address)
);

CREATE INDEX IF NOT EXISTS fantasy_pool_prices_domain_idx
  ON fantasy_pool_prices(domain_name);

-- ============================================================
-- Held positions. Drafted during DRAFTING, locked at draft_locks_at.
-- No swaps in this model — the row is immutable until round resolves.
-- ============================================================
CREATE TABLE IF NOT EXISTS fantasy_holdings (
  round_id            INT NOT NULL REFERENCES fantasy_rounds(round_id) ON DELETE CASCADE,
  discord_id          TEXT NOT NULL,
  token_address       TEXT NOT NULL,
  domain_name         TEXT NOT NULL,
  cost_basis_fdv_usd  NUMERIC NOT NULL,
  drafted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (round_id, discord_id, token_address)
);

CREATE INDEX IF NOT EXISTS fantasy_holdings_user_idx
  ON fantasy_holdings(round_id, discord_id);

-- ============================================================
-- Daily score snapshots — feeds leaderboard + history charts.
-- Computed by daily cron at 12:00 UTC against live FDVs.
-- ============================================================
CREATE TABLE IF NOT EXISTS fantasy_scores_daily (
  round_id            INT NOT NULL REFERENCES fantasy_rounds(round_id) ON DELETE CASCADE,
  discord_id          TEXT NOT NULL,
  snapshot_date       DATE NOT NULL,
  holdings_value_usd  NUMERIC NOT NULL,
  pct_growth          NUMERIC,
  PRIMARY KEY (round_id, discord_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS fantasy_scores_daily_leaderboard_idx
  ON fantasy_scores_daily(round_id, snapshot_date, holdings_value_usd DESC);

-- ============================================================
-- Share-card artifact log (PNG URLs hosted on web3guides @vercel/og).
-- ============================================================
CREATE TABLE IF NOT EXISTS fantasy_share_cards (
  card_id      SERIAL PRIMARY KEY,
  discord_id   TEXT,
  event_type   TEXT NOT NULL,
  round_id     INT,
  image_url    TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- One-time magic-link codes. Issued by !fantasy enter in Discord,
-- consumed by /api/fantasy/enter on web3guides which sets a
-- HttpOnly session cookie. Codes are single-use and expire fast
-- (30 min default). Issuing a new code for a user invalidates
-- their unused previous codes.
-- ============================================================
CREATE TABLE IF NOT EXISTS fantasy_auth_codes (
  code        TEXT PRIMARY KEY,
  discord_id  TEXT NOT NULL,
  issued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at  TIMESTAMPTZ NOT NULL,
  used_at     TIMESTAMPTZ,
  user_agent  TEXT,
  ip_address  TEXT
);

CREATE INDEX IF NOT EXISTS fantasy_auth_codes_user_idx
  ON fantasy_auth_codes(discord_id, used_at);
