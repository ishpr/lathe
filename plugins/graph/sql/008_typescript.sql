-- 008_typescript.sql: the TypeScript extractor's symbol kinds (message, command, view, route_fe) and edge
-- rels (handles, calls_api). 001_codegraph.sql carries the same lists for a new schema; this file widens the
-- two CHECK constraints of a schema created before. Idempotent.

ALTER TABLE sym DROP CONSTRAINT IF EXISTS sym_kind_check;
ALTER TABLE sym ADD CONSTRAINT sym_kind_check CHECK (kind IN ('module','class','function','method','route','model',
  'env_var','package','frontend_call','test','message','command','view','route_fe'));

ALTER TABLE sym_edge DROP CONSTRAINT IF EXISTS sym_edge_rel_check;
ALTER TABLE sym_edge ADD CONSTRAINT sym_edge_rel_check CHECK (rel IN ('imports','calls','defines','exposes_route',
  'includes_router','reads_env','uses_model','depends_on_package','fetches','tests','handles','calls_api'));
