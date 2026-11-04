"""GoogleSQL DDL for Spanner (the emulator accepts it) mirroring sql/001_codegraph.sql and
sql/002_ledger.sql, plus ONE property graph, ``SdlcGraph``:

* node tables: ``node`` (label ``Node``: kind, slug, state, tier, ...) and ``sym`` (label
  ``Symbol``: sym_id, kind, name, qualname, path, snapshot_id, ...; property names are
  graph-global and keep one type, so sym.id is exposed as ``sym_id``);
* edge tables: ``edge`` (label ``Rel``, SOURCE KEY (src) REFERENCES node (id), DESTINATION KEY
  (dst) REFERENCES node (id)) and ``sym_edge`` (label ``SymRel``, same shape over ``sym``).
  ``rel`` stays a property, so GQL filters ``[:Rel {rel: 'depends_on'}]``.

Type mapping: uuid -> STRING(36), bigserial -> INT64 (ids are supplied by the loader; no
sequences), text -> STRING(MAX), text[] -> ARRAY<STRING(MAX)>, jsonb -> JSON, timestamptz ->
TIMESTAMP, numeric -> NUMERIC, date -> DATE. Dropped: the ``embedding vector(1024)`` column and
the generated ``tsv`` column (full-text and vector search are separate Spanner features), the
GIN/trigram indexes and partial indexes. ``node.kind`` and ``edge.rel`` keep their enum CHECKs, generated from
``shapes.NODE_KINDS`` and ``shapes.NODE_RELS`` (never typed here), so Spanner knows exactly the kinds and
relations the registry lists; ``render(checks=False)`` leaves them out for an emulator that rejects CHECK.
Interleaving is not used.
"""

from __future__ import annotations

from pathlib import Path

from sdlc_graph.shapes import NODE_KINDS, NODE_RELS

#: Tables exported, in dependency order.
TABLES: tuple[str, ...] = ("snapshot", "sym", "sym_edge", "node", "edge", "evidence", "transition", "decision", "run")

GRAPH_NAME = "SdlcGraph"
QUERIES_FILE = Path(__file__).with_name("queries.gql")

#: The headline GQL query used by the smoke test.
SAMPLE_GQL = (
    "GRAPH SdlcGraph "
    "MATCH (c:Node {kind: 'contract'})-[:Rel {rel: 'depends_on'}]->(d:Node) "
    "RETURN c.slug AS contract, d.slug AS depends_on ORDER BY contract, depends_on"
)

_TABLE_DDL: dict[str, str] = {
    "snapshot": """CREATE TABLE snapshot (
  id INT64 NOT NULL,
  repo STRING(MAX) NOT NULL,
  commit_sha STRING(MAX) NOT NULL,
  parent_sha STRING(MAX),
  indexed_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
  extractor_version STRING(MAX) NOT NULL,
  storage STRING(MAX) NOT NULL,
  base_snapshot_id INT64,
) PRIMARY KEY (id)""",
    "sym": """CREATE TABLE sym (
  id INT64 NOT NULL,
  snapshot_id INT64 NOT NULL,
  kind STRING(MAX) NOT NULL,
  name STRING(MAX) NOT NULL,
  qualname STRING(MAX) NOT NULL,
  path STRING(MAX) NOT NULL,
  line_start INT64,
  line_end INT64,
  attrs JSON,
  CONSTRAINT fk_sym_snapshot FOREIGN KEY (snapshot_id) REFERENCES snapshot (id),
) PRIMARY KEY (id)""",
    "sym_edge": """CREATE TABLE sym_edge (
  snapshot_id INT64 NOT NULL,
  src INT64 NOT NULL,
  dst INT64 NOT NULL,
  rel STRING(MAX) NOT NULL,
  attrs JSON,
  CONSTRAINT fk_sym_edge_src FOREIGN KEY (src) REFERENCES sym (id),
  CONSTRAINT fk_sym_edge_dst FOREIGN KEY (dst) REFERENCES sym (id),
) PRIMARY KEY (snapshot_id, src, dst, rel)""",
    "node": """CREATE TABLE node (
  id STRING(36) NOT NULL,
  kind STRING(MAX) NOT NULL,
  slug STRING(MAX) NOT NULL,
  title STRING(MAX) NOT NULL,
  repo STRING(MAX) NOT NULL,
  path STRING(MAX),
  commit_sha STRING(MAX),
  state STRING(MAX) NOT NULL,
  tier STRING(MAX),
  external_id STRING(MAX),
  created_by STRING(MAX) NOT NULL,
  created_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
  updated_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
  touched_symbols ARRAY<STRING(MAX)>,
  capabilities JSON,
  acceptance JSON,
  attrs JSON,
  {node_check}
) PRIMARY KEY (id)""",
    "edge": """CREATE TABLE edge (
  src STRING(36) NOT NULL,
  dst STRING(36) NOT NULL,
  rel STRING(MAX) NOT NULL,
  CONSTRAINT fk_edge_src FOREIGN KEY (src) REFERENCES node (id),
  CONSTRAINT fk_edge_dst FOREIGN KEY (dst) REFERENCES node (id),
  {edge_check}
) PRIMARY KEY (src, dst, rel)""",
    "evidence": """CREATE TABLE evidence (
  id INT64 NOT NULL,
  node_id STRING(36) NOT NULL,
  kind STRING(MAX) NOT NULL,
  source STRING(MAX) NOT NULL,
  signed_by STRING(MAX),
  payload JSON NOT NULL,
  `at` TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
  CONSTRAINT fk_evidence_node FOREIGN KEY (node_id) REFERENCES node (id),
) PRIMARY KEY (id)""",
    "transition": """CREATE TABLE transition (
  id INT64 NOT NULL,
  node_id STRING(36) NOT NULL,
  from_state STRING(MAX),
  to_state STRING(MAX) NOT NULL,
  actor STRING(MAX) NOT NULL,
  evidence_ids ARRAY<INT64>,
  reason STRING(MAX),
  `at` TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
  CONSTRAINT fk_transition_node FOREIGN KEY (node_id) REFERENCES node (id),
) PRIMARY KEY (id)""",
    "decision": """CREATE TABLE decision (
  id INT64 NOT NULL,
  node_id STRING(36) NOT NULL,
  question STRING(MAX) NOT NULL,
  options JSON,
  recommendation STRING(MAX),
  proposed_by STRING(MAX),
  decided_by STRING(MAX),
  choice STRING(MAX),
  due DATE,
  created_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
  resolved_at TIMESTAMP,
  severity STRING(MAX),
  meta JSON,
  CONSTRAINT fk_decision_node FOREIGN KEY (node_id) REFERENCES node (id),
) PRIMARY KEY (id)""",
    "run": """CREATE TABLE run (
  id INT64 NOT NULL,
  node_id STRING(36),
  stage STRING(MAX),
  requested_model STRING(MAX) NOT NULL,
  resolved_model STRING(MAX),
  model_tier STRING(MAX) NOT NULL,
  tokens_in INT64,
  tokens_out INT64,
  cost_usd NUMERIC,
  turns INT64,
  started_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
  finished_at TIMESTAMP,
  outcome STRING(MAX),
  tool_use_id STRING(MAX),
  agent_type STRING(MAX),
  meta JSON,
  CONSTRAINT fk_run_node FOREIGN KEY (node_id) REFERENCES node (id),
) PRIMARY KEY (id)""",
}

