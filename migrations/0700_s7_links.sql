-- S7 dependency links between items: "blocker must happen before blocked can finish".
-- One row per ordered pair. A dismissed pair is kept so AI scans never revive it.

CREATE TABLE item_links (
  id         INTEGER PRIMARY KEY,
  blocker_id INTEGER NOT NULL REFERENCES items (id),
  blocked_id INTEGER NOT NULL REFERENCES items (id),
  reason     TEXT    NOT NULL,
  source     TEXT    NOT NULL CHECK (source IN ('ai', 'user')),
  status     TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'dismissed')),
  created_at TEXT    NOT NULL,
  UNIQUE (blocker_id, blocked_id)
);

CREATE INDEX idx_item_links_blocked ON item_links (blocked_id);

-- Items the background job (or the S1 gate) has already compared with the open set.
CREATE TABLE s7_scans (
  item_id    INTEGER PRIMARY KEY,
  scanned_at TEXT    NOT NULL
);
