-- S0 core schema. See PRD §4.3. Slices add columns/tables in their own ranges.

CREATE TABLE items (
  id                     INTEGER PRIMARY KEY,
  title                  TEXT    NOT NULL,
  source                 TEXT    NOT NULL CHECK (source IN ('manual', 'email')),
  source_ref             TEXT,
  raw_context            TEXT,
  ask_card_json          TEXT,
  pressure_count         INTEGER NOT NULL DEFAULT 0,
  type                   TEXT CHECK (type IS NULL OR type IN ('incident', 'interrupt', 'toil', 'planned')),
  status                 TEXT    NOT NULL,
  outcome                TEXT CHECK (outcome IS NULL OR outcome IN ('do_now', 'timebox', 'schedule', 'delegate', 'decline')),
  priority               INTEGER CHECK (priority IS NULL OR priority BETWEEN 1 AND 4),
  timebox_end            TEXT,
  scheduled_for          TEXT,
  delegated_to           TEXT,
  last_external_activity TEXT,
  created_at             TEXT    NOT NULL,
  decided_at             TEXT,
  closed_at              TEXT
);

CREATE INDEX idx_items_status     ON items (status);
CREATE INDEX idx_items_source_ref ON items (source_ref);

CREATE TABLE item_events (
  id           INTEGER PRIMARY KEY,
  item_id      INTEGER NOT NULL REFERENCES items (id),
  kind         TEXT    NOT NULL,
  payload_json TEXT    NOT NULL DEFAULT '{}',
  at           TEXT    NOT NULL
);

CREATE INDEX idx_events_item    ON item_events (item_id);
CREATE INDEX idx_events_kind_at ON item_events (kind, at);
