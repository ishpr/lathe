# Graph providers: where the graph database lives

The code graph and the ledger live in one PostgreSQL database with pgvector. `/factory:init` asks two questions once
(headless: it applies the defaults and lists them under Assumptions) and writes the answers to `.claude/sdlc.json`:

```json
"graph": {"enabled": true, "provider": "local", "url_env": "DATABASE_URL",
          "spanner": {"project": "", "instance": "", "database": ""}}
```

1. **Code graph and ledger: on or off.** On is recommended: the orchestrator, impact analysis and the console run on
   it. Off keeps hooks, contracts and evidence files, and drops the ledger.
2. **Where the database lives:** `local`, `neon`, `aws` or `spanner` (the four sections below).

`/factory:graph` changes it later:

| Command | Effect |
|---|---|
| `status` | on or off, provider, where the URL comes from (redacted), reachable, schema version, snapshot and node counts; the MCP tool `graph_status` answers the same |
| `on` / `off` | flips `graph.enabled` |
| `url` | stores a connection string (from Secrets Manager, Secret Manager, a staged file, or a hidden prompt a human types into) in the local env file without printing it |
| `provision <local\|neon\|aws\|spanner>` | builds the database, runs `sdlc-graph db init`, then `status`, and records evidence |

Every command is `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" <command>`, one per Bash call, each covered by an
allow rule in the skill (`check_skill_permissions.py` proves it).

## On, off, and not reachable

- **Off** (`enabled: false`): every graph step is skipped with one line, `graph off: ...`; every graph MCP tool answers
  `{"error": "graph off"}` without opening a connection; `/factory:orchestrate` and `sdlc-graph orchestrate` refuse to
  start ("the orchestrator needs the ledger; run /factory:graph on", exit 5).
- **On but not reachable:** skills never skip silently. They print `graph on but not reachable: run /factory:graph
  provision <provider> or set DATABASE_URL`; interactive `/factory:init` then runs the provision; headless runs record
  the line under Assumptions and go on without the graph steps.

## The connection string

It never enters `.claude/sdlc.json`, git, a command line or any output. It is read, in this order, from: the variable
`graph.url_env` names (default `DATABASE_URL`) in the shell, unless it holds the local default (the plugin's
`.mcp.json` fills `DATABASE_URL` with that default for its server, so there it counts as unset); `env.<url_env>` in
`.claude/settings.local.json`; `<url_env>=...` in `.env`; else the local default
`postgresql://sdlc:sdlc@localhost:5435/sdlc`. Both files are gitignored (the script adds the line when it is missing,
and creates them with mode 600). The MCP server reads them at each call, so a URL stored mid-session is used without a
restart. The provisioning script passes the URL to `sdlc-graph` in the child's environment only and redacts every
line it prints.

## Provisioning, common to all four

- Decisions are asked once, in one batch, before the first command. Headless, a decision missing from the arguments
  stops the run (`ESCALATION: graph provision <provider> needs <decision>`): nothing is created in a cloud account
  unattended. `local` has no decisions and runs unattended.
- Idempotent: what exists is found and reused; a second run prints `already there`.
- Each run ends with `sdlc-graph db init` (it creates the `vector` and `pg_trgm` extensions and applies `sql/*.sql`)
  and `status`, then records the evidence row `graph_provisioned {provider, where, schema}` on the repository's
  `repo` node (`sql/010_repo_node.sql`): once per database, a repeat reports `already there`. `where` names the host,
  port and database, never a credential.
