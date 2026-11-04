# From epics to executed contracts: an MCP ledger, an index, and Claude Code as the executor

A design note on the question "how should we think about an MCP with pgvector and
embeddings, like a graph state machine, that turns epics and features into software contracts
and workflows, executed through Claude Code's / commands, with the right model forced onto the
right job?" It uses the vocabulary of Anthropic's AI-native SDLC playbook
(https://claude.com/resources/articles/the-ai-native-sdlc-playbook, Louis Claxton, 2026-08-21).

## 0. Short answer

Build it, with three corrections to the framing.

1. **The repository stays the system of record for artifacts.** The playbook's loop commits one
   artifact per stage (`intent.md` → `spec.md` → `plan.md` → diff and tests → PR with findings →
   incident record) and uses the commit chain as the audit trail. The `/factory:epic` output is the
   intent, the `/ticket` contract is the spec plus plan. The MCP service holds the **ledger**
   (state, transitions, evidence, decisions, runs) and the **index** (embeddings), never a second
   copy of the artifacts. The playbook calls this "repo as source of truth"; Jira or Linear can
   be linked by ID, which is its "linkage" option.
2. **pgvector is retrieval, not the state machine.** The state machine is plain relational
   tables: typed nodes, typed edges, a state column, a transitions table, and evidence rows that
   guard each transition. Vectors answer "what is similar": a duplicate ticket, a past contract
   for the same area, the rules that apply to these paths, the post-mortem that already covers
   this failure. Keep the graph in Postgres (adjacency tables plus recursive CTEs). A graph
   database buys nothing at this scale.
3. **Claude has no embeddings endpoint.** Anthropic points to Voyage AI for embeddings
   (`voyage-3-large` for prose, `voyage-code-3` for code); an open model such as
   `bge-m3` or `nomic-embed-text` works if the data must stay in the VPC. Store the embedding
   model name and dimension with every vector so a model change is a re-index, not a mystery.

Model routing: enforce it in three places that cannot drift apart, and let the service publish
the policy (section 4). Fable is a budgeted resource per epic, not a default.

## 1. Where this sits in the playbook

| Playbook stage | Artifact (in the repo) | Pack command | Ledger transition | Who decides |
|---|---|---|---|---|
| Plan | `docs/epics/<slug>.md` (`/factory:epic`: outcome, hypothesis, riskiest assumption, metrics, guardrail) | `/factory:epic` | `epic: drafted → accepted` | product owner |
| Design | features and stories as sections of `docs/epics/<slug>.md`, then `.claude/work/<slug>/contract.md` | `/factory:epic` (feature), `/factory:ticket` | `feature: proposed → ready`; `contract: drafted → ready` (decisions resolved, acceptance lines present, allowed paths set) | product owner, tech lead for tier high |
| Build | failing tests commit, frozen; the diff | `/tests`, `/implement` | `contract: ready → tests_frozen → implementing` | engineer (plan approval) |
| Test | verify stamp, acceptance table | `/verify` | `implementing → verified` (evidence: tests pass, ratchet clean, frozen tests unchanged, acceptance pass) | deterministic |
| Deploy | PR with evidence, reviewer findings, CI run | `/ship`, `/review-pr`, CI | `verified → in_review → approved → merged` (evidence: non-author code-owner approval, CI green on base-branch scripts) | code owner; the agent that wrote it cannot approve |
| Release | release request, ready poll, rollback step | `/deploy` (human), `/rollback` | `merged → staged → released` (evidence: ready poll, smoke) | release manager; "the agent may act up to the production gate and cannot pass it" |
| Maintain | incident record → new `intent.md`, new rule or test | metric bands, `/audit` | `incident: open → triaged → intent` | service owner |

Everything in the "Pack command" column exists in the pack
(`/review-pr`, `/rollback`, acceptance lines and frozen tests included). The ledger adds the
state and the evidence; it does not add steps.

## 2. The three layers

### 2.1 Ledger (Postgres)

```sql
-- Enumerations are CHECK constraints rather than CREATE TYPE, so the migration re-runs safely
-- (plugins/factory/sql/002_ledger.sql is the authoritative form of this sketch).
create table node (
  id          uuid primary key default gen_random_uuid(),
  kind        text not null check (kind in ('epic','feature','story','contract','change','release','incident','decision','rule')),
  slug        text not null unique,            -- epic-rate-limits, contract-rate-limit-window
  title       text not null,
  repo        text not null,                   -- owner/name
  path        text,                            -- artifact path in the repo, e.g. docs/epics/rate-limits.md#S3
  commit_sha  text,                            -- the artifact version this row describes
  state       text not null,                   -- per-kind state machine below
  tier        text check (tier in ('trivial','low','medium','high')),
  external_id text,                            -- Jira/Linear key (linkage)
  created_by  text not null, created_at timestamptz default now(), updated_at timestamptz default now(),
  touched_symbols text[] not null default '{}', -- code-graph symbol keys the change declares (§10.3 layer 1)
  capabilities    jsonb not null default '{}',  -- paths, commands, egress, data, secrets (§10.3 layer 3, §14)
  acceptance      jsonb not null default '[]',  -- criteria with their test names
  attrs           jsonb not null default '{}'   -- per-kind extras: riskiest_assumption, guardrail_metric, intake flags
);
create table edge (                             -- the graph
  src uuid references node, dst uuid references node,
  rel text not null check (rel in ('decomposes_into','depends_on','verified_by','shipped_in','supersedes','caused_by','governed_by')),
  primary key (src, dst, rel)
);
create table transition (                       -- the state machine's history
  id bigserial primary key, node_id uuid references node, from_state text, to_state text,
  actor text not null,                          -- '@alice' | 'agent:sonnet-5.5' | 'ci:github'
  evidence_ids bigint[] not null default '{}', reason text, at timestamptz default now()
);
create table evidence (                         -- what a transition may cite
  id bigserial primary key, node_id uuid references node,
  kind text not null,                           -- tests_failing_first | tests_frozen | verify_stamp | acceptance | ratchet | review_approval | ci_run | ready_poll | smoke
  source text not null,                         -- 'local' | 'ci:<run url>' | 'github:<review id>'
  signed_by text,                               -- CI OIDC subject for CI evidence; null for local
  payload jsonb not null, at timestamptz default now()
);
create table decision (                         -- "decisions a human makes first"
  id bigserial primary key, node_id uuid references node, question text, options jsonb,
  recommendation text, decided_by text, choice text, due date, resolved_at timestamptz
);
create table run (                              -- every agent run against a node
  id bigserial primary key, node_id uuid references node, stage text,
  requested_model text not null,                -- what the spawn asked for (PreToolUse hook)
  resolved_model text,                          -- what actually ran (PostToolUse resolvedModel)
  model_tier text not null, tokens_in bigint, tokens_out bigint, cost_usd numeric, turns int,
  started_at timestamptz, finished_at timestamptz, outcome text,
  tool_use_id text unique, agent_type text,     -- joins the PreToolUse and PostToolUse hook calls
  auth_source text,                             -- subscription | api_key | oauth_token | claude_platform_aws | bedrock (§12.5)
  summary text, last_assistant_message text,    -- injected into the next step's prompt (§12.1, "seance")
  meta jsonb not null default '{}'              -- modelsUsed, totalDurationMs, rewrite notes
);
```

**State machines** (one per kind; stored as data in `sdlc_graph/ledger/state_machines.json`,
versioned in the repo and changed by pull request, so a change is a reviewed file, not a deploy.
A table was the first plan; a file under review is the playbook's own rule and needs no
migration):

- `epic`: drafted → accepted → in_progress → measured → closed (or rejected). `accepted` needs a
  resolved riskiest assumption and a guardrail metric; `measured` needs a metrics snapshot after
  the first feature releases.
- `feature`: proposed → ready → building → released → evaluated.
- `contract` (story + agent contract): drafted → ready → tests_frozen → implementing → verified →
  in_review → approved → merged. Guards, in order: `drafted → ready` needs decisions resolved,
  acceptance lines, allowed paths, the defaults block and capabilities present, the capabilities
  approved and the ticket text reviewed, and it is a human's transition (`/ticket` writes the
  contract and stops at `drafted`); `ready → tests_frozen` needs `tests_failing_first` and
  `tests_frozen` evidence whose hashes match the files and whose failures are for the right
  reason; `implementing → verified` needs `verify_stamp` with `frozen_ok = true`,
  `acceptance.failed = 0`, every criterion covered by a test and `ratchet.new = 0`;
  `verified → in_review` needs `pr_url`; `approved` needs `review_approval` from a non-author code
  owner plus a binding `ci_run` green, from a human or CI actor; `merged` needs the merge commit
  SHA. A human may also record `in_review → merged` directly, for a PR merged outside the loop.
- `release`: requested → staged → released → verified (or rolled_back). `released` needs a
  human actor; `verified` needs `ready_poll` and `smoke` evidence.
- `incident`: open → triaged → intent (links to the new epic/feature node) → closed.

The service evaluates guards; a transition request without the evidence returns the missing
kinds. **Local evidence is advisory; CI evidence is binding.** A `verify_stamp` posted from a
laptop moves a contract to `verified` for the developer's own view, but `approved` requires the
same checks from a CI run whose evidence row carries the GitHub OIDC subject. That is REC-43
("CI must not run the PR's own gate scripts") restated as a data rule.

### 2.2 Index (pgvector plus full-text)

```sql
create extension if not exists vector;
create table chunk (
  id bigserial primary key, node_id uuid references node, section text,   -- 'outcome', 'acceptance', 'decisions', ...
  text text not null, tsv tsvector generated always as (to_tsvector('english', text)) stored,
  embedding vector(1024) not null, embed_model text not null, at timestamptz default now()
);
create index on chunk using hnsw (embedding vector_cosine_ops);
create index on chunk using gin (tsv);
```

Chunk by section, not by file: an epic's "Riskiest assumption" and a contract's "Acceptance"
are the units people search for. Retrieval is hybrid (cosine plus BM25-style rank, merged with
reciprocal rank fusion) because ticket text is full of identifiers that embeddings blur.

What the index is for, in order of value:

1. **Dedupe at intake.** `/ticket` asks `intake_context` before it writes anything: "is there an
   open contract within 0.15 cosine distance of this?", and quotes it back instead of writing a
   second one. Without an embedding provider, or for contracts not yet indexed, a title trigram
   match and the external ID are the fallback; `contract_draft` repeats the check and refuses a
   duplicate unless the caller overrides it explicitly.
2. **Precedent.** "The last three contracts that touched `app/auth/**`": their decisions, their
   test names, what the reviewer flagged. This is the cheapest way to make a new agent run
   behave like the team.
3. **Applicable rules.** Rules (`.claude/rules/*.md`, `REVIEW.md`) are nodes too; the service
   returns the rules whose embedded text is nearest the contract and whose `paths:` match the
   allowed paths. The skill pastes them into the contract instead of hoping the agent reads them.
4. **Incidents to intent.** A new incident retrieves similar past incidents and the rule or test
   that closed them; if one exists, the triage is "the rule did not fire", which is a different
   ticket from "write a rule".

