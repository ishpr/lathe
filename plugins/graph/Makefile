# sdlc-graph developer targets. Postgres: localhost:5435 (override with SDLC_PG_PORT).
PY := .venv/bin/python
export DATABASE_URL ?= postgresql://sdlc:sdlc@localhost:$(or $(SDLC_PG_PORT),5435)/sdlc
export SPANNER_EMULATOR_HOST ?= localhost:$(or $(SDLC_SPANNER_GRPC_PORT),9110)

.PHONY: venv db-up db-down db-init db-reset spanner-up spanner-down test demo mcp

venv:
	uv venv --python 3.11 .venv && uv sync

db-up:
	docker compose up -d --wait db

db-down:
	docker compose stop db

db-init: db-up
	uv run sdlc-graph db init

db-reset: db-up
	uv run sdlc-graph db reset

spanner-up:
	docker compose --profile spanner up -d spanner

spanner-down:
	docker compose --profile spanner stop spanner

test: db-up
	$(PY) -m pytest -q

demo: db-up
	bash demo/run_demo.sh

mcp:
	uv run sdlc-graph mcp
