-- Messages to a running stage (issue 534): a person's Message lands in each target agent's log
-- as a `message` line, `queued` first, then `delivered` once the agent's session got it. The
-- queued lines no delivered line answers are the agent's inbox, so it survives a restart. SQLite
-- cannot widen a CHECK in place, so agent_log is rebuilt.
CREATE TABLE agent_log_next (
  seq      INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL REFERENCES agents (id) ON DELETE CASCADE,
  stage_id TEXT NOT NULL,
  agent    TEXT NOT NULL,
  kind     TEXT NOT NULL
           CHECK (kind IN ('session', 'tool', 'denied', 'result', 'error', 'text', 'output',
                           'message')),
  tool     TEXT,
  input    TEXT,
  detail   TEXT NOT NULL DEFAULT '',
  ts       TEXT NOT NULL,
  ref      TEXT
);
INSERT INTO agent_log_next (seq, agent_id, stage_id, agent, kind, tool, input, detail, ts, ref)
  SELECT seq, agent_id, stage_id, agent, kind, tool, input, detail, ts, ref FROM agent_log;
DROP TABLE agent_log;
ALTER TABLE agent_log_next RENAME TO agent_log;
CREATE INDEX agent_log_stage ON agent_log (stage_id, seq);
