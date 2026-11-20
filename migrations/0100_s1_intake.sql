-- S1 intake gate: per-item intake answers and the latest analysis.
-- Kept in S1's own table (one row per item) so the shared `items` table stays untouched.

CREATE TABLE s1_intake (
  item_id       INTEGER PRIMARY KEY REFERENCES items (id),
  intake_json   TEXT,                       -- the four answers: cost_of_delay, deadline_real, blocked_count, delegable
  analysis_json TEXT,                       -- latest suggestion after guardrails + production_impact / size_known
  analyzed_at   TEXT,                       -- when analysis_json was produced; stale once the item is decided again
  log_pending   INTEGER NOT NULL DEFAULT 0  -- 1 = incident fast path; ask card + answers still to log (FR-17)
);

CREATE INDEX idx_s1_intake_log_pending ON s1_intake (log_pending);
