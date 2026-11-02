# Spanner Graph export: decisions and emulator findings

## What the emulator did (verified, `gcr.io/cloud-spanner-emulator/emulator:latest` on localhost:9110)
- Created instance (`emulator-config`) and database with 17 DDL statements: 9 tables, 7 indexes,
  1 `CREATE PROPERTY GRAPH SdlcGraph` (nodes `node`/Node and `sym`/Symbol, edges `edge`/Rel and
  `sym_edge`/SymRel). Foreign keys, JSON, ARRAY<STRING>, NUMERIC and commit-timestamp columns work.
- `GRAPH SdlcGraph MATCH ... RETURN` through `snapshot.execute_sql` works: edge-property filter
  (`[:Rel {rel:'depends_on'}]`), quantified paths (`->{1,3}`) with `DISTINCT`, and
  `MATCH ANY SHORTEST` all returned the expected rows on a small dataset.

## What it did not support / what we changed
- Property names are global in a property graph and must keep one type: `node.id` is STRING and
  `sym.id` INT64, so the emulator rejected the first draft ("property declaration of 'id' has type
  conflicts"). `sym.id` is exposed as `sym_id` (`PROPERTIES (id AS sym_id, ...)`).
- Dropped from the export (Postgres only): `sym.embedding vector(1024)`, generated `tsv`, GIN and
  trigram indexes, partial indexes, `kind`/`rel` CHECK enums. Full-text and vector search are
  separate Spanner features and were not attempted.
- `at` (evidence, transition) is a GoogleSQL reserved word and is backtick-quoted.
- Graph algorithms: per the emulator README they return empty results. NOT tested by us; do not
  demo anything beyond MATCH pattern queries on the emulator. Real Spanner (Enterprise edition) is needed for them and was not tried.
- Docs: the URL first tried (`.../graph/create-manage-graph-schema`) returned 404; the syntax was
  taken from `.../graph/create-update-drop-schema`, which has no PROPERTIES example, so the
  PROPERTIES / KEY / `AS` forms were validated empirically against the emulator.

## Decisions
- One graph (`SdlcGraph`) over ledger and code tables, as planned, not the scaffold's two graphs. Edge labels differ (`Rel`, `SymRel`) so GQL can target either.
- IDs are supplied by the loader (no sequences/GENERATE_UUID): Postgres is the system of record, Spanner is a one-way export.
- Ports: the emulator defaults are 9010/9020, often taken by another emulator. Default here is 9110 (compose
  `spanner` profile). `smoke` uses `SPANNER_EMULATOR_HOST` if set; else the compose port if
  reachable; else it starts a throwaway `sdlc-graph-spanner-smoke` container on 9110/9120 and removes it.
- `smoke` drops its database at the end but leaves the instance. It loads `node` and `edge` only
  (3 sample nodes and 2 depends_on edges, or the Postgres ledger with `--from-pg`, which now matches
  every ledger edge; integrator, INT4).
- Integrator: `spanner export` loads every ledger table plus one snapshot's `sym`/`sym_edge` and
  keeps the database (`sdlc_export`) for `spanner query`. Verified on the emulator: quantified paths
  over `SymRel` (`->{1,4}`), a `WHERE ... IN UNNEST(c.touched_symbols)` join between a Node array
  property and Symbol properties, and two MATCH clauses in one statement. The emulator returned
  `RETURN DISTINCT ... ORDER BY` rows unsorted, so named queries are sorted client-side.