Do not embed source code for this. `grep`, the route table and the AST checkers answer code
questions exactly; embeddings answer them approximately. The one exception is a short summary
per code-graph symbol (kind, qualified name, path and the extractor's attributes; never the
source body), embedded so
`graph_search` can find a symbol by description. It is rebuilt with the graph per SHA, so it
cannot go stale independently of it.

### 2.3 Executor (Claude Code)

The playbook's ladder is "manual prompting → codified slash commands → merge-triggered
automation with human review at gates". The pack is at rung two. The ledger lets rung three
happen without a second orchestration system:

- **Skills call the service through MCP tools** (section 3). `/ticket` ends with
  `contract.draft` (and `contract.update` as the contract is refined) and stops at `drafted`:
  `ready` is a human's transition once the decisions are confirmed, never the skill's;
  `/tests` posts `tests_frozen` evidence; `/verify` posts the stamp;
  `/ship` opens the PR and posts its URL; `/deploy` is a human-only `release.request`.
- **Hooks stay the enforcement layer.** The MCP server never runs code; it returns the next
  allowed action. Execution stays in Claude Code, where the edit guard, the Bash guard, the
  ratchet and the sandbox apply. The playbook: skills are advisory controls, hooks are
  deterministic enforcers; the ledger is the third kind, an evidence ledger.
- **Headless stages** run through the one wrapper (`scripts/run_headless.sh`, REC-52) or the
  Agent SDK, triggered by a transition: `feature → ready` opens a `claude -p` run that executes
  `/feature` and stops at every human decision; `incident → open` at a 2σ band runs a read-only
  diagnosis and posts it as evidence. Each run is a `run` row with its model and cost.
- **CI posts evidence**, not comments: the gate job calls `evidence.add` with the run URL and
  the OIDC subject, so `approved` is computable and nobody pastes "tests pass" into a PR.

## 3. MCP tool surface

Keep it small and typed; every tool returns the ledger's view, never free text to be re-parsed.
The served names use underscores (`contract_transition`). `graph.search` is two tools:
`graph_search` over code-graph symbols and `node_search` over the ledger's chunks. Four tools
sit beside the table: `intake_context` (duplicates, precedent and rules before a contract is
written), `incident_similar` (use 4 of §2.2), `release_request` (a `release` node in `requested`
from merged contracts) and the read-only code-graph tools of §10.4.

| Tool | Does | Never does |
|---|---|---|
| `graph.search(query, kinds, paths, k)` | hybrid retrieval over chunks, filtered by kind and path overlap | ranks by anything an agent wrote in the query |
| `node.get(slug)` / `node.neighbours(slug, rel)` | one node with its state, open decisions, evidence summary, edges | returns artifact bodies (read those from the repo) |
| `epic.decompose(slug)` | returns *proposed* feature nodes from the epic text with the walking-skeleton rule applied | commits them; a human accepts each |
| `contract.draft(feature, ticket_text)` | runs intake (`ticket_intake.py` rules server-side too), dedupes, returns a contract skeleton with precedent and applicable rules filled in | marks anything ready |
| `contract.transition(slug, to, evidence_ids)` | evaluates the guard, records the transition or returns the missing evidence kinds | accepts a transition from an agent actor into `approved`, `released` or `closed` |
| `evidence.add(slug, kind, payload, source)` | stores evidence; CI sources must carry a verified OIDC claim | upgrades local evidence to binding |
| `decision.propose(slug, question, options, recommendation)` / `decision.resolve(id, choice)` | the "decisions a human makes first" table | lets an agent actor resolve |
| `workflow.next(slug)` | the next allowed step: the slash command, the required evidence, the model tier, the remaining Fable budget | runs it |
| `run.start(slug, stage, model)` / `run.finish(id, outcome, usage)` | records who ran what with which model | — |
| `metrics.snapshot(repo, since)` | the playbook's per-stage metrics from the ledger (intent survival, intent-to-spec time, first-pass merge, rework cycles, gate wait, repeat incidents) | — |

Resources: `contract://<slug>`, `epic://<slug>`, `rules://<repo>/<path-glob>` for read-only
context. Prompts: none; the skills own the prompts, in the repo, under review.

Serve it as a remote MCP server (Streamable HTTP) with OAuth, so managed settings can list it
as the only allowed server (the playbook's "managed-only MCP servers"). The server is an OAuth
resource server: it verifies bearer JWTs against the issuer's JWKS (issuer, audience, expiry,
required scopes) and publishes its protected-resource metadata; `--http` refuses to start
without that configuration unless `--insecure-no-auth` is given, which binds to loopback only.
stdio, for a single engineer's session, needs none of it. A Python FastMCP or a
TypeScript server are both fine; the schema above is the design, the framework is a detail.

## 4. Forcing the right model onto the right job

The policy is a table the service publishes and every runner reads:

| Tier | Model | Stages | Why |
|---|---|---|---|
| planner (sparing) | Fable 5.1 | epic → feature slicing for tier-high epics; contract design when tier is high or the riskiest assumption is unresolved; reconciling conflicting reviewer findings; incident post-mortem → new rule | needs the whole graph in view and the judgment call is expensive to get wrong |
| orchestrator | Opus 5.5 | reads `workflow.next`, decides, spawns, synthesises reviews, writes the PR evidence section | judgment across lenses, not volume |
| executor | Sonnet 5.5 | `/tests`, `/implement`, single-lens review, docs, migrations rendering | the bulk of tokens; good enough when the contract is tight |
| utility | Haiku 5.5 | tier and stack classification, dedupe scoring, log and CI triage, status summaries, formatting evidence tables | mechanical and cheap |

Four mechanisms, used together, make the table enforceable rather than advisory. The Claude
Code facts here were checked against the current docs on 2026-11-02 (sub-agents, hooks, model
configuration and Agent SDK pages).

1. **Agent definitions carry the default model, not the pin.** `.claude/agents/test-author.md`
   says `model: sonnet`, `reviewer.md` `model: sonnet`, `planner.md` `model: opus`, `triage.md`
   `model: haiku` (`fable` and full IDs are also accepted). A skill that spawns a subagent names
   the agent, not the model, so the choice is reviewed in a PR. But the documented resolution
   order is: the per-invocation `model` argument, then the definition's frontmatter, then
   `CLAUDE_CODE_SUBAGENT_MODEL`, then the session model. A spawn that passes `model` wins over
   the file, which is why the next mechanism exists.
2. **A PreToolUse hook on the `Agent` tool is the pin.** Its input carries `tool_input.prompt`,
   `description`, `subagent_type` and `model`. The hook reads the policy table
   (`routing_policy.json`) and the current stage (a `[sdlc stage=…]` tag in `description`, then
   `SDLC_STAGE`, then the agent type's own stage, then the contract's state in `current.json`;
   never the prompt, which an agent writes). A missing model, a model on the wrong tier, and Fable
   requested outside a planner stage are all **rewritten** to the stage's alias through
   `hookSpecificOutput.updatedInput`, with one logged line and the request kept on the `run` row:
   an unattended step keeps moving on the right tier instead of stopping on a typo, and the drift
   still shows in the metrics. The hook exits 2 only for the fourth Fable call on one epic (the
   slot is reserved atomically in the ledger, refused until a human raises the budget through a
   resolved `fable_budget_increase` decision) and for any input or error it cannot handle, so a
   broken hook fails closed. The matching PostToolUse hook receives `resolvedModel`, `modelsUsed`,
   `totalTokens` and `totalDurationMs` for the finished subagent, which is the `run` row: the
   model that actually ran, not the one that was asked for. `SubagentStart` cannot block, so it
   is not the place for this. This is the same shape as the pack's commit guard.
3. **Workflows and the SDK pass the model explicitly.** Workflow scripts (`agent(prompt,
   {model})`) and Agent SDK `agents={...: AgentDefinition(model=...)}` with `max_budget_usd`
   never inherit; the same PreToolUse hook runs inside SDK sessions and catches the ones that
   try. The orchestrator session runs on Opus 5.5 by setting `model` in the project settings;
   Fable is only ever a subagent or a separate, budgeted session. `CLAUDE_CODE_SUBAGENT_MODEL`
   with `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` exists but forces one model onto every subagent,
   so it is a "Sonnet-only cost mode" switch, not a tiering tool.
4. **Managed settings pin the aliases.** `ANTHROPIC_DEFAULT_OPUS_MODEL`,
   `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL` and
   `ANTHROPIC_DEFAULT_FABLE_MODEL` map the aliases to exact model IDs for everyone, and the
   managed `availableModels` list is the ceiling an alias may resolve to. An upgrade is one
   managed-settings change and the `run` table shows the switch date. `PreModelSwitch` and
   `PostModelSwitch` hooks carry `from_model` and `to_model`, so a mid-session switch to Fable is
   at least logged.

Measure it: `run` rows give "runs by tier versus policy", "Fable calls per epic" and "cost per
merged contract by tier". If Sonnet's first-pass merge rate on tier-low contracts matches Opus's,
the policy tightens; if tier-high contracts bounce in review, the planner tier widens.

## 5. The walking skeleton (two weeks, one engineer)

Week 1: the `node`, `edge`, `transition`, `evidence`, `decision` tables; the server with
`node.get`, `contract.transition`, `evidence.add`, `decision.*` and `workflow.next`; `/ticket`
and `/verify` writing through it; the CI gate posting one `ci_run` evidence row with its OIDC
subject. Prove one contract moves drafted → merged with every transition cited.

Week 2: the `chunk` table and `graph.search`; `/ticket` dedupe and precedent; the `Agent`
spawn hook and the `run` table; `workflow.next` driving `/feature` end to end. Then, in order:
headless runs on `feature → ready`, `metrics.snapshot`, the 1σ/2σ/3σ bands from the playbook
with their bounded actions (log; read-only diagnosis; PR or pre-approved runbook).

## 6. What not to build

- A graph database, a workflow engine (Temporal, Airflow) or an LLM that decides transitions.
  Transitions are guarded by evidence rows; the only model in the loop is the one producing the
  artifact.
- A second copy of intents or contracts in the database. Store the path and the commit SHA.
- Auto-approval of anything. The playbook's rule is absolute: findings never approve or block a
  PR alone, and the agent that wrote code cannot approve it.
- Embeddings over the codebase. Low value here, real cost, and a stale-index failure mode.

## 7. Risks and their controls

- **Prompt injection through the ledger.** Ticket text, review comments and incident messages
  flow into tools. Treat every text field as data: the server runs the same intake scan as
  `ticket_intake.py`, stores the flags, and `contract.draft` quotes flagged lines back to a
  human instead of into a prompt.
- **Evidence spoofing.** A local `verify_stamp` is a claim. Only CI evidence with a verified
  OIDC subject counts toward `approved`; the server rejects `source = 'ci:*'` rows without one.
  Verification is the ledger's, not the caller's: the RS256 signature against GitHub's JWKS, the
  issuer, the audience, expiry and the `repository` claim against the node's repo, and the
  verified `sub` becomes `signed_by`. No caller can type a subject in. The CI templates post
  `ci_run` from a separate job that holds `id-token: write`, runs no code from the pull request
  and installs the plugin outside the workspace.
