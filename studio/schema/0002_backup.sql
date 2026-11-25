-- Where verified copies go, what each copy contained, and whether a copy has ever been restored.
CREATE TABLE backup_target(
  id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, label TEXT NOT NULL, enabled INT NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),
  added_by TEXT NOT NULL, added_at TEXT NOT NULL);
CREATE TABLE backup_run(
  id TEXT PRIMARY KEY, target_id TEXT NOT NULL REFERENCES backup_target(id), set_name TEXT, state TEXT NOT NULL CHECK(state IN('verified','failed','unavailable')),
  files INT, bytes INT, event_seq INT, seconds REAL, error TEXT, made_at TEXT NOT NULL, CHECK(state<>'verified' OR set_name IS NOT NULL));
CREATE TABLE restore_drill(
  id TEXT PRIMARY KEY, target_id TEXT NOT NULL REFERENCES backup_target(id), set_name TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN('equal','different','failed')), detail_json TEXT NOT NULL, at TEXT NOT NULL);
CREATE TRIGGER backup_run_no_update BEFORE UPDATE ON backup_run BEGIN SELECT RAISE(ABORT,'backup history is append-only'); END;
CREATE TRIGGER backup_run_no_delete BEFORE DELETE ON backup_run BEGIN SELECT RAISE(ABORT,'backup history is append-only'); END;
CREATE TRIGGER restore_drill_no_update BEFORE UPDATE ON restore_drill BEGIN SELECT RAISE(ABORT,'restore history is append-only'); END;
CREATE TRIGGER restore_drill_no_delete BEFORE DELETE ON restore_drill BEGIN SELECT RAISE(ABORT,'restore history is append-only'); END;
