-- 011_foldkit_edges.sql: the rest of the Foldkit symbol set of DESIGN.md section 11.4: the symbol kind submodel and the
-- edge rels emits (command -> message), renders (view -> submodel) and routes_to (route_fe -> view). Widens the two CHECK
-- constraints 008_typescript.sql left. Idempotent.

ALTER TABLE sym DROP CONSTRAINT IF EXISTS sym_kind_check;
ALTER TABLE sym ADD CONSTRAINT sym_kind_check CHECK (kind IN ('module','class','function','method','route','model',
  'env_var','package','frontend_call','test','message','command','view','route_fe','submodel'));

ALTER TABLE sym_edge DROP CONSTRAINT IF EXISTS sym_edge_rel_check;
ALTER TABLE sym_edge ADD CONSTRAINT sym_edge_rel_check CHECK (rel IN ('imports','calls','defines','exposes_route',
  'includes_router','reads_env','uses_model','depends_on_package','fetches','tests','handles','calls_api',
  'emits','renders','routes_to'));