- **Schema churn.** Use expand/migrate/contract on the ledger exactly as the pack's
  `models.md` rule asks of the application.
- **State-machine theatre.** If humans resolve decisions by Slack and the table stays empty,
  the ledger lies. Make `decision.resolve` the only path that unblocks `ready`, and show the
  open-decisions count in the PR template.
- **Cost.** Haiku for everything mechanical, Fable budgeted per epic, `/clear` between
  contracts, and the `run` table on a dashboard from day one.

## 8. Relation to task-tracker verbs

The verbs of a typical agent task tracker (`task.classify`, `status.set`, `decision.propose`) are this
design's `node`, `transition` and `decision` with two additions: evidence-guarded transitions
and the index. The pack's `.claude/work/current.json` is the local projection of one contract
node; when the service exists, `/ticket` writes both and the guard keeps enforcing from the
local file so an outage never blocks an engineer.

## 9. Where to run the ledger: Postgres, Spanner Graph, or AWS

Facts below were read from vendor documentation on 2026-11-02 and adversarially re-checked;
"unverified" marks what the pages did not settle.

| | Postgres (self-managed, Aurora, Cloud SQL, AlloyDB) | Spanner Graph (GCP) | AWS options |
|---|---|---|---|
| Graph query language | None native. `WITH RECURSIVE` with `SEARCH`/`CYCLE` clauses (PG 14+); Apache AGE adds openCypher inside SQL but is not offered on RDS, Aurora, Cloud SQL or AlloyDB (it is on Azure flexible server). SQL/PGQ was committed for PG 19 in March 2026 and reverted in September 2026: do not plan on it. | ISO GQL (based on ISO/IEC 39075:2024 and SQL/PGQ) over a property graph declared on existing tables; `GRAPH_TABLE` embeds graph results in SQL. Quantified paths, `ANY SHORTEST`, `ANY CHEAPEST`. GA 2025-01-30. GoogleSQL dialect only, not the PostgreSQL interface. Needs Enterprise or Enterprise Plus edition. | Neptune Database: Gremlin, openCypher, SPARQL; no GQL. Neptune Analytics: openCypher, in-memory, graph algorithms. Aurora DSQL: no graph. |
| Vector search | pgvector 0.8.x (HNSW, IVFFlat, iterative scans; Cloud SQL ships 0.8.5, Aurora and RDS 0.8.2; AlloyDB adds a ScaNN index). Filtered search needs care: approximate indexes filter after the scan. | Vector index with `APPROX_COSINE_DISTANCE` and friends, plus full-text search, in the same database as the graph. The docs say vector, full-text and graph are integrated; a single query combining all three was not shown on the pages read. | Neptune Analytics: vector index on nodes, `topK.byEmbedding`. Neptune Database: none. Aurora DSQL: no extensions, so no pgvector. Aurora PostgreSQL: pgvector 0.8.2. |
| Distributed, multi-region writes | Aurora Global Database and Cloud SQL replicas are single-writer; Aurora Limitless shards one region. | Native. | Aurora DSQL: active-active multi-region, strong consistency, PostgreSQL wire protocol (GA 2025-05-27). Limits: no extensions, no PL/pgSQL, no triggers, no temp tables, 3,000 rows per write transaction, 5-minute transactions, optimistic concurrency with retries; foreign keys since 2026-08, JSON and JSONB supported. |
| Local development | Docker. | Emulator README lists GQL support; graph algorithms parse but return empty results. | Neptune has no local emulator; DSQL has none. |
| Price floor | Aurora Serverless v2 from 0.5 ACU, or 0 with auto-pause; Cloud SQL and AlloyDB small instances. | Smallest instance 100 processing units: about $90/month on Enterprise (the edition Graph needs), plus storage from about $0.30/GB-month. 90-day free trial includes Graph and vector search. | DSQL: $8 per million DPUs plus $0.33/GB-month, free tier 100k DPUs and 1 GB. Neptune serverless from 1 NCU (price per NCU-hour not on the page). Neptune Analytics billed per m-NCU-hour (no published minimum found). |
| Fit for this ledger | One engine for ledger and index; recursive CTEs cover 1–4 hop traversals over typed edges; the whole team already knows it. | Best single-engine fit if GCP is home and GQL path patterns are the main query shape; the price is dialect lock-in and a smaller tool ecosystem (no pgvector, SQLAlchemy via the Spanner dialect). | No single AWS service does graph + vector + relational. The practical choice is Aurora PostgreSQL for both; add Neptune Analytics only if graph algorithms become a real need. DSQL suits the ledger's small transactions but cannot hold the index. |

**Decision rule.** Pick Spanner Graph when GCP is home and at least one of these holds: you need
multi-region active-active writes; the graph will pass a few hundred thousand nodes with deep
path queries; GQL path patterns are the dominant query shape. Otherwise pick Postgres. For this
ledger the graph is shallow (epic → feature → contract → change → release, plus `governed_by`
edges to rules) and the index matters more than path patterns, so Postgres is the default and
Aurora PostgreSQL Serverless v2 is the AWS form.

**Portability runs one way.** Keep the graph in `node` and `edge` tables as in section 2.1. Spanner
Graph declares its property graph over exactly such tables (`CREATE PROPERTY GRAPH ... NODE TABLES
... EDGE TABLES ...`), so a Postgres ledger moves to Spanner Graph without a data-model change.
A GQL-first design does not move back. If openCypher ergonomics are wanted on Postgres, Apache
AGE stores each label as an ordinary table too, so the same schema works there, self-managed.

**Two AWS corrections to the obvious analogies.** Aurora DSQL is the Spanner-like relational
engine, not a Spanner Graph analogue: it has no graph and no pgvector. Neptune Analytics is the
graph-plus-vector engine, but it is positioned as an in-memory complement to Neptune Database
loaded from snapshots or S3, not as a system of record; the pages read do not describe it as one.

## 10. Beyond the ledger: the graph as system of record

The larger idea: persist intent history, deltas, evidence and codebase graph snapshots so the
graph, not Git, is the primary workflow; then five layers on top: an intent graph that replaces
the branch as the unit of work, a semantic simulator in place of a CI runner, a capability-based
sandbox per change enforced at runtime, continuous verification in shadow mode, and progressive
rollout that flows along dependency edges and routes its own rollback. Security fundamentals
(signing, audit, isolation) attach to intent and behaviour instead of artifacts and pipelines.

### 10.1 What to call the pieces

- The codebase snapshot agents query is a **code property graph** (symbols as nodes: modules,
  functions, classes, routes, tables, env vars, packages; edges: imports, calls, reads/writes,
  exposes, depends-on), **versioned per commit SHA**: a repository's first snapshot is stored
  whole, later ones as deltas against a base, and any snapshot can be reconstructed and
  verified against a fresh extraction.
  Sourcegraph's SCIP, Kythe, Joern's CPG and CodeQL databases are the prior art for the index;
  Bazel and Nx "affected" queries are the prior art for using it.
- The change-with-dependencies idea is **patch theory**: Darcs and Pijul made the patch, with
  explicit dependencies between patches, the unit of work instead of the branch. That is your
  layer one, with intent and preserved contracts added to the patch.
- The whole shape is a **graph-native SDLC** with Git demoted to a projection.

### 10.2 Git as projection, not as nothing

Git is already a content-addressed Merkle DAG of snapshots and the interchange format every
tool, auditor, IDE and signing scheme understands. What it lacks is semantics: intent,
dependencies between changes, evidence, blast radius. So put the graph **above** Git and let
the ledger **materialise** commits and branches deterministically from intent nodes and their
deltas. Agents never run `git`; they create intent nodes and post deltas and evidence, and the
service writes the commit, signs it and opens the PR. Humans and tooling keep the SHA as the
artifact identity. "Git is unnecessary for the primary workflow" is then true for agents and
false for the organisation, which is the version that ships. Do not build a new VCS; Pijul is
the cautionary tale of how long that takes.

### 10.3 The five layers, graded

| Layer | Verdict | What exists today | What is genuinely new | First step |
|---|---|---|---|---|
| 1. Intent graph: change node = intent + delta + preserved contracts + `depends_on` edges | Strongest. Build it. | Patch theory (Darcs, Pijul); stacked diffs; the ledger's `contract` node | Conflicts computed up front from **symbol overlap in the code graph**, not discovered at textual merge; preserved contracts as test and property names the change must keep green | Intent node declares touched symbols; the service refuses two open intents that overlap unless one `depends_on` the other |
| 2. Semantic simulator instead of CI | Overclaimed as stated; strong as "simulate first, execute affected" | Change-impact analysis on dependency graphs; predictive test selection; static properties on a CPG (call graph, taint); contract tests | A **blast-radius estimate** from the code graph plus a learned model of past failures that **orders and prunes** execution | `/verify` runs only the tests and properties reachable from the touched symbols, then the rest; record predicted versus actual failures and measure precision and recall of the estimate |
| 3. Capability-based sandbox per change, enforced at runtime | Real at build time now; feasible at runtime where the runtime supports it | Claude Code's OS sandbox (filesystem and network allowlists); the pack's `allowed_paths`; WASI capabilities and the Wasm component model; Deno permissions; Kubernetes NetworkPolicy, OPA, SPIFFE identities; DB roles | The intent node **declares** capabilities (paths, egress, data, secrets) and the same declaration compiles into the agent's sandbox at build time and into runtime policy at deploy time; **declared versus observed** capability is itself a check | Add `capabilities` to the contract; generate the sandbox config from it; log any access outside it as a finding |
| 4. Continuous verification in shadow mode | Established for reads; hard for writes | Shadow traffic and response diffing (Twitter's Diffy, GitHub's Scientist), property-based checks, contract tests, Keploy-style replay | Intent carries **machine-checkable properties** (the acceptance lines, invariants), checked against live traffic as intended-versus-actual | Shadow the read-only routes first; writes need request-level isolation, so keep pre-merge tests for them |
| 5. Progressive rollout along dependency edges, graph-routed rollback | Feasible where units deploy separately; in a monolith the nodes are feature flags | Argo Rollouts, Flagger, Kayenta canary analysis, feature flags, SLO-based automated rollback | Rollout **order** and rollback **localisation** come from the dependency graph: a misbehaving downstream node points back along edges to the change that introduced them | Build edges from **observed** dependencies (traces, imports) as well as declared ones; a graph built only from declarations is the one that lies during an incident |

Security attaches to nodes naturally: sign intent nodes and evidence rows (Sigstore keyless;
in-toto attestations as the evidence schema; SLSA provenance on the materialised artifact,
which inherits the signature through the materialisation edge); the `transition` table is the
audit trail; layer 3 is the isolation. Nothing here needs inventing; it needs the node to be the
subject of the attestation instead of the tarball.

### 10.4 Two design rules that keep the graph honest

1. **The code graph is derived, never written.** Extractors rebuild it from source per SHA;
   agents read it through `graph.deps`, `graph.impact`, `graph.owners` and may not edit it. The
   intent graph is written, by agents and humans, through typed tools. Mixing the two is how a
   graph starts lying.
