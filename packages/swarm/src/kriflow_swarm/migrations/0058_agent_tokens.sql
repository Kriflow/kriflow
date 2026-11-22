-- Each agent's token counts, summed over its sessions' model steps (issue 536). 0 for agents
-- from before.
ALTER TABLE agents ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN cache_read_input_tokens INTEGER NOT NULL DEFAULT 0;
-- The total its current session last reported at a result: resuming the session reports that much
-- again, so only the rest is new spend. NULL when unknown (then the whole total counts again).
ALTER TABLE agents ADD COLUMN session_total_usd REAL;
