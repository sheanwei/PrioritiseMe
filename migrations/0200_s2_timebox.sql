-- S2 triage timebox (PRD §5 S2). Additive item columns only.
--   reassess            1 = the timebox ended and the item needs a re-assessment (FR-21/23)
--   reassess_note       "What did you learn?" note; S1 passes it to the LLM (FR-23)
--   timebox_expired_at  when the expiry job last moved the item back to the tray
--   expiry_notified_at  when a dashboard claimed the notification for that expiry (FR-22);
--                       NULL while the latest expiry has not been shown yet

ALTER TABLE items ADD COLUMN reassess INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN reassess_note TEXT;
ALTER TABLE items ADD COLUMN timebox_expired_at TEXT;
ALTER TABLE items ADD COLUMN expiry_notified_at TEXT;

CREATE INDEX idx_items_s2_reassess ON items (reassess, status);
