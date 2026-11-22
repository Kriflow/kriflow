-- When the run (a finished one, with its child runs) was archived: hidden from the runs list,
-- still served by id. NULL while it is shown.
ALTER TABLE runs ADD COLUMN archived_at TEXT;
