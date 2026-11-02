-- 007_assumptions.sql: the defaults an unattended session applied instead of asking (design section 14).
-- Idempotent. Evidence kind 'assumptions' ({"assumptions": [{default_applied, where, why}, ...]}) is
-- validated in Python (sdlc_graph.orchestrator registers it in ledger.evidence.KINDS, like 'conformance');
-- evidence.kind has no SQL check. This view flattens every recorded assumption for review and reporting:
--   SELECT slug, default_applied, "where", why FROM assumption WHERE slug = 'rate-limit';
-- The newest row per node is the current set (the orchestrator records a row only when the set changes).

CREATE OR REPLACE VIEW assumption AS
SELECT e.id                       AS evidence_id,
       n.slug                     AS slug,
       e.at                       AS at,
       e.source                   AS source,
       e.payload->>'step'         AS step,
       a.ord                      AS item,
       a.value->>'default_applied' AS default_applied,
       a.value->>'where'          AS "where",
       a.value->>'why'            AS why,
       a.value->>'decision'       AS decision,
       e.id = (SELECT max(e2.id) FROM evidence e2 WHERE e2.node_id = e.node_id AND e2.kind = 'assumptions') AS current
  FROM evidence e
  JOIN node n ON n.id = e.node_id
  CROSS JOIN LATERAL jsonb_array_elements(
       CASE WHEN jsonb_typeof(e.payload->'assumptions') = 'array' THEN e.payload->'assumptions' ELSE '[]'::jsonb END
  ) WITH ORDINALITY AS a(value, ord)
 WHERE e.kind = 'assumptions';
