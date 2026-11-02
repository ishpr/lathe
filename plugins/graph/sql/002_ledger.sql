-- 002_ledger.sql: the WRITTEN intent ledger (docs/DESIGN.md section 2.1).
-- Written only through sdlc_graph.ledger functions / MCP tools that evaluate guards.
-- Enums are CHECK constraints (not CREATE TYPE) so the file re-runs safely.
-- Additions over section 2.1: node.touched_symbols, node.capabilities, node.acceptance, node.attrs;
-- decision.proposed_by/created_at; run.tool_use_id/agent_type/meta. gen_random_uuid() is core in PG13+.

CREATE TABLE IF NOT EXISTS node (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind            text NOT NULL CHECK (kind IN ('epic','feature','story','contract','change','release',
                                                'incident','decision','rule')),
  slug            text NOT NULL UNIQUE,
  title           text NOT NULL,
  repo            text NOT NULL,
  path            text,
  commit_sha      text,
  state           text NOT NULL,
  tier            text CHECK (tier IN ('trivial','low','medium','high')),
  external_id     text,
  created_by      text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  touched_symbols text[] NOT NULL DEFAULT '{}',      -- symbol keys "kind:qualname@path" (ARCHITECTURE.md)
  capabilities    jsonb  NOT NULL DEFAULT '{}'::jsonb, -- {"paths":[],"egress":[],"data":[],"secrets":[]}
  acceptance      jsonb  NOT NULL DEFAULT '[]'::jsonb, -- [{"id":"AC1","text":"...","test":"tests/x.py::test_y"}]
  attrs           jsonb  NOT NULL DEFAULT '{}'::jsonb  -- free per-kind extras (epic: riskiest_assumption, guardrail_metric)
);
CREATE INDEX IF NOT EXISTS node_kind_state_idx ON node (kind, state);
CREATE INDEX IF NOT EXISTS node_repo_idx ON node (repo);
CREATE INDEX IF NOT EXISTS node_touched_idx ON node USING gin (touched_symbols);

CREATE TABLE IF NOT EXISTS edge (
  src  uuid NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  dst  uuid NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  rel  text NOT NULL CHECK (rel IN ('decomposes_into','depends_on','verified_by','shipped_in',
                                    'supersedes','caused_by','governed_by')),
  PRIMARY KEY (src, dst, rel)
);
CREATE INDEX IF NOT EXISTS edge_dst_idx ON edge (dst, rel);

CREATE TABLE IF NOT EXISTS evidence (
  id         bigserial PRIMARY KEY,
  node_id    uuid NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  kind       text NOT NULL,                 -- validated in sdlc_graph.ledger.evidence.KINDS, not here
  source     text NOT NULL,                 -- 'local' | 'ci:<run url>' | 'github:<review id>'
  signed_by  text,                          -- CI OIDC subject; null for local. Binding = source LIKE 'ci:%' AND signed_by <> ''
  payload    jsonb NOT NULL,
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS evidence_node_kind_idx ON evidence (node_id, kind, at DESC);

CREATE TABLE IF NOT EXISTS transition (
  id            bigserial PRIMARY KEY,
  node_id       uuid NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  from_state    text,
  to_state      text NOT NULL,
  actor         text NOT NULL,              -- '@alice' | 'agent:sonnet' | 'ci:github'
  evidence_ids  bigint[] NOT NULL DEFAULT '{}',
  reason        text,
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS transition_node_idx ON transition (node_id, at);

CREATE TABLE IF NOT EXISTS decision (
  id              bigserial PRIMARY KEY,
  node_id         uuid NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  question        text NOT NULL,
  options         jsonb NOT NULL DEFAULT '[]'::jsonb,
  recommendation  text,
  proposed_by     text,
  decided_by      text,
  choice          text,
  due             date,
  created_at      timestamptz NOT NULL DEFAULT now(),
  resolved_at     timestamptz,
  CHECK ((resolved_at IS NULL) = (choice IS NULL))
);
CREATE INDEX IF NOT EXISTS decision_open_idx ON decision (node_id) WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS run (
  id               bigserial PRIMARY KEY,
  node_id          uuid REFERENCES node(id) ON DELETE CASCADE,  -- null when a spawn names no node
  stage            text,
  requested_model  text NOT NULL,           -- what the spawn asked for (PreToolUse hook)
  resolved_model   text,                    -- what actually ran (PostToolUse resolvedModel)
  model_tier       text NOT NULL CHECK (model_tier IN ('planner','orchestrator','executor','utility','unknown')),
  tokens_in        bigint,
  tokens_out       bigint,
  cost_usd         numeric,
  turns            int,
  started_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz,
  outcome          text,                    -- 'ok' | 'error' | 'refused' | free text
  tool_use_id      text UNIQUE,             -- joins the PreToolUse and PostToolUse hook calls
  agent_type       text,                    -- Agent tool subagent_type
  meta             jsonb NOT NULL DEFAULT '{}'::jsonb  -- modelsUsed, totalDurationMs, rewrite notes
);
CREATE INDEX IF NOT EXISTS run_node_idx ON run (node_id, started_at);
