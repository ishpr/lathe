-- 001_codegraph.sql: the DERIVED code graph. Extractors rebuild it from source per commit SHA;
-- no API or agent writes it. Idempotent: safe to re-run. Tables are created unqualified in the
-- first schema of search_path, which sdlc_graph/db.py sets from SDLC_SCHEMA (default public).
-- Extensions live in public so every schema (including per-test schemas) shares them.

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

-- One row per indexed commit. storage='full': its symbols/edges are rows in sym/sym_edge.
-- storage='delta': its content is base_snapshot_id's content plus the sym_delta rows.
CREATE TABLE IF NOT EXISTS snapshot (
  id                bigserial PRIMARY KEY,
  repo              text NOT NULL,                 -- repo name, see snapshot.repo_name()
  commit_sha        text NOT NULL,                 -- full 40-char SHA
  parent_sha        text,                          -- first parent of commit_sha, if any
  indexed_at        timestamptz NOT NULL DEFAULT now(),
  extractor_version text NOT NULL,
  storage           text NOT NULL DEFAULT 'full' CHECK (storage IN ('full','delta')),
  base_snapshot_id  bigint REFERENCES snapshot(id) ON DELETE CASCADE,
  CHECK ((storage = 'full' AND base_snapshot_id IS NULL) OR (storage = 'delta' AND base_snapshot_id IS NOT NULL)),
  UNIQUE (repo, commit_sha)
);

CREATE TABLE IF NOT EXISTS sym (
  id           bigserial PRIMARY KEY,
  snapshot_id  bigint NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN ('module','class','function','method','route','model',
                                             'env_var','package','frontend_call','test',
                                             'message','command','view','route_fe')),
  name         text NOT NULL,
  qualname     text NOT NULL,
  path         text NOT NULL,                      -- repo-relative POSIX path
  line_start   int,
  line_end     int,
  attrs        jsonb NOT NULL DEFAULT '{}'::jsonb, -- per-kind keys, see ARCHITECTURE.md; attrs.confidence='low' for guesses
  embedding    public.vector(1024),                -- unused until embeddings land
  tsv          tsvector GENERATED ALWAYS AS (
                 to_tsvector('simple', coalesce(name,'') || ' ' || replace(coalesce(qualname,''), '.', ' ')
                                       || ' ' || replace(replace(coalesce(path,''), '/', ' '), '.', ' '))) STORED,
  UNIQUE (snapshot_id, kind, qualname, path)       -- identity across snapshots: (kind, qualname, path)
);
CREATE INDEX IF NOT EXISTS sym_snapshot_kind_idx ON sym (snapshot_id, kind);
CREATE INDEX IF NOT EXISTS sym_snapshot_path_idx ON sym (snapshot_id, path);
CREATE INDEX IF NOT EXISTS sym_snapshot_qualname_idx ON sym (snapshot_id, qualname);
CREATE INDEX IF NOT EXISTS sym_tsv_idx ON sym USING gin (tsv);
CREATE INDEX IF NOT EXISTS sym_qualname_trgm_idx ON sym USING gin (qualname public.gin_trgm_ops);

CREATE TABLE IF NOT EXISTS sym_edge (
  snapshot_id  bigint NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
  src          bigint NOT NULL REFERENCES sym(id) ON DELETE CASCADE,
  dst          bigint NOT NULL REFERENCES sym(id) ON DELETE CASCADE,
  rel          text NOT NULL CHECK (rel IN ('imports','calls','defines','exposes_route','includes_router',
                                            'reads_env','uses_model','depends_on_package','fetches','tests',
                                            'handles','calls_api')),
  attrs        jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (snapshot_id, src, dst, rel)
);
CREATE INDEX IF NOT EXISTS sym_edge_dst_idx ON sym_edge (snapshot_id, dst, rel);
CREATE INDEX IF NOT EXISTS sym_edge_src_idx ON sym_edge (snapshot_id, src, rel);

-- CODEOWNERS rules. ord is the line order: the LAST matching rule wins (GitHub semantics).
CREATE TABLE IF NOT EXISTS owner (
  repo       text NOT NULL,
  path_glob  text NOT NULL,
  handle     text NOT NULL,
  ord        int  NOT NULL DEFAULT 0,
  PRIMARY KEY (repo, path_glob, handle)
);

-- Changes of a delta snapshot relative to snapshot.base_snapshot_id.
-- row shapes (identity keys are kind+qualname+path, never sym ids):
--   table_name='sym'      op in (add, remove, change): {"kind","name","qualname","path","line_start","line_end","attrs"}
--   table_name='sym_edge' op in (add, remove):         {"src":{"kind","qualname","path"},"dst":{...},"rel","attrs"}
CREATE TABLE IF NOT EXISTS sym_delta (
  id          bigserial PRIMARY KEY,
  snapshot_id bigint NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
  op          text NOT NULL CHECK (op IN ('add','remove','change')),
  table_name  text NOT NULL CHECK (table_name IN ('sym','sym_edge')),
  row         jsonb NOT NULL,
  CHECK (NOT (table_name = 'sym_edge' AND op = 'change'))
);
CREATE INDEX IF NOT EXISTS sym_delta_snapshot_idx ON sym_delta (snapshot_id, table_name);