2. **Predictions are proposals.** Anything the simulator or the rollout router decides is
   confirmed by execution or observation before it changes state, and its hit rate is a metric on
   the dashboard. The playbook's rule applies to the graph too: deterministic checks gate; models
   propose.

### 10.5 Sequence from the ledger

1. Code graph snapshots per SHA with a read-only MCP API (the pack's route, module and
   dead-code checkers already compute half the edges for Python).
2. Intent nodes declare touched symbols and preserved contracts; overlap detection before work
   starts (layer 1).
3. Impact-ordered verification with predicted-versus-actual tracking (layer 2 in its honest form).
4. Capabilities on the contract compiled into the agent sandbox, later into runtime policy
   (layer 3).
5. Shadow checks on read-only routes using the acceptance properties (layer 4).
6. Graph-routed flag rollout with observed edges (layer 5).

Git stays the projection throughout. Revisit its role only when steps 2–4 have run for a
quarter; by then the branch may indeed be gone for agents, and the SHA will still be the
artifact's name.

## 11. Frontend contracts with Foldkit

Facts read on 2026-11-02 from github.com/foldkit/foldkit and foldkit.dev: MIT, `foldkit@0.170.0`
(2026-10-29), pre-1.0 ("breaking changes may occur in minor releases"), one visible maintainer,
about 1,100 stars; packages `foldkit`, `@foldkit/ui`, `@foldkit/devtools`, `@foldkit/devtools-mcp`,
`@foldkit/vite-plugin`, `@foldkit/oxlint-plugin`, `create-foldkit-app`; a Claude Code plugin
`foldkit-skills` with skills `foldkit`, `generate-program`, `audit-program`.

### 11.1 Why the backend has had the stronger contract

A backend acceptance line is a request and a status code; the pack checks routes, auth, state
and tests with an AST. The frontend has had nothing equivalent: state lives in hooks, side
effects are scattered, "it renders" is the only assertion, and the only contract check is a
regex over `fetch` calls (a frontend call to a route that does not exist is found that way). Foldkit's architecture turns each of
those gaps into something a contract can name:

| Contract element | Backend form | Foldkit form |
|---|---|---|
| Given | seeded rows, a fake clock | `given({ ...model })`: one immutable Model holds all state |
| When | a request | `message(Msg())`: Messages are the only way state changes |
| Then | status, header, row | `model(m => expect(m.x).toBe(y))` and `expect(role('button', { name })).toExist()` |
| Side effects | a store call | `Command.expectExact(Cmd)` / `Command.resolve(Cmd, Msg())`: effects are values, enumerable and assertable |
| API contract | route table vs `fetch` regex | every backend call is a `Command.define` with typed args, so the call set is exact |
| Accessibility | none | scene locators are `role`, `label`, `placeholder`, `text`: a view a scene test cannot reach is a finding |
| Routes | FastAPI decorators | typed bidirectional routes, parse and build |
| Evidence from the running app | logs | DevTools MCP: `foldkit_get_model`, `foldkit_list_messages`, `foldkit_diff_models`, `foldkit_replay_to_keyframe`, `foldkit_dispatch_messages` |

Story tests (`foldkit/story`) are executable acceptance for state; scene tests (`foldkit/scene`)
are executable acceptance for the view, without a browser and without mocks. Both run on
Vitest, so the pack's acceptance grammar covers them unchanged:

```
- $ npx vitest run src/ratelimit/banner.story.test.ts
  => exit 0
- $ npx vitest run src/ratelimit/banner.scene.test.ts
  => exit 0
```

### 11.2 Live acceptance through the DevTools MCP

`@foldkit/devtools-mcp` exposes a running app over stdio: inspect the Model (current or at any
history index), list and filter Messages, diff two Model states, rewind to a keyframe, and
dispatch Schema-validated Messages (dispatch needs the `Message` Schema passed to
`Runtime.makeApplication` under `devTools`). That is the frontend's version of "the command it
ran and its output". Two additions to the pack:

- `acceptance.py` gains a third line kind, `- mcp: foldkit_dispatch_messages [...]` followed by
  `=> model.<path> == <value>`, evaluated by dispatching, then calling `foldkit_get_model` with
  `path`. The evidence row stores the Message history slice and the Model diff
  (`foldkit_diff_models`), which is a better artefact than a screenshot.
- `/verify` on a Foldkit app records `frontend: {stories, scenes, live_acceptance}` in the stamp.

Security, from the package's own notes: the relay is dev-only; a fixed `devToolsMcpPort`
listens on every interface without a token, so it never runs on a shared network; Models and
Messages must not carry secrets (`Redacted` hides, it does not encrypt). Those become two rules
in the adapter below (`fe-devtools` and `fe-secret`).

### 11.3 A `foldkit` frontend adapter for the pack

Foldkit is a frontend inside a Node stack, not a stack of its own, so it is not a
`stacks/foldkit.json`: `/factory:init` detects it from `package.json` and writes a `frontend`
block into `.claude/sdlc.json`, and the checkers live in `lib/frontend_foldkit.py`, run by the
ratchet. The block sets test `npx vitest run`, lint `oxlint` with `@foldkit/oxlint-plugin`
(parsed as JSON; its 37 rules include `no-module-level-mutable-state`,
`no-hardcoded-route-strings`, `no-impure-call-at-decision-time` and
`no-disabling-dev-guardrails`, which are the frontend twins of the pack's module-state and
protected-config rules, so they enter the ratchet as `lint|<file>|<rule>` keys for free),
typecheck `tsc --noEmit`, and five checkers with ratchet keys:

- `fe-story|<file>|<Tag>`: a Message tag in the union with no story test that sends it.
- `fe-scene|<file>|<locator>`: an interactive element reachable only by `text()` (no role or
  label), or a scene file with no `expect(...).toExist()`.
