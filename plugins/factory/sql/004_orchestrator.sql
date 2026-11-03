-- 004_orchestrator.sql: the orchestrator's state on the ledger (docs/DESIGN.md section 12).
-- The daemon's only state is the ledger plus one lock row per feature, so a restart loses nothing.
-- Idempotent like the other files: every statement re-runs safely.
-- Sorts before 004_owner_per_snapshot.sql; it depends only on 002_ledger.sql.

-- One daemon per feature. A row older than 3 of its holder's ticks is stale and may be taken over.
CREATE TABLE IF NOT EXISTS orchestrator_lock (
  feature_id    uuid PRIMARY KEY REFERENCES node(id) ON DELETE CASCADE,
  owner         text NOT NULL,                       -- host:pid:nonce of the daemon holding it
  heartbeat_at  timestamptz NOT NULL DEFAULT now(),
  acquired_at   timestamptz NOT NULL DEFAULT now(),
  tick_seconds  numeric NOT NULL DEFAULT 60 CHECK (tick_seconds > 0),
  meta          jsonb NOT NULL DEFAULT '{}'::jsonb     -- repo path, mode, max runs
);

-- A run row per orchestrated step: the session, its process, where it started and what it left behind.
ALTER TABLE run ADD COLUMN IF NOT EXISTS session_id text;              -- claude --session-id
ALTER TABLE run ADD COLUMN IF NOT EXISTS pid int;                      -- executor process (its own process group)
ALTER TABLE run ADD COLUMN IF NOT EXISTS start_commit text;            -- worktree HEAD at dispatch; drift resets here
ALTER TABLE run ADD COLUMN IF NOT EXISTS log_path text;                -- stream-json log; <log_path>.result.json next to it
ALTER TABLE run ADD COLUMN IF NOT EXISTS summary text;                 -- what the gates found, injected into the next step
ALTER TABLE run ADD COLUMN IF NOT EXISTS last_assistant_message text;  -- from the executor's result file
ALTER TABLE run ADD COLUMN IF NOT EXISTS step text;                    -- tests | implement | verify | ship | conformance | judge
ALTER TABLE run ADD COLUMN IF NOT EXISTS attempt int;                  -- 1-based within (node, step, since the state was entered)
CREATE INDEX IF NOT EXISTS run_open_step_idx ON run (node_id, step) WHERE finished_at IS NULL;

-- Escalations are decisions with a severity (Gas Town's P0-P2) and a dedupe key in meta.
ALTER TABLE decision ADD COLUMN IF NOT EXISTS severity text;
ALTER TABLE decision ADD COLUMN IF NOT EXISTS meta jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE decision DROP CONSTRAINT IF EXISTS decision_severity_check;
ALTER TABLE decision ADD CONSTRAINT decision_severity_check CHECK (severity IS NULL OR severity IN ('P0','P1','P2'));
CREATE INDEX IF NOT EXISTS decision_severity_open_idx ON decision (severity) WHERE resolved_at IS NULL;

-- Evidence kind 'conformance' ({score 0-1, findings[]}) is validated in Python
-- (sdlc_graph.orchestrator registers it in ledger.evidence.KINDS); evidence.kind has no SQL check.
