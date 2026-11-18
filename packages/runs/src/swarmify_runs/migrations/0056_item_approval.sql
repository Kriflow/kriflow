-- A fan-out stage with `approve` (issue 502): a person approves each Item. Any new status of the
-- Item (a rerun, a new output) clears it.
ALTER TABLE fanout_items ADD COLUMN approved INTEGER NOT NULL DEFAULT 0;