- `fe-api|<METHOD PATH>`: a `Command.define` whose `execute` calls a URL that is not in the
  backend route table (exact, from the code graph's `calls_api` edges; replaces the regex).
- `fe-secret|<file>|<field>`: a Model Schema field named like a credential that is not `Redacted`.
- `fe-devtools|<file>`: DevTools wired into the application with no development check, so the
  relay could reach a production build (the first of the two security rules in §11.2).

The edit guard freezes `*.story.test.ts` and `*.scene.test.ts` like any other test; the reviewer
flags an `update` branch without a story. `tier_high_paths` gains the auth and payment views.

### 11.4 Foldkit symbols in the code graph

The TypeScript extractor (a Node script in the adapter, since the stdlib-only rule is for hooks)
adds kinds `message`, `command`, `submodel`, `view`, `route_fe` and edges `handles` (update →
message), `emits` (command → message), `calls_api` (command → backend route), `renders` (view →
submodel), `routes_to` (route_fe → view). Impact analysis then answers "which stories and scenes
does this change reach" exactly as it answers "which tests" for Python.

### 11.5 In a product UI

Build the epics, contracts and runs pages as Foldkit programs. If the OpenHands React shell is
kept, embed them with `Runtime.embed` and Schema-typed Ports (Foldkit's documented path for
incremental adoption, since it calls a full React rewrite a poor fit); if the thin-UI option
wins, the whole UI is Foldkit. Either way the DevTools MCP gives agents the frontend half of
"verify your own work": dispatch the Messages a story describes against the running
UI and diff the Model. Install `foldkit-skills` from Foldkit's own marketplace next to ours in
the showcase; its `audit-program` skill audits architecture, ours audits the contract, and
`FOLDKIT.md`/`AGENTS.md` from `create-foldkit-app` sit beside the working agreement.

### 11.6 Risks

Pre-1.0 churn (pin the version; vendor the source with `git subtree` as the project recommends);
Effect's learning curve for a team that has never used it; a single visible maintainer; scene
tests run no browser, so keep a handful of Playwright smoke runs for layout; the DevTools relay
is a dev-only privilege, never a CI or production surface.

## 12. The orchestrator: Gas Town's mechanisms on the ledger

Facts read on 2026-11-02 from github.com/gastownhall/gastown (MIT, about 18.7k stars; README,
`docs/design/architecture.md`, `docs/design/polecat-lifecycle-patrol.md`, `docs/HOOKS.md`).
Gas Town is a multi-agent orchestration system for Claude Code and other agents with
"persistent work tracking": a Mayor (coordinator session), polecats (workers with "persistent
identity but ephemeral sessions", one git worktree each), beads (a git- and Dolt-backed issue
ledger), hooks (work on your hook persists across crashes; the propulsion principle says a
session with work on its hook runs it), molecules (workflow templates instantiated as tracked
steps, "each molecule step gets one session"), convoys (batches with completion tracking and
stall detection), a Witness per repo (detects zombie, orphaned and stalled workers; "three
crashes on the same step triggers escalation"; thresholds "intentionally generous" because
"false positives are worse than slow detection"; "the polecat is the authority on its own
state"), a Deacon (cross-repo supervisor with a 3-minute heartbeat), a Scheduler (capacity
governor), a Refinery (a Bors-style merge queue with verification gates; "polecats never push
directly to main"), mail and escalation with P0–P2 severities. One principle matters most for
us: "the Witness observes but does NOT gate completion"; completion is gated by verification,
not by the supervisor.

### 12.1 The mapping

| Gas Town | The orchestrator (factory plugin) |
|---|---|
| Beads: the ledger of work | The ledger (§2.1): nodes, transitions, evidence, decisions, runs. Nothing else is state. |
| Hook: work that persists in the worktree | `.claude/work/current.json` in the contract's worktree, written from the ledger, plus the ledger row itself. A crashed session loses nothing. |
| Propulsion principle, `gt prime --hook` at SessionStart | A SessionStart hook (`sdlc-graph prime`) prints the contract, its state and `workflow_next`; the session's first action is that step. There are no idle sessions and no "what should I do" prompts. |
| Molecule: a template instantiated as tracked steps, one session per step | `state_machines.json` is the formula; each transition is a step; each step is a fresh `claude -p` session with the pack loaded. Context never fills because the ledger, not the context window, carries the work between steps. |
| Polecat: persistent identity, ephemeral sessions, own worktree | An executor: one worktree per contract (`<repo>-<slug>`), one run row per step carrying the model that ran; the orchestrator records transitions as the fixed actor `agent:orchestrator`, because it, not the step's model, checked the evidence. |
| Witness patrol: zombies, orphans, stalls; 30-minute no-progress threshold; 3 crashes on a step → escalate; never nuke a dirty sandbox | The watchdog tick: a run with a dead process or no new evidence/transition for `stall_minutes` (default 30) is retried with a fresh session on the same step; the third failure on one step creates a P1 decision for a human; a worktree with uncommitted changes is never deleted, it is escalated. |
| Deacon heartbeat (3 minutes) and the Scheduler | A deterministic daemon loop (`sdlc-graph orchestrate`), tick every 60 s, `max_concurrent_runs`, a per-session budget passed to every step and a per-feature cost cap (a per-model budget is deferred until the `run` table shows it is needed); restartable because its only state is the ledger and a per-feature lock row. |
| Convoy: a batch tracked to completion | The feature node: its `decomposes_into` contracts are the convoy; the goal loop ends when every contract is `merged` (or `in_review` with a PR, in the default mode) or a human has explicitly skipped one. `sdlc-graph feed` is `gt feed`. |
| Refinery: merge queue, gates, never push to main | The PR stage: `/ship` opens a draft PR per contract; CI posts binding evidence; `approved` needs a non-author code owner. Merging stays human by default; a `refinery` mode merges only after branch protection's required approvals exist, which GitHub enforces regardless. No bisecting queue at our scale. |
| Mail and escalation severities | Decision rows with `severity` and a `notify` adapter (stdout now, Slack later). |
| Seance: query predecessor sessions | The previous run's `summary` and `last_assistant_message` stored on the run row and injected into the next step's prompt. Fresh sessions, remembered outcomes. |
| Settings injected with `--settings` so "customer repos stay clean" | Executor sessions get `--settings <generated>` with the pack's hooks, the contract's compiled capabilities (§2.1 `capabilities`) and `--plugin-dir`; the customer repo carries only `.claude/sdlc.json` and the rules. |

### 12.2 The goal loop

```
orchestrate(feature):
  acquire lock(feature)                                   # ledger row; a second daemon exits
  until feature is done or blocked:
    for contract in topo_order(feature.contracts):         # depends_on edges
      step = workflow_next(contract)                       # from the ledger, never guessed
      if step.needs_human: ensure decision row; continue   # the loop never answers for a human
      if step.running: patrol(step); continue              # stall / crash / drift checks
      if capacity and budget allow: dispatch(step)         # fresh claude -p session in the worktree
    sleep(tick)
  if all contracts in_review|merged: stop and report       # a human merges and moves the feature to released
```

`dispatch` runs `run_headless.sh --plugin-dir` for the one slash command the step maps to
(`/factory:tests`, `:implement`, `:verify`, `:ship`), with `--max-turns`, the budget flag,
`--permission-mode dontAsk`, the generated settings, and a prompt that is the contract plus the
previous run's summary. When the session ends, the orchestrator does not trust the session's
word: it checks that the step's expected evidence kind now exists (`tests_frozen`,
`verify_stamp`, `pr_url`), that `git diff --name-only` against the step's start commit is a
subset of `allowed_paths` (the drift gate: a violation resets the worktree to the start commit
and retries with the violation in the prompt), that frozen tests are unchanged, and that the
ratchet is clean. Only then does it call `contract_transition`. The step is idempotent: a retry
starts from the same ledger state and the same commit.

### 12.3 Where Claude Code's harness does the work

- **Every step is a Claude Code session** with the pack loaded: the edit guard, the Bash guard,
  the post-edit ratchet and the stop hook run inside it. The orchestrator never edits code.
- **Judgment is a bounded session, not the loop.** When a step has failed twice, the daemon
  calls one session on the orchestrator tier (`claude -p --output-format json --max-turns 6`)
  with the ledger state and a strict schema `{choice, reason, escalate}`: retry differently,
  explain, or escalate. That is Gas Town's Mayor, reduced to a function with a schema. The same
  call for "several next steps are allowed" and "reviewers disagree" is not wired yet: today the
  topological order settles the first and the conformance gate's escalation the second.
- **The Agent-spawn hook (§4) runs inside every step**, so subagents a step spawns keep to the
  routing table and the Fable budget.
- **Conformance is its own gate.** After `implement`, a Sonnet reviewer compares the diff to the
  contract's acceptance criteria and emits `{score, findings}` as `conformance` evidence; below
  the threshold the loop re-enters `implement` with the findings, at most twice, then escalates.
  This is the "building according to the contract" check, separate from tests passing.

### 12.4 What Gas Town does that we deliberately do not

No Dolt, no tmux, no mail system, no federation: the ledger is Postgres and the sessions are
processes. No auto-merge by default: the playbook's rule stands. No multi-repo town in
increment 4: one repo per feature. And no mechanical "stuck" verdict without a retry first,
which is Gas Town's own advice.

### 12.5 Where the executor's Claude comes from (verified 2026-11-02)

The executor is always Claude Code (`claude -p`, never `--bare`, with `--plugin-dir` and
`--settings` passed explicitly). What differs is the credential behind it. The orchestrator
runs in one of the modes below (`--executor local|runner`), refuses to start when the credential
it finds does not fit the mode, and records the credential itself on every run row as
`auth_source` (`subscription`, `oauth_token`, `api_key`, `claude_platform_aws` or `bedrock`), so
the mode is configuration and the run row says what actually paid for the step:

| Mode | Credential | Who it suits | Facts |
|---|---|---|---|
| `local` (default, and the showcase) | the engineer's own Claude Code login on their machine: Pro, Max, Ultra, Team or Enterprise seat | a team that already holds licences; the ledger coordinates, each machine executes its own steps | a non-bare `-p` run uses the keychain login; `--bare` "doesn't use your subscription login" and needs `ANTHROPIC_API_KEY`. Tonight's canary, headless feature run and orchestrator smoke all ran this way on this Mac. Subscription usage windows apply, so `max_concurrent_runs` and `--max-turns` stay small |
| `runner` with a subscription token | `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` (Pro, Max, Ultra, Team, Enterprise) | one person's own always-on runner or their GitHub Action | "an OAuth token is tied to the subscription of the person who ran `claude setup-token`"; "runs use your Claude subscription instead of API billing"; for a secret shared across repositories the docs say to use an API key instead |
| `runner` with an API key or federation | `ANTHROPIC_API_KEY`, or GitHub OIDC exchanged for a Claude Console service account (`anthropic_federation_rule_id`), or Claude Platform on AWS (IAM, SigV4, AWS-managed key) | a shared team runner, CI, the product's executor containers | no static secret with federation; on AWS the key is minted from IAM credentials |
| `managed` (later; not an executor mode yet) | API key only (SigV4 on AWS) | long-running sessions in Anthropic's sandbox | Managed Agents is beta, needs "a Claude API key from the Claude Console", bills as API usage, has no subscription login, and is not ZDR or HIPAA eligible |

So the honest answer to "can a team's subscriptions run this?" is: locally, yes, today,
and that is the mode the showcase uses; on a shared runner, one person's token is allowed but
is that person's subscription, so a team runner uses an API key, federation or Claude Platform
on AWS; Managed Agents cannot use a subscription at all. Two unattended-run details from the
same docs: `--permission-prompts none` (v2.1.259+) makes a run deny, rather than wait on, any
prompt nobody can answer, and a `-p` run executes a folder's hooks and `.mcp.json` servers
without a trust dialog, so executors run only in worktrees the orchestrator created. One
forward-looking caveat: the docs say `--bare` "will become the default for `-p` in a future
release"; pin the Claude Code version in the runbook and keep every flag explicit.

## 13. The factory loop

Everything above is one machine, and the factory plugin runs that machine over a brownfield
repository end to end. One command per stage, each leaving a file in git and a row in the
ledger:

| Stage | Command | What it produces | Gate before the next stage |
|---|---|---|---|
| Intake | `/factory:init` | the working agreement, the baseline of today's debt, the code graph, the audit, `docs/onboarding-report.md` | none; nothing is changed |
| Measure | `/factory:assess` (`/factory:maturity` + `:security`) | `docs/maturity-assessment.md`, `docs/security-assessment.md`: levels with evidence, findings with blast radius and characteristics | the human picks A/B/C |
| Plan | `/factory:epic` from the assessment | the epic (outcome, riskiest assumption, metrics, guardrail), features in walking-skeleton order, agent-ready stories with decisions | decisions resolved by a human |
| Contract | `/factory:ticket` | `contract.md` with acceptance lines, allowed paths from the graph's impact, tier; ledger node `drafted → ready` | decisions resolved, acceptance present |
| Tests | `/factory:tests` | failing tests, frozen; evidence `tests_failing_first`, `tests_frozen` | the tests fail for the right reason |
| Build | `/factory:implement` | the change inside the allowed paths; guards and ratchet running | drift gate, frozen tests unchanged |
| Verify | `/factory:verify` | the stamp: tests, ratchet, acceptance, two reviewers; evidence `verify_stamp` | binding only from CI |
| Ship | `/factory:ship` | the PR with its evidence section; evidence `pr_url`; `in_review` | non-author code owner, CI green |
| Loop | the orchestrator (`sdlc-graph orchestrate <feature>`) | the next contract of the feature, the next feature of the epic, until the epic's metrics are measured | a human at every decision and at every merge |

The factory's output is not code; it is evidence that the code does what the contract said,
produced by a process that cannot skip a gate. That is what makes it a factory rather than a
faster developer. A first loop can run on a single ticket; the security epic
from `/factory:assess` is a second loop, which the orchestrator can carry while the humans
watch the feed.

## 14. Autonomy policy: ask before, never during

The factory's rule is that every question is asked when the epic, feature and story are
written, and execution runs on contracts with nothing left to ask. Three mechanisms make the
rule enforceable rather than hopeful.

**The contract carries the answers.** A story is not `ready` while a decision is open (the
ledger guard `decisions_resolved`), so execution can never meet one. For the long tail that no
decision list anticipates, the contract carries `## Defaults when ambiguous`: a short policy
the agent applies instead of asking (smallest change that satisfies the acceptance; follow the
nearest existing pattern; prefer the option the contract's decisions imply; when none applies,
choose the reversible option), and every applied default is recorded as an **assumption**
(`.claude/work/<slug>/assumptions.md`, evidence kind `assumptions`) that the reviewer and the
conformance gate read. An assumption that contradicts a decision fails conformance. The contract
also carries `## Capabilities` (paths, commands, egress, data) from which the executor's
permissions are compiled, so "the agent needed something it did not have" is a contract defect
found at verification, not a prompt at runtime.

**Execution never prompts.** The executor runs with `--permission-mode dontAsk` and
`--permission-prompts none`, so anything outside its allow-list is denied and reported, never
waited on. The allow-list is the union of the contract's compiled capabilities, the commands
the pack's skills run (every script and git form a skill names), and the stack's test, lint
and format commands. `scripts/check_skill_permissions.py` proves statically that each Bash
command a skill names matches a rule in the project settings or the skill's `allowed-tools`,
and CI runs it, so a new skill step cannot introduce a prompt. Writes under `.claude/` go
through the staging directory because Claude Code itself refuses them unattended (§12 and
the memory of the first live run). A denial that still happens turns the step into an escalation
with the exact missing rule named. The orchestrator recognises three shapes and no others: Claude
Code's own denial (the `permission_denied` system event, the result's `permission_denials`, a tool
result that begins "Permission to use X has been denied"), a PreToolUse hook block in the stream
("hook blocking error ... Blocked: ..."), and every record the pack's guards wrote to `blocks.jsonl`
since the run started; the last two carry the reason `guard_refused` and name the guard's rule. A shell
"Permission denied" or a test that prints the words is not a denial. Two denials name no missing rule and
are recorded on the run instead of escalated: a Bash command refused only for its shell shape (a chain, a
loop, a heredoc, a redirection inside the worktree, with no part forbidden on its own) and the commit guard's
message rules. The session redoes the call in an allowed form and the gates judge the step on its evidence. The executor's list never holds a
blanket runner (`uv run*`, `make *`): a runner runs anything, and `guard_bash` does not see through it.

