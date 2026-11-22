-- Archived skills (archive and delete): a world's skill by name, own or shared, that no stage
-- loads until it is restored. Its versions stay in skill_versions.
CREATE TABLE archived_skills (
  world_id    TEXT NOT NULL,
  name        TEXT NOT NULL,
  archived_at TEXT NOT NULL,
  PRIMARY KEY (world_id, name)
);
