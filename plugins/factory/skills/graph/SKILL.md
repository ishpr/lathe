---
name: graph
description: Turn the code graph and ledger on or off, show whether its database is reachable, store its connection string without printing it, or build the database where the customer wants it — local Docker, Neon, AWS (RDS or Aurora) or Google Cloud Spanner as the graph mirror. Use when the user says graph status, turn the graph off, set the database URL, or provision the graph database, and when another factory skill reports "graph on but not reachable".
argument-hint: "status | on | off | url [--from-aws-secret ARN --region R | --from-gcp-secret NAME --project P] | provision <local|neon|aws|spanner> [decisions]"
allowed-tools:
  - Read
  - Edit(.sdlc-stage/**)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" status)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" migrate)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" on)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" off)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" url --from-file .sdlc-stage/graph-url)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" url --from-aws-secret *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" url --from-gcp-secret *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" provision local *)
  - Bash(docker compose --env-file .sdlc-stage/graph-compose.vars -f "${CLAUDE_PLUGIN_ROOT}/docker-compose.yml" up -d db)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" provision neon *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" provision aws *)
  - Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" provision spanner *)
  - Bash(curl -s https://checkip.amazonaws.com)
  - mcp__plugin_factory_graph__graph_status
  - mcp__graph__graph_status
  - mcp__neon__list_projects
  - mcp__neon__create_project
  - mcp__neon__run_sql
  - mcp__neon__get_connection_string
---
The code graph and the ledger live in one PostgreSQL database with pgvector. This skill turns them on or off and
builds that database where the customer wants it. Arguments: $ARGUMENTS

**One command per Bash call, exactly as written below**, with the `<placeholders>` filled in: no chaining, no pipe, no
redirect, no shell variable. Each allow rule matches one command. The script prints every line already redacted;
never print a connection string, a password or an API key yourself, and never ask for one in chat.

The run is **headless** when the arguments hold `--headless` or no human will answer. Then nothing is asked: a
decision missing from the arguments ends the run with `ESCALATION: graph provision <provider> needs <decision>`, and
nothing is created in a cloud account.

## Commands

| Command | What it does | Runs |
|---|---|---|
| `status` | graph on or off, provider, where the URL comes from (redacted), reachable, schema version (with `behind by N migrations` when the database is older than this plugin), snapshot and node counts | `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" status` |
| `migrate` | applies the migrations the database is behind by (`db init`; idempotent). Run it when `status` says `behind by N migrations`; `/factory:init` runs it too | `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" migrate` |
| `on` / `off` | flips `graph.enabled` in `.claude/sdlc.json`; off skips every graph step with one line and the orchestrator refuses to start | `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" on` (or `off`) |
| `url` | stores a connection string in the local env file without printing it | see "Storing a URL" |
| `provision local` | Docker Postgres with pgvector on port 5435, `db init`, env file, status, evidence | `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" provision local` |
| `provision neon` | a Neon project (Postgres 17), `db init`, env file, status, evidence | see "neon" |
| `provision aws` | RDS for PostgreSQL 16 (`aws-demo`) or an Aurora Serverless v2 CDK stack (`aws-private`) | see "aws" |
| `provision spanner` | Spanner instance and database from the ledger's DDL, then the graph mirrored into it | see "spanner" |

The MCP tool `graph_status` (`mcp__plugin_factory_graph__graph_status`, or `mcp__graph__graph_status`) answers the
same as `status`. The connection string never enters `.claude/sdlc.json` or git: it is read from the variable
`graph.url_env` names (default `DATABASE_URL`) in the shell, then `env` in `.claude/settings.local.json`, then
`.env`; both files are gitignored and the script adds the line when one is missing. The graph's MCP server reads the
files at every call, so a stored URL works without restarting the session.

## Storing a URL

- From AWS Secrets Manager: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" url --from-aws-secret <secret arn> --region <region>`
  (a URL, or RDS JSON with username, password, host, port, dbname).
- From Google Secret Manager: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" url --from-gcp-secret <secret name> --project <project>`.
- Typed by a person: ask a human to run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" url` in their own
  terminal (with the plugin path filled in); it reads the string at a hidden prompt. You never run that form.

Then run `status`.

## Provisioning

Ask every decision of the chosen provider once, in one batch, before the first command (headless: they come from
the arguments or the run stops, see above). Each run is idempotent: what exists is found and reused, and a second
run ends with `already there`. Each successful run records the evidence row
`graph_provisioned {provider, where, schema}` on the repository's `repo` node in the ledger, once per database. Every procedure ends with `db init`
(which creates the `vector` extension) and `status`; quote the script's last four lines.

### local

Decisions: none. Egress: none (the image `pgvector/pgvector:pg16` is pulled from Docker Hub once).
Run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" provision local`. It runs `docker compose -f
${CLAUDE_PLUGIN_ROOT}/docker-compose.yml up -d db`, waits for `pg_isready` and a connection, runs `sdlc-graph db
init`, writes `DATABASE_URL` to `.claude/settings.local.json`, then status and evidence. When the database at the
URL already answers (CI's service container, or a second run), Docker is skipped and only `db init` runs. Runs
unattended, headless too.

In the pack's sandbox (the settings `/factory:init` writes) Docker works only as its own top-level command: the
sandbox excludes a docker compose command typed at the top level, but a docker started by the script is sandboxed, cannot read `~/.docker` and
exits 125. The script then writes the compose variables (port, container and project name; nothing secret) to
`.sdlc-stage/graph-compose.vars`, ends with `failed:`, and prints two `next:` lines. Run them, one per call, without
asking (this is still the same unattended procedure):

1. `docker compose --env-file .sdlc-stage/graph-compose.vars -f "${CLAUDE_PLUGIN_ROOT}/docker-compose.yml" up -d db`
2. `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" provision local --no-docker`, with the same
   `--compose-project <name>` when the first run had one. It waits for the database without calling Docker, then
   `db init`, the env file, status and evidence, and deletes the vars file.

If the docker line is denied or fails too, quote its output and give the human both lines to run in their own
terminal; then run `status`.

The sandbox also denies writes to `.claude/settings.local.json`. When the URL already comes from the shell or is the
local default, the script prints `not stored: ...`, still records status and evidence and exits 0: quote that line
in the summary so the human can store the URL later; it is not a failure of the provisioning. When nothing else
leads to the database (a neon or aws URL), the script ends `failed: ... a human reruns this same provision command
in their own terminal`: say exactly that.

### neon

Decisions: the region (for example `aws-us-east-2`), and whose Neon account (the free tier is enough for a pilot).
Egress: `console.neon.tech`, `*.neon.tech`.

- With the Neon MCP connected (tools `list_projects`, `create_project`, `run_sql`, `get_connection_string`; the
  allow rules above name the `mcp__neon__` spelling, another server name needs its own): `list_projects` with the
  search `<repo>-graph`; none, then `create_project` with name `<repo>-graph`, `pg_version` 17 and the region;
  `run_sql` `CREATE EXTENSION IF NOT EXISTS vector`; `get_connection_string` for the project (the direct endpoint:
  never the `-pooler` host, which cannot run DDL). Write the string, and nothing else, with the Write tool to
  `.sdlc-stage/graph-url`, never echo it, then run
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" url --from-file .sdlc-stage/graph-url` (it stores the string
  and deletes the file), then `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" provision neon` (the stored URL
  answers, so it runs `db init`, status and evidence only).
- Without the MCP: the human exports `NEON_API_KEY` in their own shell before starting the session (never pasted in
  chat). Run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" provision neon --region-id <region>`. The script
  uses neonctl: projects list (an existing `<repo>-graph` is reused), projects create, connection-string (direct
  endpoint), then `db init`, the env file, status and evidence.

### aws

Decisions, asked once: the shape, the region, the account id, and who pays (a team or cost centre; it is recorded
as a tag and in the evidence). Egress: `*.amazonaws.com`. The AWS CLI must be signed in (the human runs aws sso
login); the script checks the credentials' account against the one given and refuses a mismatch.

- `aws-demo` (a pilot, quick): RDS for PostgreSQL 16, `db.t4g.micro`, 20 GB encrypted, in the default VPC,
  publicly reachable from the operator's IP only, the master password managed by RDS in Secrets Manager. Get the IP
  with `curl -s https://checkip.amazonaws.com` (or ask), then run
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" provision aws --shape aws-demo --region <region> --account <account id> --payer <who pays> --operator-cidr <ip>/32`.
  It finds or creates the security group (5432 from that address only) and the instance, waits until it is
  available, reads the secret and assembles the connection string without printing it, then `db init`, the env
  file, status and evidence.
- `aws-private` (the team's own pipeline): Aurora PostgreSQL Serverless v2 (0.5 to 2 ACU) in the private subnets of
  a VPC, reached from runners in the VPC or through SSM. Decision: the VPC id too. Run
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" provision aws --shape aws-private --region <region> --account <account id> --payer <who pays> --vpc-id <vpc id>`.
  Before the stack exists it writes a CDK app under `infra/graph-db/` (app.py, cdk.json, requirements.txt,
  README.md; an existing different file gets a `.new` beside it) and stops with `pending:`: the team deploys it
  through its pipeline and commits it like any other change. Run the same command again once the stack
  `FactoryGraphDb` is deployed, from a runner in the VPC, or from a laptop through an SSM port forward with
  `--via-host localhost --via-port <local port>` added: it reads the stack outputs and the generated secret, then
  `db init`, the env file, status and evidence.

### spanner

**The limit, stated plainly:** the ledger (contracts, transitions, evidence, runs) runs on PostgreSQL with pgvector;
Spanner is the graph mirror (DDL export, loader, GQL queries, the emulator smoke test), not the ledger's store,
because the ledger uses pgvector and PostgreSQL semantics that Spanner's PostgreSQL interface does not provide. So
`spanner` means the ledger on local, Neon or AWS PostgreSQL and the graph mirrored to Spanner Graph. A native
Spanner ledger is a later adapter.

Decisions: the Google Cloud project, the instance id and config (for example `regional-us-central1`), the database
id, and the processing units (100 is the smallest; Spanner Graph needs the Enterprise edition). Egress:
`*.googleapis.com`. The human signs in first (gcloud auth login, then gcloud auth application-default login).
The ledger must already be reachable (provision local, neon or aws first; the script refuses otherwise). Run
`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/graph.py" provision spanner --project <project> --instance <instance> --database <database> --config <instance config> --processing-units <n>`.
It describes, and creates only when missing, the instance (Enterprise edition) and the database with the DDL that
sdlc-graph spanner ddl prints, mirrors the ledger and the newest snapshot into it with sdlc-graph spanner export,
writes `graph.spanner` and `graph.provider` in `.claude/sdlc.json`, and records the evidence. A second run refreshes
the mirror and says `already there`.

## When it fails

A `refused:` line is a missing decision, the graph being off (`on` first), or a wrong account: answer it and rerun.
A `failed:` line names the step; quote it. When the session runs in the pack's sandbox, a cloud procedure can fail
on the provider's credentials or egress: then print the command line you would have run so the human runs it in
their own terminal, and run `status` afterwards.
