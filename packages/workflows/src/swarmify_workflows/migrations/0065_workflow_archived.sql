-- When the workflow was archived: hidden from lists, its schedule off and new runs refused until
-- it is restored. NULL while it is live.
ALTER TABLE workflows ADD COLUMN archived_at TEXT;
