-- 006_executor.sql: which executor mode and which credential produced each run (design section 12.5).
-- Idempotent. NULL on runs that are not orchestrated steps (hook-recorded agent runs, older rows).

ALTER TABLE run ADD COLUMN IF NOT EXISTS executor    text;   -- local | runner
ALTER TABLE run ADD COLUMN IF NOT EXISTS auth_source text;   -- subscription | api_key | oauth_token | claude_platform_aws | bedrock

ALTER TABLE run DROP CONSTRAINT IF EXISTS run_executor_check;
ALTER TABLE run ADD CONSTRAINT run_executor_check CHECK (executor IS NULL OR executor IN ('local', 'runner'));
ALTER TABLE run DROP CONSTRAINT IF EXISTS run_auth_source_check;
ALTER TABLE run ADD CONSTRAINT run_auth_source_check
  CHECK (auth_source IS NULL OR auth_source IN ('subscription', 'api_key', 'oauth_token', 'claude_platform_aws', 'bedrock'));
