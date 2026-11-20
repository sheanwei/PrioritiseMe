-- S4 Outlook ingestion (PRD FR-43..FR-46). One row per mail message, deduplicated by id.
-- Spec columns: id, conversation_id, from, subject, received_at, body_preview,
-- classification, user_corrected. The rest is S4 bookkeeping.

CREATE TABLE emails (
  id                    TEXT    PRIMARY KEY,           -- Graph message id
  conversation_id       TEXT,
  "from"                TEXT,                          -- "Name <address>"
  subject               TEXT,
  received_at           TEXT,                          -- UTC ISO-8601
  body_preview          TEXT,
  classification        TEXT CHECK (classification IS NULL OR classification IN ('needs_action', 'fyi', 'noise')),
  user_corrected        INTEGER NOT NULL DEFAULT 0,
  llm_classification    TEXT,                          -- what the model said before any correction
  classification_reason TEXT,
  item_id               INTEGER REFERENCES items (id), -- item this message created or bumped
  item_role             TEXT CHECK (item_role IS NULL OR item_role IN ('created', 'bumped')),
  fetched_at            TEXT    NOT NULL,
  classified_at         TEXT,
  corrected_at          TEXT
);

CREATE INDEX idx_emails_conversation   ON emails (conversation_id);
CREATE INDEX idx_emails_classification ON emails (classification, received_at);
CREATE INDEX idx_emails_corrected      ON emails (user_corrected, corrected_at);
