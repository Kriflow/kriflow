-- The stage log keeps a transcript (issue 533): what an agent said (`text`) and each allowed tool
-- call's result (`output`), paired to its call by the tool use id in `ref`. SQLite cannot widen
-- a CHECK in place, so agent_log is rebuilt.
CREATE TABLE agent_log_next (
  seq      INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL REFERENCES agents (id) ON DELETE CASCADE,
  stage_id TEXT NOT NULL,
  agent    TEXT NOT NULL,
  kind     TEXT NOT NULL
           CHECK (kind IN ('session', 'tool', 'denied', 'result', 'error', 'text', 'output')),
  tool     TEXT,
  input    TEXT,
  detail   TEXT NOT NULL DEFAULT '',
  ts       TEXT NOT NULL,
  ref      TEXT
);
INSERT INTO agent_log_next (seq, agent_id, stage_id, agent, kind, tool, input, detail, ts)
  SELECT seq, agent_id, stage_id, agent, kind, tool, input, detail, ts FROM agent_log;
DROP TABLE agent_log;
ALTER TABLE agent_log_next RENAME TO agent_log;
CREATE INDEX agent_log_stage ON agent_log (stage_id, seq);
