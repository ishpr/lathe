-- 005_index.sql: the retrieval index (docs/DESIGN.md section 2.2). DERIVED like the code
-- graph: rebuilt from ledger nodes and the latest snapshot's symbols by `sdlc-graph index build`,
-- never written by hand. One row per (ref, section): a ledger node section ('outcome',
-- 'acceptance', 'decisions', ...) or one symbol summary ('summary'). Rows exist only when an
-- embedding provider produced a vector for them; with no provider nothing is written here.
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS chunk (
  id           bigserial PRIMARY KEY,
  source       text NOT NULL CHECK (source IN ('node','sym')),
  ref          text NOT NULL,        -- 'node:<slug>' | 'sym:<repo>:<kind>:<qualname>@<path>' (stable across snapshots)
  repo         text NOT NULL,
  kind         text NOT NULL,        -- node kind or symbol kind
  slug         text,                 -- nodes
  qualname     text,                 -- symbols
  path         text,
  node_id      uuid   REFERENCES node(id)     ON DELETE CASCADE,
  snapshot_id  bigint REFERENCES snapshot(id) ON DELETE CASCADE,  -- symbols: the snapshot they were last built from
  sym_id       bigint REFERENCES sym(id)      ON DELETE SET NULL,
  section      text NOT NULL,
  text         text NOT NULL,
  text_hash    text NOT NULL,        -- sha256 of text: an unchanged chunk is never re-embedded
  -- the second term splits paths, snake_case and dotted names into words so 'login' finds 'app/auth/login.py'
  tsv          tsvector GENERATED ALWAYS AS (
                 to_tsvector('english', text || ' ' || regexp_replace(text, '[^[:alnum:]]+', ' ', 'g'))) STORED,
  embedding    public.vector(1024) NOT NULL,
  embed_model  text NOT NULL,        -- e.g. 'openai:text-embedding-3-small:1024'; a query only compares like with like
  meta         jsonb NOT NULL DEFAULT '{}'::jsonb,
  at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (ref, section)
);
CREATE INDEX IF NOT EXISTS chunk_embedding_hnsw ON chunk USING hnsw (embedding public.vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunk_tsv_idx ON chunk USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunk_source_repo_idx ON chunk (source, repo, kind);
CREATE INDEX IF NOT EXISTS chunk_snapshot_idx ON chunk (snapshot_id) WHERE snapshot_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS chunk_node_idx ON chunk (node_id) WHERE node_id IS NOT NULL;
