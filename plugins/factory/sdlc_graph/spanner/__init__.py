"""Spanner Graph export: the same node/edge (and sym/sym_edge) tables in GoogleSQL with a
``CREATE PROPERTY GRAPH`` over them, smoke-tested against the local Cloud Spanner emulator
(docker compose --profile spanner up -d spanner; SPANNER_EMULATOR_HOST=localhost:9110).
Portability runs one way: Postgres tables -> Spanner Graph, no data-model change.
"""
