-- 010_repo_node.sql: a node of kind 'repo', one per repository, that carries repository-level evidence
-- (graph_provisioned: /factory:graph provision built or found the database). Idempotent: the kind check is
-- dropped and re-added with the full list, so a re-run changes nothing.

ALTER TABLE node DROP CONSTRAINT IF EXISTS node_kind_check;
ALTER TABLE node ADD CONSTRAINT node_kind_check CHECK (kind IN ('epic','feature','story','contract','change','release',
                                                                'incident','decision','rule','repo'));
