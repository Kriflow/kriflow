-- World Logins (spec: World Logins And Browser Connector): what anyone may see of a login a world
-- keeps for a website. Its password, authenticator secret and Saved Session live in the Secrets
-- Store, never here. `domains` is a JSON list.
CREATE TABLE world_logins (
  world_id   TEXT NOT NULL,
  name       TEXT NOT NULL,
  domains    TEXT NOT NULL,
  username   TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  rotated_at TEXT NOT NULL,
  PRIMARY KEY (world_id, name)
);
