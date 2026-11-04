-- 013: who made the write. The MCP server records the calling actor on the runs and the advisory evidence it
-- writes, and refuses to finish a run another actor opened. NULL for rows written before this migration and for
-- rows the hooks and CI write.
ALTER TABLE run      ADD COLUMN IF NOT EXISTS actor text;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS actor text;
