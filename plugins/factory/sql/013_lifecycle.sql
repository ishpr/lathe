-- 013_lifecycle.sql: the lifecycle of DESIGN section 16. Two new node kinds (finding, idea), the promoted_to relation,
-- an expression index for finding fingerprints and one for decision keys, and the one data change: story and decision
-- nodes leave the stateless 'active' state for their new machines. Idempotent: the CHECKs are dropped and re-added, and
-- the remap only touches rows still in 'active'.

ALTER TABLE node DROP CONSTRAINT IF EXISTS node_kind_check;
ALTER TABLE node ADD CONSTRAINT node_kind_check CHECK (kind IN ('epic','feature','story','contract','change','release',
  'incident','decision','rule','repo','finding','idea'));

ALTER TABLE edge DROP CONSTRAINT IF EXISTS edge_rel_check;
ALTER TABLE edge ADD CONSTRAINT edge_rel_check CHECK (rel IN ('decomposes_into','depends_on','verified_by','shipped_in',
  'supersedes','caused_by','governed_by','promoted_to'));

CREATE UNIQUE INDEX IF NOT EXISTS node_finding_fingerprint_idx ON node (repo, (attrs->>'fingerprint')) WHERE kind = 'finding';
CREATE INDEX IF NOT EXISTS decision_key_idx ON decision ((meta->>'key')) WHERE meta ? 'key';

-- Existing stories: no contract -> drafted; every contract merged -> built; otherwise building.
WITH moved AS (
  UPDATE node s SET state = CASE
      WHEN NOT EXISTS (SELECT 1 FROM edge e JOIN node c ON c.id = e.dst
                        WHERE e.src = s.id AND e.rel = 'decomposes_into' AND c.kind = 'contract') THEN 'drafted'
      WHEN NOT EXISTS (SELECT 1 FROM edge e JOIN node c ON c.id = e.dst
                        WHERE e.src = s.id AND e.rel = 'decomposes_into' AND c.kind = 'contract' AND c.state <> 'merged') THEN 'built'
      ELSE 'building' END,
    updated_at = now()
  WHERE s.kind = 'story' AND s.state = 'active'
  RETURNING s.id, s.state)
INSERT INTO transition (node_id, from_state, to_state, actor, reason)
SELECT id, 'active', state, 'ci:migrate-013', 'stateless -> machine (013)' FROM moved;

-- Existing decision records: proposed, never accepted, so nothing governs until a named owner accepts it.
WITH moved AS (
  UPDATE node SET state = 'proposed', updated_at = now()
  WHERE kind = 'decision' AND state = 'active'
  RETURNING id, state)
INSERT INTO transition (node_id, from_state, to_state, actor, reason)
SELECT id, 'active', state, 'ci:migrate-013', 'stateless -> machine (013)' FROM moved;