**Questions move to the front.** `/feature` and the orchestrator refuse to start a contract
with open decisions or without defaults, instead of stopping midway. `/ticket` asks its
questions in one batch, with a recommendation each, before the contract is written. The
`/factory:epic`'s agent-ready stories list decisions with their values and the defaults block, and
`lint_epic.py --agent` marks a story Ready only when every decision has a real value (one of its
options, never "see above" or "ask product") that a named person decided; `acceptance.py --ready` applies
the same rule to the contract, and refuses an unconfirmed decision at tier medium or high or when it touches
money, security, data or customer-visible behaviour, so a headless `/ticket` can settle low-risk questions
alone and nothing else. The defaults block carries the standard four and no line that asks, stops or
overrides a decision (the ledger guard `defaults_present` checks the same). Every assumption names the
decision it relies on (`decision: #12`, `D1` or `none`); one that cites a decision without applying its
choice, or applies another option of it, fails conformance, and a model reads every step's assumptions with
the decisions inline. The orchestrator re-runs the stack's test command itself (`commands.test`, or
`--test-command`) and refuses `tests` and `verify` when there is none, because a verify stamp or
failing-first evidence the session wrote itself is not proof. What remains
human during execution is exactly the playbook's gates: a decision the contract did not
anticipate (an escalation, P1), code-owner approval, and the production gate.

## 15. The console: what the ledger already knows, as pages

The OpenHands Enterprise console (organisation switcher, Dashboard, Budgets, Members, Language
Model, Condenser, Verification, Billing & Credits, Organization; personal Agent, API Keys,
Secrets, MCP, Integrations, Skills; account budget) is the shape of an admin surface for a
product like this. Its organisation half lives in `OpenHands/enterprise` under the PolyForm
Free Trial licence, so it is not reusable; the MIT Canvas covers the conversation view and the
personal settings. The ledger and the pack already hold the data behind every page of the
organisation half, which makes the console a projection, not a new system:

| Console page | Backed by |
|---|---|
| Dashboard | `sdlc-graph feed`, `metrics snapshot` (nodes by state, median hours per stage, first-pass merge, repeat incidents) |
| Budgets, account budget | `run` rows (tokens, cost, executor, credential), the Fable allowance per epic, per-contract `--max-budget-usd`, a platform allocation by WorkOS org |
| Members, Organization | WorkOS Directory Sync, RBAC roles mapped to the playbook's roles, `org_id` on every table |
| Language Model | `routing_policy.json`: the tier per stage, the aliases pinned by managed settings, drift from `requested_model` versus `resolved_model` |
| Condenser | the orchestrator's one-session-per-step rule and the compact instructions; nothing to configure yet |
| Verification | the evidence gates: which kinds are binding, the CI OIDC subject, conformance threshold, frozen-test and drift rules |
| Billing & Credits | the inference channel per org (Claude Platform on AWS by CCU, Bedrock, Anthropic direct) and the managed-hours block |
| Agent | the agent definitions shipped by the pack (test-author, reviewer, security-reviewer) and their models |
| API Keys, Secrets | WorkOS Vault or AWS Secrets Manager; the pack never stores one |
| MCP | the managed MCP policy (`docs/mcp-policy.md`): the ledger's own server and the allow-listed ones |
| Integrations | the GitHub App, the CI evidence webhook, the notify adapter |
| Skills | the marketplace plugins installed per org, with versions, and the skill-permission check's result |

Build order: the read-only ledger board first (epics, features, contracts with state, evidence,
decisions, runs), as Foldkit programs (§11) over the MCP's read-only tools; then Verification
and Language Model as the first two writable pages, because their settings are already files in
the repo (`state_machines.json`, `routing_policy.json`) and a page that edits a versioned file
through a PR is the playbook's own rule. Members, Budgets and Billing wait on two decisions
this note does not make: the identity provider (WorkOS is the working assumption) and the
inference channel per organisation.

## 16. The lifecycle: idea to done, with stories, decision records and findings

Sections 1 to 15 run one path well: a ticket becomes a contract, the contract becomes merged code
with evidence. Around that path the factory is thin. An idea has nowhere to live before it is an
epic; a story is fused into its contract; a decision that outlives one story has no home; a
finding from a reviewer or a scan is printed and forgotten; and "done" means merged, not "the
outcome the brief promised was measured". This section adds those pieces without adding a second
system. It keeps the two rules every earlier section keeps: **artifacts are files in git and the
ledger holds their state, path and commit SHA, never a copy**; and **models propose, humans
decide, deterministic checks gate**.

### 16.1 Seven stages, computed, never stored

| Stage | Command | Artifact in the repo | Ledger states it spans | Gate to leave | Who decides |
|---|---|---|---|---|---|
| **Ideate** | `/factory:ideate` | `docs/ideas/<slug>.md` | idea `captured → shortlisted → promoted` | not a duplicate; a problem statement; an owner | product owner |
| **Brief** | `/factory:brief` (was `/factory:epic`) | `docs/epics/<slug>.md`, product decision records | epic `drafted → accepted` | riskiest assumption resolved, guardrail metric, PDRs accepted | product owner |
| **Design** | `/factory:design` (was `/factory:ticket` and `/factory:contract`) | features and stories as sections of the brief's `docs/epics/<slug>.md`, ADRs and DDRs, the feature design doc, contracts | feature `proposed → ready`; story `drafted → ready`; contract `drafted → ready` | every decision resolved by a named human; records accepted; design doc reviewed when required | product owner, tech lead, data owner |
| **Build** | `/factory:tests`, `:implement`, `:verify`, or the orchestrator | failing tests, the diff, the verify stamp | contract `ready → … → verified`; story `ready → building` | the gates of §12.2 | deterministic |
| **Review** | `/factory:ship`, `/factory:review-pr` | the PR with its evidence | contract `verified → in_review → approved → merged`; story `building → built` | non-author code owner, binding CI | code owner |
| **Accept** | `/factory:deploy` (human), `/factory:accept` | the release, the acceptance sign-off | release `requested → … → verified`; story `built → accepted`; feature `building → released` | ready poll and smoke; the product owner accepts each story's criteria in the released system | release manager, product owner |
| **Done** | metrics snapshot | the closing note on the brief | feature `released → evaluated`; epic `measured → closed` | the metrics measured against the hypothesis, kept or killed | product owner |

The stage is a **projection**: `sdlc_graph/ledger/lifecycle.json` maps each stage to the (kind,
state) pairs above, and `lifecycle_view(slug)` computes an item's stage from the ledger. There is
no stage column, because a stored stage drifts from the per-kind states the first time a human
moves one by hand.

An epic's children sit in several stages at once, so a parent's stage needs a rule. The board
shows the **least advanced open child's stage with a progress count** ("Build · 3/5 contracts
merged"), so a straggler is visible and momentum is not hidden. Killed work is a successful exit:
`parked`, `rejected` and "hypothesis failed" all count as Done, and the funnel metric
(ideas → briefs → shipped → outcome kept) is how the team sees whether it kills early enough.

### 16.2 Stories: the unit of acceptance, separate from the unit of work

| Layer | Answers | Typical size | Owner | Done when |
|---|---|---|---|---|
| Epic | why: the outcome and the hypothesis | a quarter | product | metrics measured |
| Feature | which capability | weeks | product and tech lead | released and evaluated |
| **Story** | what one user can now do: the unit of **acceptance** | days | product owner | its criteria accepted |
| **Contract** | how it is built: the unit of **work** (the work package) | hours to a day | engineer, executed by an agent | merged |

A story is a vertical slice, independent, small and testable, written in product's language
with acceptance criteria. A contract is engineering's executable form of all or part of a story.
Today the two are fused: `story` is a node kind with no state machine, and `/ticket` turns one
story into one contract. That works while the ratio is one to one and breaks when a story needs
an expand/migrate/contract sequence, a backend then a frontend, or a risky part split off for
review. So the story becomes a node with its own machine:

- `story`: drafted → ready → building → built → accepted (or `split`, which supersedes it with
  smaller stories, or `rejected`). `ready` needs acceptance criteria, a decision owner and its
  decisions resolved; `building` needs at least one contract (`decomposes_into`); `built` needs
  every contract `merged` (deterministic, so the orchestrator may record it); `accepted` needs a
  human actor and `acceptance_signoff` evidence naming the criteria checked in the released
  system.
- Contracts link to their story with `decomposes_into`, and the story's acceptance criteria are
  the source of each contract's acceptance lines. A contract may cover part of a story; the
  union of a story's contracts must cover all of its criteria (`acceptance_covered`, lifted from
  the contract to the story).
- **Where a story lives: the content in a file, the state in the ledger.** A story is a section
  of its brief's file, `docs/epics/<slug>.md`, where `/factory:brief` already writes the
  agent-ready stories, under a stable id (`### S3 · Reset after the window`). No story directory
  and no story text in the database: a product owner changes a criterion through a pull request,
  and a reviewer sees it in the diff. The story node holds the state, owner, edges and
  `touched_symbols`, with `path = docs/epics/<slug>.md#S3` and the commit SHA. It keeps only what
  the guards need of the content: each criterion's id and a hash of its text. A drift check,
  the mechanism the frozen tests use, fails when the file's criteria no longer match the
  ledger's, so a changed story is re-confirmed (back to `drafted` until its owner readies it
  again) instead of drifting silently. Briefs stay in `docs/epics/`, and ideas sit beside them in
  `docs/ideas/`.
- In the common one-to-one case, `/factory:design` writes the story and its contract together,
  so the extra node costs nothing.
- `feature: building → released` additionally needs every story `built`; `released → evaluated`
  needs every story `accepted` (or explicitly `rejected` by a human) and the metrics snapshot.

### 16.3 Decision rows and decision records

Two kinds of decision exist, and both are needed:

| | Decision row (exists, §2.1) | Decision record (new) |
|---|---|---|
| Example | "Rate-limit window: 60 s or 300 s?" | "All rate limiting uses Redis" |
| Scope | one story or contract | crosses features; long-lived |
| Form | a ledger row: question, options, choice | a short numbered file in the repo |
| Lifecycle | resolved once | proposed → accepted → superseded (or rejected) |
| Changes | — | **never edited once accepted**; a new record supersedes it |

Three record types, each with its owner and its questions:

- **PDR, product decision record** (`docs/decisions/product/NNNN-<slug>.md`): problem, scope in
  and out, options, decision, what customers will see. Owner: the product owner.
- **ADR, architecture decision record** (`docs/decisions/architecture/NNNN-<slug>.md`, Nygard's
  form): context, options, decision, consequences. Owner: the tech lead, or the code owner of the
  paths it governs.
- **DDR, data decision record** (`docs/decisions/data/NNNN-<slug>.md`): the data, its
  classification (PII, financial, secret), retention, owner, residency, and the
  expand/migrate/contract plan. Owner: the data owner.

