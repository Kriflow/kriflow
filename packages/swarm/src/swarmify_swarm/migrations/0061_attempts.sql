-- Attempts (issue 535): each execution of a swarm stage, numbered per stage. A send-back, Rerun
-- or Follow-up starts the next one; `cause` is what started it (kind, note, by, at) as JSON.
-- `spent_before` is the stage's agents' cost when it started, so each Attempt's spend is the
-- difference to the next one's.
CREATE TABLE attempts (
  stage_id     TEXT NOT NULL,
  number       INTEGER NOT NULL,
  world_id     TEXT NOT NULL,
  run_id       TEXT NOT NULL,
  visit        INTEGER NOT NULL,
  cause        TEXT,
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  spent_before REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (stage_id, number)
);
