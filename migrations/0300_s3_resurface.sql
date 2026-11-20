-- S3 resurfacing & staleness (PRD §5 S3).
--
-- Per-item staleness bookkeeping lives in its own table rather than new
-- `items` columns, so it never shows up in other slices' `item.extra`.
--
-- reset_at:     set when the owner clicks "Keep"; restarts the staleness clock (FR-33).
-- suggested_at: when `stale_suggested` was emitted. A suggestion belongs to the
--               current clock only if it is not older than the clock start, so a
--               new decision, new external activity or a Keep re-arms it (FR-32).

CREATE TABLE s3_stale_state (
  item_id      INTEGER PRIMARY KEY REFERENCES items (id),
  reset_at     TEXT,
  suggested_at TEXT
);