A record is a `decision` node (`attrs.record_type` = `pdr`, `adr` or `ddr`) with the file's path
and commit SHA. Items it shapes carry a `governed_by` edge to it, and a replacement carries a
`supersedes` edge to the old one; both relations already exist in §2.1. An agent may draft a
record (`decision_propose` with a record type); only the named owner may accept it, through the
same human-only path as `decision.resolve`. Once accepted, the edit guard freezes the file the way
it freezes tests, so "never edited once accepted" is enforced, not hoped. `intake_context`
returns the accepted records that govern a contract's paths, exactly as it returns rules (§2.2
use 3), so a new contract inherits its constraints without anyone remembering them.

**When a decision row becomes a record.** Promote it when it is hard to reverse, when it affects
more than one feature, or when it touches money, security, data or customer-visible behaviour
(the same sensitive list `acceptance.py --ready` already uses). Everything else stays a row. The
rule lives in one function (`ledger/records.py: should_promote`) because it sets how much
paperwork the factory makes: too eager and every story carries an ADR nobody reads; too lax and
the decisions that matter stay buried in contracts.

### 16.4 The feature design document

A single large software design document written up front goes stale in weeks and breaks "no
second copy". The factory writes one **per feature**, an RFC in the style of a design doc, at
`docs/design/<feature>.md`:

1. **Context**: links to the brief, the stories it covers and the records that govern it.
   Links, never copies.
2. **Design**: components, data flow, interfaces; the code graph's impact for the paths it
   names.
3. **Data**: what the DDRs decided and how this feature applies it.
4. **Work breakdown**: the contracts, each with its story, `depends_on`, allowed paths, tier and
   the records that govern it. This is the table `design_decompose` turns into **proposed**
   contract nodes, as `epic_decompose` proposes features; a human accepts each.
5. **Risks and rollout**: what could go wrong, the flag or migration order, and the rollback.

It is required when a feature has more than one contract, any contract at tier high, or any
promoted record; otherwise the contract is the design and no document is written. When it is
required, `feature: proposed → ready` needs `design_review` evidence: the document merged
through a PR that a code owner approved. So the flow reads:

```
Brief:   epic ──► PDRs                                  (why; what is in and out)
Design:  feature ──► stories                            (what; product-owned criteria)
                 ──► ADRs / DDRs, only where promoted   (the hard-to-reverse how)
                 ──► feature design doc                 (how, when required)
                        work breakdown ──► contracts    (the work packages, executable)
Build…:  each contract through §12's pipeline; each story accepted in Accept
```

Stories say **what**, the design document says **how**, contracts are what runs.

### 16.5 Findings: from noticed to fixed to never again

The factory already produces findings and keeps none of them: reviewer and security-reviewer
output, `/factory:assess`, a ratchet key that is new, `capability_findings.jsonl`, a failed CI
check, an incident's root cause. A **finding** is a defect someone or something noticed; an
**incident** is harm that happened. They get separate kinds and meet through `caused_by`.

- `finding`: open → triaged → accepted → fixing → fixed → verified → closed, with three exits
  from `triaged`: `duplicate` (with a `supersedes` edge to the original), `false_positive`, and
  `risk_accepted`, which needs a resolved decision row naming an owner and an expiry date and
  reopens the finding when the date passes.
- **One queue for every source**, each source an adapter that posts `finding` nodes with a
  **fingerprint**. Ratchet keys (`route-auth|GET /admin`, `fe-api|GET /x`) already are
  fingerprints; reviewer and assessment findings get `<rule>|<path>|<symbol>`. The same
  fingerprint is the same finding: it is reopened or counted, never filed twice.
- **Severity times likelihood**, with a CVSS score for security findings, and a response time
  per severity (defaults: critical 7 days, high 30, medium 90, low next debt epic) shown by
  `metrics_snapshot` as "findings past their response time".
- **Triage**: the utility tier proposes severity, owner and duplicates; a human confirms through
  a decision row. A model never closes, accepts the risk of, or dismisses a finding.
- **Fix**: an accepted finding is the input to `/factory:design`, and many low findings in one
  area become one contract under a standing debt epic, not one PR each.
- **Verified** needs two things: the fix's regression test, and the fingerprint absent from the
  next run of the source that raised it. A fix the scanner still reports is not fixed.
- **Never again**: a second finding of the same class takes the incident-to-intent path (§2.2
  use 4): the outcome is a rule or a checker, so the class is caught at authoring time next.
- `external_id` links a finding to Jira or Linear when the team triages there; the ledger keeps
  the evidence and the tracker keeps the conversation.

### 16.6 Ideas

An idea is cheap, unvetted and usually discarded, so it is not an epic: filing every idea as an
epic floods the board and makes intent survival count every discarded idea as a lost epic.
`idea`: captured → shortlisted → promoted (a `promoted_to` edge to the epic it became), or
`parked`, or `rejected`. Sources are a person, an assessment, a cluster of findings or an
incident. `/factory:ideate` dedupes against open ideas and epics through the index, asks for the
problem (not the solution), the person who has it and the evidence that they do, and writes
`docs/ideas/<slug>.md`. `/factory:brief` promotes a shortlisted idea into an epic.

### 16.7 Commands after the rename

The commands are named after the stages, and the old names go in one change (no aliases):

| Stage | Commands |
|---|---|
| Intake and measure | `/factory:init`, `/factory:assess` (`:maturity`, `:security`) |
| Ideate | `/factory:ideate` |
| Brief | `/factory:brief` (was `/factory:epic`) |
| Design | `/factory:design` (was `/factory:ticket` and `/factory:contract`): one story, or a whole feature with its records, design document and work breakdown |
| Build | `/factory:tests`, `/factory:implement`, `/factory:verify`, `/factory:orchestrate` |
| Review | `/factory:ship`, `/factory:review-pr` |
| Accept | `/factory:deploy` (human), `/factory:accept`, `/factory:rollback` |
| Done | `/factory:feed`, the metrics snapshot |
| Any stage | `/factory:findings` (the triage queue), `/factory:next` |

### 16.8 What not to build

- A stage column, or a second state machine over the existing ones. The lifecycle is a view.
- A single up-front design document, or records and design documents copied into the database.
- A tracker. Jira or Linear stays the conversation if the team has one; `external_id` links.
- Any model that accepts a record, a story, a risk or a finding's dismissal.
- Mandatory paperwork for small work: a single low-tier contract needs no design document and
  usually no record.

### 16.9 The graph in lock step

Every piece above is a node or an edge, so the graph is not a consumer of this section; it is
where the section lives. Two halves, one bridge: the **intent graph** (ledger `node` and `edge`,
written through guarded tools) and the **code graph** (`sym` and `sym_edge` per SHA, derived,
never written, §10.4), joined by **symbol keys and paths**. Five rules keep them tight:

1. **One registry, enforced by a test.** `sdlc_graph/shapes.py` already lists both halves' kinds
   and relations (`SYM_KINDS`, `SYM_RELS`, `NODE_KINDS`, `NODE_RELS`). It gains the evidence kinds
   and, for every kind, the places that must know it (the `node` and `edge` CHECK constraints, a state
   machine in `state_machines.json`, a stage in `lifecycle.json`, chunk sections in the index,
   node and edge tables in the Spanner property graph, the board). A conformance test reads every
   one of those and fails when a kind is in one and missing from another. Adding `finding` and
   forgetting the Spanner label, or the index sections, is a red test, not a silent gap.
2. **Every intent node that is about code names its symbols.** `node.touched_symbols` (§10.3
   layer 1) holds the symbol keys for a contract today. Stories carry the union of their
   contracts'; findings carry the symbols they were raised on; records carry the symbols their
   `paths:` resolve to at acceptance. So one GIN-indexed overlap query answers all of: which
   in-flight contracts collide (the existing refusal), which open findings sit on the code a
   contract will touch, which accepted records govern it, and which stories a change could break.
