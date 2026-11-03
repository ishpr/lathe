-- 004_owner_per_snapshot.sql: CODEOWNERS is versioned per snapshot, like the rest of the derived
-- graph. Before this, `owner` was one repo-wide table rewritten on every index/delta call, so owners()
-- answered with whichever commit was indexed last. snapshot.committed_at lets "latest" mean the
-- newest COMMIT, not the most recently indexed one.

ALTER TABLE snapshot ADD COLUMN IF NOT EXISTS committed_at timestamptz;
-- number of commits reachable from the snapshot's commit: breaks ties between commits made in the same second
ALTER TABLE snapshot ADD COLUMN IF NOT EXISTS ancestors int;

ALTER TABLE owner ADD COLUMN IF NOT EXISTS snapshot_id bigint REFERENCES snapshot(id) ON DELETE CASCADE;
-- rows written before this migration cannot be attributed to a commit
DELETE FROM owner WHERE snapshot_id IS NULL;
ALTER TABLE owner DROP CONSTRAINT IF EXISTS owner_pkey;
ALTER TABLE owner ALTER COLUMN snapshot_id SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS owner_snapshot_glob_handle ON owner (snapshot_id, path_glob, handle);
CREATE INDEX IF NOT EXISTS owner_repo ON owner (repo);