_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX sym_snapshot_kind_idx ON sym (snapshot_id, kind)",
    "CREATE INDEX sym_snapshot_qualname_idx ON sym (snapshot_id, qualname)",
    "CREATE INDEX sym_edge_dst_idx ON sym_edge (snapshot_id, dst, rel)",
    "CREATE UNIQUE INDEX node_slug_idx ON node (slug)",
    "CREATE INDEX node_kind_state_idx ON node (kind, state)",
    "CREATE INDEX edge_dst_idx ON edge (dst, rel)",
    "CREATE INDEX evidence_node_kind_idx ON evidence (node_id, kind)",
)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v + "'" for v in values)


def _checks(checks: bool) -> dict[str, str]:
    if not checks:
        return {"node_check": "", "edge_check": ""}
    return {"node_check": f"CONSTRAINT node_kind_check CHECK (kind IN ({_in_list(NODE_KINDS)})),",
            "edge_check": f"CONSTRAINT edge_rel_check CHECK (rel IN ({_in_list(NODE_RELS)})),"}


def table_ddl(checks: bool = True) -> list[str]:
    """``CREATE TABLE`` and ``CREATE INDEX`` statements for ``TABLES``, in order, each a single
    statement without a trailing semicolon (the admin API takes a list). ``checks=False`` omits the
    ``node.kind`` and ``edge.rel`` CHECK constraints."""
    fill = _checks(checks)
    out = []
    for t in TABLES:
        text = _TABLE_DDL[t].replace("{node_check}", fill["node_check"]).replace("{edge_check}", fill["edge_check"])
        out.append("\n".join(line for line in text.split("\n") if line.strip()))
    return out + list(_INDEX_DDL)


def property_graph_ddl() -> list[str]:
    """The ``CREATE PROPERTY GRAPH SdlcGraph`` statement (a one-element list)."""
    return [
        """CREATE PROPERTY GRAPH SdlcGraph
  NODE TABLES (
    node
      KEY (id)
      LABEL Node
      PROPERTIES (id, kind, slug, title, repo, state, tier, created_by, touched_symbols, attrs),
    sym
      KEY (id)
      LABEL Symbol
      PROPERTIES (id AS sym_id, snapshot_id, kind, name, qualname, path, line_start, line_end, attrs)
  )
  EDGE TABLES (
    edge
      KEY (src, dst, rel)
      SOURCE KEY (src) REFERENCES node (id)
      DESTINATION KEY (dst) REFERENCES node (id)
      LABEL Rel
      PROPERTIES (rel),
    sym_edge
      KEY (snapshot_id, src, dst, rel)
      SOURCE KEY (src) REFERENCES sym (id)
      DESTINATION KEY (dst) REFERENCES sym (id)
      LABEL SymRel
      PROPERTIES (snapshot_id, rel)
  )"""
    ]


def ddl_statements(checks: bool = True) -> list[str]:
    """``table_ddl(checks) + property_graph_ddl()``."""
    return table_ddl(checks) + property_graph_ddl()


def render(checks: bool = True) -> str:
    """All statements joined with ``;\\n\\n`` and a trailing ``;`` (what ``sdlc-graph spanner ddl`` prints)."""
    return ";\n\n".join(ddl_statements(checks)) + ";\n"


def sample_queries() -> str:
    """Contents of ``queries.gql``."""
    return QUERIES_FILE.read_text()
