-- Issue 541: the status a list's Entry moves to when the run holding it publishes (none: it
-- stays where it is), and the index that finds the Entries a run holds.
ALTER TABLE world_lists ADD COLUMN published TEXT;
CREATE INDEX list_entries_claimed ON list_entries (claimed_by) WHERE claimed_by IS NOT NULL;
