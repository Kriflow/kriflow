-- World Lists (issue 539): named, world-wide lists of Entries any workflow of the world shares.
-- A list declares its statuses (JSON lists); the first is where an added Entry starts unless
-- the adder names another. `done` and `rejected` name which statuses count as such.
CREATE TABLE world_lists (
  world_id   TEXT NOT NULL,
  name       TEXT NOT NULL,
  statuses   TEXT NOT NULL,
  done       TEXT NOT NULL DEFAULT '[]',
  rejected   TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (world_id, name)
);

-- One row per Entry, unique by key within its list. `fields` is a small JSON object; `runs` the
-- JSON list of run ids that held it; `claimed_by` the run holding it now. Its history is events.
CREATE TABLE list_entries (
  world_id   TEXT NOT NULL,
  list       TEXT NOT NULL,
  key        TEXT NOT NULL,
  title      TEXT NOT NULL,
  status     TEXT NOT NULL,
  score      REAL,
  fields     TEXT NOT NULL DEFAULT '{}',
  file       TEXT,
  claimed_by TEXT,
  runs       TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (world_id, list, key),
  FOREIGN KEY (world_id, list) REFERENCES world_lists (world_id, name) ON DELETE CASCADE
);
CREATE INDEX list_entries_status ON list_entries (world_id, list, status);
