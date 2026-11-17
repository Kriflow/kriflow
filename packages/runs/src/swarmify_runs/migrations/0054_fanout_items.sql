-- Fan-out Stages (ADR 0031): one row per Item of a fan-out stage's visit, with the child run it
-- started. `position` keeps the source's order; `source` is what the child is given, as JSON: a
-- card's file ({"file": path}) or a JSON list's element ({"data": element}).
CREATE TABLE fanout_items (
  run_id       TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
  stage_index  INTEGER NOT NULL,
  visit        INTEGER NOT NULL,
  item_key     TEXT NOT NULL,
  item_title   TEXT NOT NULL,
  position     INTEGER NOT NULL,
  source       TEXT NOT NULL,
  child_run_id TEXT,
  status       TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed', 'aborted')),
  error        TEXT,
  PRIMARY KEY (run_id, stage_index, visit, item_key)
);
CREATE INDEX fanout_items_child ON fanout_items (child_run_id);