- Under the pack's sandbox a cloud procedure can fail on the provider's credentials (`~/.aws` is unreadable to
  sandboxed commands) or egress (the provider's domains are not in `sandbox.network.allowedDomains`); the skill then
  hands the human the command to run in their own terminal.

## local

- **What:** `docker compose -f ${CLAUDE_PLUGIN_ROOT}/docker-compose.yml up -d db` (pgvector/pgvector:pg16 on
  127.0.0.1:5435), wait for `pg_isready` and a connection, `db init`, write `DATABASE_URL` to
  `.claude/settings.local.json`, status, evidence.
- **Decisions:** none. **Egress:** Docker Hub once, for the image.
- **Idempotence:** a database that already answers at the URL (CI's service container, a second run) skips Docker:
  `db init` only. A second copy beside the first takes `--compose-project <name>`, and the port comes from the URL.
- **In the pack's sandbox:** the sandbox excludes a docker compose command typed at the top level, not one the
  script starts (that child cannot read `~/.docker` and exits 125). The script then writes the compose variables to
  `.sdlc-stage/graph-compose.vars` and prints two `next:` commands, which the skill runs one per call:
  `docker compose --env-file .sdlc-stage/graph-compose.vars -f "${CLAUDE_PLUGIN_ROOT}/docker-compose.yml" up -d db`,
  then `graph.py provision local --no-docker` (it waits for the database without calling Docker, runs `db init`,
  and deletes the vars file). Still unattended; the sandbox itself is unchanged. The sandbox also denies writes
  to `.claude/settings.local.json`: when the URL comes from the shell or is the local default the script prints
  `not stored: ...` and finishes (status and evidence); a neon or aws URL with no other source fails plainly and
  the human reruns the same provision command in their own terminal.

## neon

- **What:** a Neon project named `<repo>-graph`, Postgres 17, in the region asked once; the `vector` extension; the
  connection string of the direct endpoint (not the `-pooler` host, which cannot run DDL); `db init`; env file;
  status; evidence.
- **With the Neon MCP:** `list_projects`, `create_project` (`pg_version` 17), `run_sql` `CREATE EXTENSION IF NOT EXISTS
  vector`, `get_connection_string`; the string is staged in `.sdlc-stage/graph-url` and stored with
  `url --from-file`, which deletes the file; then `provision neon` runs `db init`, status and evidence.
- **Without it:** neonctl with `NEON_API_KEY` exported in the human's own shell (never pasted in chat, never on a
  command line): `projects list`, `projects create --name <repo>-graph --region-id <region>`, `connection-string
  --project-id <id> --database-name neondb`.
- **Decisions:** region; whose Neon account. **Egress:** `console.neon.tech`, `*.neon.tech`.
- **Idempotence:** a stored URL that answers creates nothing; a project with the name is reused.

## aws

- **Decisions (asked once):** the shape, the region, the account id (checked against `aws sts get-caller-identity`;
  a mismatch is refused), who pays (recorded as a `payer` tag and in the evidence). **Egress:** `*.amazonaws.com`.
- **aws-demo** (a pilot): RDS for PostgreSQL 16, `db.t4g.micro`, 20 GB encrypted, default VPC, publicly reachable
  only from `--operator-cidr` (the operator's IP, `/32`), master password managed by RDS in Secrets Manager. The
  script finds or creates the security group and the instance, waits for it, reads the secret, assembles
  `postgresql://...?sslmode=require` without printing it, then `db init` (`CREATE EXTENSION vector`), env file,
  status, evidence.
- **aws-private** (the team's pipeline): Aurora PostgreSQL Serverless v2 (0.5 to 2 ACU) in the private subnets of
  `--vpc-id`, 5432 open to the VPC CIDR only, generated credentials in Secrets Manager, deletion protection, 7-day
  backups. The first run writes a CDK app under `infra/graph-db/` in the customer repo (`app.py`, `cdk.json`,
  `requirements.txt`, `README.md`; a differing file gets a `.new`) and stops with `pending:`; the team deploys stack
  `FactoryGraphDb` through its pipeline. The next run, from a runner in the VPC or through an SSM port forward
  (`--via-host localhost --via-port <port>`), reads the stack outputs and the secret, then `db init`, env file,
  status, evidence.
- **Idempotence:** a stored URL that answers creates nothing; an existing instance, security group or stack is reused.

## spanner

- **What:** `gcloud spanner instances create` (Enterprise edition, which Spanner Graph needs) and `gcloud spanner
  databases create --ddl-file` with the DDL `sdlc-graph spanner ddl` renders, then `sdlc-graph spanner export
  --project --instance --database` mirrors the ledger and the newest snapshot into it (rows replaced, schema kept);
  Spanner Graph answers GQL over the mirror (`sdlc-graph spanner query`). `graph.spanner` and `graph.provider` are
  written to `.claude/sdlc.json`.
- **Decisions:** project, instance id and config (for example `regional-us-central1`), database id, processing units
  (100 is the smallest). **Egress:** `*.googleapis.com`. The human signs in with gcloud auth login and gcloud auth
  application-default login.
- **Precondition:** the Postgres ledger is reachable (provision local, neon or aws first); the script refuses
  otherwise.
- **Idempotence:** an existing instance and database are reused; a second run refreshes the mirror and says
  `already there`.

## The Spanner limit

The ledger (contracts, transitions, evidence, runs) runs on PostgreSQL with pgvector. Spanner today is the graph
mirror (DDL export, loader, GQL queries, the emulator smoke test), not the ledger's primary store, because the ledger
uses pgvector and PostgreSQL semantics that Spanner's PostgreSQL interface does not provide. A `spanner` provider
therefore means the ledger on local, Neon or AWS PostgreSQL and the graph mirrored to Spanner Graph. A native Spanner
ledger is a later adapter.

## Costs

Approximate list prices; check the provider's pricing page for the region before choosing.

- **local:** nothing beyond the laptop or runner.
- **neon:** the free tier (0.5 GB storage, scale to zero) holds a pilot; paid plans start at about $19 a month.
- **aws-demo:** about $12 to $15 a month for `db.t4g.micro` and 20 GB in us-east-1 (less while free-tier eligible).
- **aws-private:** about $45 a month at the 0.5 ACU floor plus storage and I/O; more under load up to the 2 ACU cap.
- **spanner:** about $65 to $90 a month at 100 processing units on the Enterprise edition, plus storage; the
  Postgres ledger's own cost comes on top.
