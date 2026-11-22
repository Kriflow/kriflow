-- Lists page (issue 540): the statuses whose Entries wait for a person, as a JSON list. While
-- any Entry is in one, Needs you lists a notice.
ALTER TABLE world_lists ADD COLUMN review TEXT NOT NULL DEFAULT '[]';