3. **The code graph drives the design, never the reverse.** The feature design document's work
   breakdown takes each contract's allowed paths from `graph_impact`, and its `depends_on` edges
   from symbol overlap between the proposed contracts: two contracts that touch the same symbol
   must depend on one another or merge (the layer-1 rule, applied before work starts instead of
   at the second contract's intake). `design_decompose` refuses a breakdown that leaves an overlap
   unordered.
4. **Symbols are re-resolved per SHA, never trusted across one.** A finding or record names
   symbols at a commit; at each new snapshot the keys are re-resolved (a renamed symbol is
   followed by path and qualified name; a vanished one is flagged). Verification of a finding is a
   code-graph fact: at the fix's merge SHA the fingerprint's checker is clean or its symbol is
   gone. A flagged, vanished symbol goes back to triage, never to `closed` on its own, because
   "the code moved" is not "the defect is fixed".
5. **One traversal, two dialects.** The lifecycle view, the parent-stage rule and the funnel are
   recursive CTEs over `decomposes_into`, `promoted_to`, `governed_by` and `caused_by` in
   Postgres, with the same queries in `spanner/queries.gql` over the property graph (§9: the
   tables are the graph). Both run in the test suite against the same fixture, so portability is
   checked, not claimed.

The tools follow: `graph_impact(paths)` returns, besides symbols and tests, the open findings,
governing records and in-flight stories on those symbols; `node_neighbours` walks the new
relations; `intake_context` indexes ideas, stories, records and findings so dedupe works for every
kind; and the orchestrator orders stories, not only contracts, by `depends_on`.

### 16.10 Build sequence

Each increment ships on its own, with its tests, and leaves the factory working. The graph is not
an increment of its own: each increment that adds a kind, an edge or a machine adds it to the
registry, the Spanner graph, the index and the board in the same change, and the conformance
test of §16.9 rule 1 holds every one of them to that. The order puts
the rename first, so everything after it is written once under the new names, and the schema
second, because every later increment needs it.

The detailed, file-level plan is in `plugins/factory/docs/plans/lifecycle.md`; the increments are:

1. **Rename** `epic` → `brief`, `ticket` and `contract` → `design`, everywhere at once.
2. **Registry, schema and lifecycle**: the conformance test of §16.9 first, green on today's
   kinds; then the new kinds, edges, machines and evidence kinds through it; `lifecycle.json`,
   `lifecycle_view` in both dialects.
3. **Stories**: the story machine wired into `/factory:design`, the orchestrator and
   `/factory:accept`.
4. **Decision records**: templates, `should_promote`, proposing and accepting records, the frozen
   accepted file, governing records in `intake_context`.
5. **Feature design document**: the template, the required-when rule, `design_decompose`, the
   `design_review` guard.
6. **Findings**: the node, the source adapters, fingerprints, `/factory:findings`, response times,
   verification by fingerprint.
7. **Ideas**: the node, `/factory:ideate`, promotion from `/factory:brief`.
8. **The board**: `sdlc-graph board` and the funnel metrics over the lifecycle view.
9. **Review lenses** (§16.11): `/code-review` in verify, review-pr, CI and assess, its findings
   through increment 6's queue and the triage gate on `approved`. It needs increment 6; the two
   headless facts are checked first.

### 16.11 Review lenses: Claude Code's `/code-review` inside the Review stage

The pack's own `reviewer` and `security-reviewer` agents stay, and Claude Code's built-in
`/code-review` joins them as a lens, so the factory gains the platform's reviewer as it improves
instead of maintaining a parallel prompt.

| Moment | Runs | Level |
|---|---|---|
| `/factory:verify`, before the PR | `/code-review` on the contract's diff, beside the two agents | by tier: trivial and low → `low`; medium → `medium`; high, or any file under `tier_high_paths` → `high` |
| `/factory:review-pr` and the CI review job | `/code-review <PR#>` | the same mapping |
| `/factory:assess` on a brownfield repository | `/code-review high <path>` over the tier-high paths, the judgment pass beside `security_assess.py`'s deterministic scan | `high` |
| Tier-high changes | `ultra` (the multi-agent cloud review) is **offered** in the decisions pack and the ship summary | never run automatically: it is billed and user-triggered |

Five rules keep it inside the design:

1. **Findings go to the ledger.** Each becomes a `finding` (§16.5) with source `code-review` and
   fingerprint `<category>|<path>|<symbol>`; the verify stamp counts them by severity and the
   change explainer (§17.4) shows them.
2. **Findings never approve or block alone; triage gates.** A contract cannot reach `approved`
   while a critical or high `code-review` finding is untriaged. A human marks each one accepted
   (fix it), a false positive, or a risk accepted with an owner and expiry.
3. **No `--fix` inside review.** A reviewer that edits the code is the author reviewing itself.
   Accepted findings send the contract back to `implement` with the findings in the prompt, the
   conformance gate's loop (§12.3): at most twice, then an escalation.
4. **Independence.** The review runs in a fresh subagent or session, never in the implementer's
   context, and records a `run` row with its model and cost.
5. **Graceful degradation.** Where `/code-review` is unavailable (an older Claude Code, a
   stripped executor), the two agents run alone and the stamp says `code_review: unavailable`.

Two facts decide whether the lens also runs in the orchestrator's unattended steps, and are
checked before it is built: whether a headless `claude -p` executor can invoke the built-in skill,
and whether its reviewer subagents pass through the spawn guard (§4). Until both are confirmed it
runs in interactive sessions and the CI review job only.

## 17. Explaining at agent velocity: touchpoints, review packs and attention

When agents write most of the code, the scarce resource is no longer engineering time. It is
human attention and human understanding. An FDE who approved forty agent changes this week
understands less of the system than one who wrote four. The gates of sections 12 to 16 stay
human, but a gate is only as good as the understanding of the person at it. So the factory takes
on three duties beside building:

1. **Ask fewer, better questions.** Questions come early and in one batch (§14), and only the
   risky ones go to a human (tiers, §16.3's promotion rule).
2. **Make every question explain itself.** Each human gate gets a **review pack**: one artifact
   that a customer, a PMO or a manager can read and decide from.
3. **Measure attention.** Decision latency, approvals given without reading, and code nobody
   understands become metrics and findings, like any other defect.

### 17.1 The output ladder as house rules

Explanations follow a four-level ladder (after Andrej Karpathy's), from cheapest to richest:

| Level | Form | In the factory |
|---|---|---|
| 1 | **Controlled English**, about 80% of ASD-STE100: short sentences, one idea each, active voice, plain words | `templates/present/STYLE.md` for every drafter; a lint in the renderer (sentence length, a jargon list mapped to plain words, passive voice as a warning) |
| 2 | **Diagrams over prose** | inline SVG drawn **deterministically from the data and the graph**: maturity by dimension, the risk matrix, the roadmap, a blast radius, a dependency chain. No diagram is drawn by a model |
| 3 | **Small interactive HTML** | each pack is one self-contained file: deck and reading modes, filters, an option comparison, "show technical detail" |
| 4 | **Change over time** | an HTML timeline that replays the ledger (intent, contract, merge, release, incident). Real video only later, for customer demos |

One rule sits over all four: **models write the words; the data supplies the numbers.** The
renderer computes every figure, and it refuses a narrative that contains a number the source data
does not. That is "deterministic checks gate; models propose" applied to explanation.

### 17.2 One pack per human touchpoint

| Stage | What the human decides | Pack |
|---|---|---|
| Intake | Is this what our system looks like? | **Onboarding**: the system map, today's debt, where the risk is |
| Measure | Which path do we fund: A, B or C? | **Assessment**: maturity score, security risk matrix, options compared |
| Ideate | Which ideas get a brief? | **Idea shortlist** |
| Brief | Is this the outcome, and the bet we make? | **Brief**: outcome, hypothesis, riskiest assumption, roadmap |
| Design | The questions to answer before anything is built | **Decisions**: each decision with options, recommendation and consequences |
| Design | Do we accept this record? | **Record**: a PDR, ADR or DDR in one page |
| Review | Is this change safe to merge? | **Change explainer** (§17.4) |
| Accept | Did it do what we promised? | **Acceptance**: each criterion with its evidence from the released system |
| Done | Keep it, change it, or kill it? | **Outcome**: the metrics against the hypothesis |
| Any | Which findings do we fix, accept or dismiss? | **Findings triage** |
| Weekly | Where are we, and what waits on us? | **Portfolio** for the PMO (§17.6) |

Every pack keeps the same contract:

- One self-contained HTML file under `docs/reviews/`, committed with the artifact it explains;
  no network references, readable offline, printable to a PDF deck.
- It opens with a **headline verdict** and ends with **"The decision we need from you"**: the
  ask, the options with the recommendation marked, who decides, by when, and what happens after
  each option. An appendix holds the engineers' detail (paths, rule ids, commands).
- It meets the accessibility rules of the pack's theme (contrast in both themes, focus, target
  size) and takes the organisation's name and accent from `.claude/sdlc.json`.
- **A pack never decides, and never blocks.** The human answers through the ledger, as before. A
  failed render is one line in the skill's output, never a stopped gate.
- **What the human saw is evidence.** When a transition or a decision is recorded after a pack,
  a `review_pack` evidence row stores the pack's path and SHA. The audit trail then shows what the
  decider was shown, not only what they decided.

### 17.3 The ladder in each pack

The level is chosen per pack, not per page: the onboarding pack leans on Level 2 (the system map
from the code graph), the assessment and decisions packs on Level 3 (comparing options), the
outcome and incident packs on Level 4 (change over time). Every pack is Level 1 throughout.

### 17.4 The change explainer

A human cannot read forty agent-written files a day, and an unread change is approved on trust.
So every PR gets a one-page explainer next to the evidence section `/factory:ship` already
writes:

- **What and why**: the story it serves and its criteria, in Level 1 English.
- **Blast radius**: a diagram from `graph_impact` (the touched symbols, what depends on them,
  the stories and records on those symbols, open findings there).
- **What the agent decided alone**: the defaults it applied (`assumptions.md`), any rewrite by the
  spawn guard, any access outside the declared capabilities.
- **What was verified, and what was not**: tests, acceptance, reviewers' "not reviewed" lists.
- **Where to look**: the three hunks a reviewer should read first, chosen by risk (auth paths,
  data changes, public interfaces), not by size.

### 17.5 Attention signals

These are metrics in `metrics_snapshot`, and a breach becomes a `finding` of source `process`
(§16.5), so it is triaged like a defect instead of admired on a chart. **They are reported at
team level by default**: by team, stage and tier, never by person. The point is to route
attention, not to rank people, and a metric that names individuals teaches people to game it.
Per-person views are an explicit opt-in, set by the organisation in `.claude/sdlc.json`, and a
person always sees their own figures. A `process` finding names the team and the gate, never a
person.

- **Decision latency**: hours from a decision row or a pack to its resolution, by decider and by
  stage. At agent speed, waiting for a human is the slowest step; this names who it waits on.
- **Rubber stamps**: approval time against diff size and tier. A high-tier change of 900 lines
  approved in 40 seconds is a finding. The response is not blame; it is routing: low-risk changes
  are batched and sampled, so human attention goes where the risk is.
- **Comprehension debt**: per module, the share of lines written by agents, the date a human last
  reviewed it in depth (a review that opened the explainer and commented, not an approval alone),
  and the people who can explain it. A module that agents wrote and nobody has read for a quarter
  is a risk, and it earns an "explain this module" pack: a Level 2 map from the code graph that
  onboards a human to code no human wrote.
- **Drift digest**: once a week, everything agents decided alone (defaults applied, model
  rewrites, capability findings, escalations), in one page. Each item is small; together they are
  the decisions nobody approved.

### 17.6 Packs for people outside the team

- **Portfolio** (weekly, for the PMO): the lifecycle board (§16) in Level 1 English, what shipped
  against which outcome, what is stuck, and above all **which decisions wait on whom**.
- **Release notes** for customers, generated from **accepted stories**, never from commits.
- **Audit**: the evidence chain per change (who decided, what they were shown, what verified it,
  the OIDC-signed CI run) in the form an auditor asks for, for SOC 2 or ISO 27001 reviews.
- **Incident explainer**: the Level 4 timeline from the intent to the failure, with the blast
  radius and the rule or test that now prevents it.

### 17.7 Decisions get feedback

A recommendation is a model's proposal; its quality is measurable. The ledger already knows which
decisions were later superseded, which defaults contradicted a decision, and which
recommendations a human overrode. `metrics_snapshot` reports those rates by stage and tier, and a
recommendation pattern that humans keep overriding goes to triage as a `process` finding. The
packs then get better at the one thing they exist for: helping a person decide well, quickly.

### 17.8 What not to build

- A dashboard nobody opens. Packs appear at a gate or once a week, never as a stream.
- Any figure written by a model, or any diagram a model draws freehand.
- A pack that blocks a gate, or a gate that a pack replaces.
- Video before the HTML timeline has proved what is worth animating.
- A second store for packs. They are files in git; the ledger holds their path and SHA.

### 17.9 Build sequence

The explanation layer builds in step with §16, because most packs explain a lifecycle stage that
§16 creates. The file-level plan, merged with §16's increments into one sequence, is in
`plugins/factory/docs/plans/explain.md`. In outline:

1. **Review packs, first four** (onboarding, assessment, brief, decisions): the renderer, the
   style lint, the number guard, the theme. Needs nothing from §16.
2. **Pack evidence and gate wiring**: the `review_pack` evidence kind, recorded with the
   transition or decision a pack preceded.
3. **Change explainer** on every PR.
4. **Attention signals**: decision latency, rubber stamps and the drift digest as metrics and
   `process` findings (needs §16's findings).
5. **Comprehension debt** and the "explain this module" pack.
6. **Lifecycle packs**: idea shortlist, record, acceptance, outcome and findings triage, each
   landing with the §16 increment that creates its stage.
7. **Outward packs**: portfolio, release notes, audit, and the incident timeline.
8. **Decision feedback**: override and reversal rates, and their findings.

<!-- Sources: the AI-native SDLC playbook (claude.com/resources/articles/the-ai-native-sdlc-playbook);
the pack; pgvector docs for
HNSW and cosine ops; Voyage AI as Anthropic's embeddings partner (Anthropic's embeddings page says it
has no embedding model of its own; current Voyage models are voyage-4-large, voyage-4, voyage-4-lite
and voyage-code-4, 1024 dimensions by default). Claude Code facts (Agent hook fields, model resolution
order, SDK option names, env var names) were verified against the current docs on 2026-11-02;
section 9's database facts likewise. -->
