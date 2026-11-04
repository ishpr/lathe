# Explanation build plan (DESIGN §17), merged with the lifecycle plan (DESIGN §16)

This is the file-level plan for the eight increments in DESIGN §17.9 (X1–X8). They are merged with the eight increments of §16.10 (L1–L8, planned in `docs/plans/lifecycle.md`) into one sequence. Each increment ships alone, keeps every CI job green and leaves the factory working. Paths are relative to `plugins/factory/` unless they start with `docs/DESIGN.md`, `.github/` or `CHANGELOG.md`, which are at the repo root. A path marked **(new)** does not exist on `design-lifecycle` today. Every other path was checked.

**CI commands every increment must pass** (the same block as `lifecycle.md`):

```
python3 -I plugins/factory/scripts/check_skill_permissions.py --root plugins/factory
python3 -m pytest plugins/factory/tests/sdlc -q
uv run --project plugins/factory sdlc-graph db init
uv run --project plugins/factory python -m pytest plugins/factory/tests/graph -q
bash plugins/factory/tests/epic/check_examples.sh && python3 -m pytest plugins/factory/tests/epic -q
bash plugins/factory/demo/run_demo.sh
```

The Spanner parity tests follow `lifecycle.md`. They use `_emulator_running()` from `tests/graph/graph_fakes.py`, which L2 moves there, and carry `@pytest.mark.spanner`.

## The merged sequence

| # | Increment | Needs (the ordering edge) | Can run in parallel with |
|---|---|---|---|
| 1 | **X1** Review packs, first four | nothing | L1 in development; **merges before L1** |
| 2 | **L1** Rename | nothing; lands after X1 (below) | X2 in development |
| 3 | **X2** Pack evidence and gate wiring | X1 (packs exist to cite); after L1 so it edits `skills/brief`/`skills/design` once | L2 in development; **merges before L2** |
| 4 | **L2** Registry, schema and lifecycle | — | X3 in development |
| 5 | **L3** Stories | L2 | X3 |
| 6 | **X3** Change explainer | X1 (renderer), X2 (`review_pack` on `in_review`); reads L3's `intent.stories` when present | L3 (both edit `graph_impact`: the second rebases), L4 |
| 7 | **L4** Decision records | L3 | X3 |
| 8 | **X6a** Record pack | L4 (record nodes) | L5 |
| 9 | **X6b** Acceptance and outcome packs | L3 (stories, `acceptance_signoff`); outcome also reads L2's `lifecycle_view` | L5 |
| 10 | **L5** Feature design document | L3, L4 | X6a, X6b |
| 11 | **L6** Findings | L3, L4 | — |
| 12 | **X6c** Findings triage pack | L6 | X4 |
| 13 | **X4** Attention signals and drift digest | L6 (`process` findings), X3 (`pr_review` evidence for rubber stamps), X2 (pack→decision latency), L2 (`stage_of`) | X6c, L7 |
| 14 | **X5** Comprehension debt and module pack | X4 (`attention.py`, team derivation, policy), X3 (`pr_review` is the deep-review signal) | L7, X8 |
| 15 | **L7** Ideas | L2 | X5, X8 |
| 16 | **X6d** Idea shortlist pack | L7 | X8 |
| 17 | **X8** Decision feedback | L4 (superseded records), X4 (process-finding path) | L7, X6d |
| 18 | **L8** Board and funnel | all of L1–L7 | — |
| 19 | **X7** Outward packs | L8 (portfolio reads the board), L3 (release notes from accepted stories), X2 (audit cites `review_pack`), X4 (portfolio's "waits on whom") | — |

**The rename and X1.** X1 is in flight and wires `/factory:init`, `/factory:assess`, `/factory:epic` and `/factory:ticket`. **X1 merges first.** L1's sweep then carries X1's wiring into the new skills:
- the present step of `skills/epic/SKILL.md` moves to `skills/brief/SKILL.md`, and the one in `skills/ticket/SKILL.md` moves to `skills/design/SKILL.md`;
- any `/factory:epic` or `/factory:ticket` text in `templates/present/STYLE.md`, the narrative schemas, `scripts/present.py` help and messages, and X1's tests is rewritten;
- `tests/sdlc/test_rename.py` gets `templates/present/` and `scripts/present.py` added to the files it scans;
- the pack kind `brief` already carries its stage name and the kind `decisions` is stage-neutral, so neither is renamed.

If L1 merged first instead, X1 would rebase onto `skills/brief` and `skills/design`. That costs the same work, done by the branch that is moving. Choose X1 first because it is smaller and further along.

**Lifecycle plan updates this sequence forces.** `lifecycle.md` hard-codes MCP tool counts. X3 adds one read tool after L3, so the counts become: L5 17 read / 27 total, L6 18 read / 11 write / 29, L7 30, L8 19 read / 31. Edit `lifecycle.md` in X3's change. `tests/graph/test_mcp.py` catches any mismatch.

## Choices that span all increments

- **Data from the ledger, rendering from stdlib.** `scripts/present.py` stays stdlib-only and never opens the database. Each pack that needs the ledger or the code graph gets its data from a new `sdlc_graph/explain/` package **(new)**:
  - `sdlc-graph explain <kind> <slug|path> [--repo] [--since] --out F.json` writes the data;
  - `present.py <kind> --data F.json --narrative N.json` renders it.
  - The same data goes out through one read-only MCP tool, `explain_data(kind, ref, repo=None, since=None)`, added in X3. Later kinds add no tools.
- **One registry for pack kinds.** `sdlc_graph/shapes.py` gains `PACK_KINDS` and the conformance test asserts that it equals the keys of `present.PACKS`, loaded through `importlib` from `scripts/present.py`. Each increment below lists the kinds it adds.
- **No new node kind or edge in any X increment.** Packs are files in git, cited by evidence. So the node and edge CHECKs, `state_machines.json`, `lifecycle.json` and `KIND_SECTIONS` do not change. Packs are not indexed, because a rendering is not a source and indexing one would be a second copy.
- **Thresholds live in one file.** `sdlc_graph/ledger/attention_policy.json` **(new, X4)** holds the latency SLAs, the rubber-stamp floors, the comprehension thresholds and `min_group`. `ledger/__init__.py` gains `ATTENTION_POLICY_PATH`.
- **Settings.** Every new MCP tool goes into `templates/settings.project.json` in both spellings (`mcp__plugin_factory_graph__<t>`, `mcp__graph__<t>`) and into the `"mcp__*"` note string. Every human-only CLI path gets two Bash deny rules, `Bash(uv run * sdlc-graph … *)` and `Bash(* sdlc-graph … *)`, following lines 253–262.

## Attention metrics and privacy (settled)

DESIGN §17.5 now says attention metrics are reported at team level by default. The plan follows that and does not reopen it.

- **Config** (in `.claude/sdlc.json`, which is already in `lib/sdlc.py: ALWAYS_PROTECTED`, so an agent cannot switch it on):

  ```json
  "attention": {"per_person": false, "teams": {}, "min_group": 3}
  ```

  - `per_person: true` is the organisation's opt-in for per-person breakdowns.
  - `teams` maps an individual handle to a team (`{"@alice": "payments"}`). It is only needed when CODEOWNERS names people instead of teams.
  - `min_group` defaults to 3. A group with fewer items is shown as "fewer than 3", because a team of one is a person.
  - Parsing is `sdlc_graph/graphcfg.py: load_attention(path)` **(new function)**. A missing or invalid block falls back to the defaults, which are the private ones.
- **How "team" is derived.** `ledger/attention.py: team_of(conn, node, cfg) -> str` **(new)**:
  1. The paths of the item are `capabilities.paths` (each glob cut at its first `*?[`) plus the paths of `touched_symbols`.
  2. `codegraph.queries.owners(conn, repo, paths)` returns the owners, using the newest snapshot's CODEOWNERS rules (`extract_owners.match`, last rule wins).
  3. A team handle is one with a slash (`@org/team`). An individual handle goes through `cfg.teams`.
  4. The team that owns the most paths wins, with ties broken alphabetically. With no owner, the team is `"(no team)"`.
  5. An item with no paths (an epic or an idea) maps `attrs.owner` through `cfg.teams`, else `"(no team)"`.

  So a figure is "how long this team's gates wait", counted against the code's owners, never against the decider.
- **The self view.** `sdlc-graph metrics attention --me @handle` shows only that handle's rows. It is a CLI convenience, not an access control: `decided_by` is already readable by anyone with ledger read. The database roles (`templates/docs/db-roles.sql`) are the boundary, and the risk says so. The MCP server has no self view, because its actor is an agent. Deny `* sdlc-graph metrics attention * --me *` for agents in both Bash spellings.
- **What the rule covers.** It covers attention metrics, process findings, the drift digest and the portfolio's waiting list. Gate packs (decisions, change explainer, record) still name the person the ask is addressed to, because "who decides" is the ask itself.
- **Doc fix in X4.** DESIGN §17.5's decision-latency bullet still says "by decider". Change it to "by team, stage and tier".

## X1: Review packs, first four (in flight on `review-packs`)

In flight. Later increments rely only on this contract. If X1 lands with a different shape, the first task of X3 is an adapter to it.

- **CLI:** `present.py <kind> [--root R] [--slug S] [--data F.json] [--narrative N.json] [--out P] [--json]`.
  - It writes `docs/reviews/<YYYY-MM-DD>-<kind>[-<slug>].html`.
  - With `--json` it prints `{path, slides, warnings}`.
  - It exits 0 on a render with warnings and non-zero only on misuse. A failed render is one line in the skill.
  - X3 needs `--data`; if X1 lacks it, X3 adds it.
- **The pack registry.** `PACKS: dict[str, PackSpec]` in `present.py`, one entry per kind:

  ```python
  class PackSpec(TypedDict):
      kind: str                      # "change"; also the file-name segment
      title: str                     # the headline-verdict slide's default title
      level: int                     # 1-4, the ladder level the pack leans on (DESIGN 17.3)
      load: Callable[[Path, str | None, Path | None], dict]   # (root, slug, data) -> source data; reads files only
      narrative_schema: str          # "templates/present/narrative/<kind>.schema.json"
      numbers: Callable[[dict], set[str]]   # every number the guard accepts (the source data's figures)
      slides: Callable[[dict, dict], list[Slide]]   # (data, narrative) -> slides; the last is "The decision we need from you"
      diagrams: tuple[str, ...]      # names of deterministic SVG builders it uses
  ```

- **Adding a pack kind** takes:
  1. one `PACKS` entry;
  2. `templates/present/narrative/<kind>.schema.json`;
  3. glossary terms in `templates/present/glossary.json`;
  4. a fixture `tests/sdlc/fixtures/present/<kind>.json`;
  5. a row in the parametrised render test (number guard, style lint, no network references, both themes);
  6. the name in `shapes.PACK_KINDS`.
- **Diagrams** are pure functions `svg_<name>(data) -> str` in `present.py`. They are deterministic: the same input gives the same bytes, checked by golden-file tests.
- **The narrative hand-off.** The skill launches `factory:drafter` with the data JSON and the schema. The drafter returns the narrative JSON and `present.py` renders it. A number in the narrative that is not in `numbers(data)` is refused.

## X2: Pack evidence and gate wiring

**Goal:** what the decider was shown becomes evidence on the node, and it is attached automatically to the decision or transition the pack preceded. **Why here:** it needs only X1. Landing it before L2 means L2's conformance step A sees `review_pack` in `EVIDENCE_KINDS` from the first run.

**Files:**
- **`ledger/evidence.py`:** add `"review_pack": ("pack", "path", "sha256", "shown_for")`, with these checks in `validate()`:
  - `pack` is in `shapes.PACK_KINDS`;
  - `path` matches `^docs/reviews/\d{4}-\d{2}-\d{2}-[a-z_]+(-[a-z0-9-]+)?\.html$`;
  - `sha256` matches `_SHA256`;
  - `shown_for` is `{"decisions": [int > 0, …]}` or `{"transition": <state>}`, or both, and not empty;
  - optional `commit` uses `_GIT_SHA`; optional `warnings` is an int ≥ 0.
- **`shapes.py`:** `PACK_KINDS = ("onboarding", "assessment", "brief", "decisions")`.
- **`ledger/decisions.py: resolve`:** inside the existing transaction, find the newest `review_pack` on the node whose `shown_for.decisions` contains this id and whose `at ≤ now()`. Write `meta = meta || {"pack": {"evidence_id", "path", "sha256"}}`. Without one, `meta` is unchanged. There is no new parameter, so every caller keeps working.
- **`ledger/transitions.py: transition`:** after the guards pass, append to `evidence_ids` the newest `review_pack` whose `shown_for.transition == to_state` and whose `at` is after the node's previous transition. It is cited, never required: no guard reads it.
- **Skills:** each pack step X1 added (`skills/init`, `skills/assess`, `skills/brief`, `skills/design`) follows the render with `evidence_add` kind `review_pack`, taking the path and sha256 from `present.py --json` and `shown_for` from the decisions the pack asks about. With no graph, skip silently, as `skills/ship/SKILL.md` 5a does. `present.py --json` adds `sha256` to its output.
- **CLI:** `sdlc-graph evidence add` needs no change. `sdlc-graph decision show <id>` prints `meta.pack`.

**Graph lock-step checklist:**

| Place | Change |
|---|---|
| registry | `PACK_KINDS`; `review_pack` in `evidence.KINDS` (and in `EVIDENCE_KINDS` once L2 adds it) |
| CHECKs, machines, `lifecycle.json`, `KIND_SECTIONS` | none |
| Spanner | none: evidence is an exported table (`_TABLES["evidence"]`), not in the property graph. `decision.meta` is exported from L2 on (that column-parity fix is L2's) |
| `graph_impact` | unchanged |
| conformance | `PACK_KINDS == present.PACKS` (pure; added to `test_registry_conformance.py` when L2 creates it, until then in `tests/sdlc/test_present_registry.py` **(new)**) |

**Tests:**
- `tests/graph/test_review_pack.py` **(new, db)**:
  - payload validation rejects a path outside `docs/reviews/` and an unknown pack;
  - resolving decision 31 after a pack with `shown_for.decisions=[31]` writes `meta.pack`;
  - a pack for decision 32 is not attached to 31;
  - a transition to `ready` cites the pack with `shown_for.transition="ready"` and ignores one older than the previous transition;
  - no guard changes outcome with or without a pack (a pack never blocks).
- `tests/sdlc/test_present_registry.py`.

**Exit check:** the CI commands. **Demonstration:** `/factory:design` on the sample app renders the decisions pack. The human runs `sdlc-graph decision resolve 31 Redis --actor @po`, and `decision show 31` prints the pack path and sha.

**Risks:**
- The evidence says "presented", not "read". The audit pack (X7) must word it that way.
- Local evidence is advisory, so a pack file edited after the row was written shows a sha mismatch. `explain audit` (X7) recomputes the hash and flags it.

**Size:** S.

## X3: Change explainer

**Goal:** every PR gets a one-page explainer next to `/factory:ship`'s Evidence section, with a blast-radius diagram drawn from `graph_impact`, and CI starts recording review activity (`pr_review`), which X4 and X5 measure. **Why here:** it needs X1 and X2, and it reads L3's `intent.stories` when present. The review-activity source must exist a few weeks before X4 so X4 has data to compute from.

**Files:**
- **`codegraph/queries.py: impact`** and **`impact/affected.py: affected`** gain `include_edges: bool = False`. When set, one more query returns every edge between reached symbols: `SELECT src, dst, rel FROM sym_edge WHERE snapshot_id=%s AND src=ANY(%s) AND dst=ANY(%s) AND rel=ANY(IMPACT_RELS)`, mapped to symbol keys and sorted. `shapes.AffectedSet` documents `edges: list[{"src": key, "dst": key, "rel"}]`.
- **`mcp_server.py: graph_impact`** gains `edges: bool = False`. The tool count is unchanged.
- **`explain/__init__.py`, `explain/change.py` (new):**
  - `change_data(conn, slug, *, root, base) -> ChangeData`.
  - `rank_hunks(diff_text, affected, cfg) -> [hunk]` picks "Where to look" deterministically from `git diff -U0 <base>...HEAD`:
    - auth paths and route handlers (`paths.auth` in `sdlc.json`, `exposes_route` symbols): 100;
    - data (`sql/**`, migrations, `model` symbols, paths governed by an accepted DDR once L4 lands): 80;
    - public interfaces (a symbol with reverse in-degree ≥ 1 from another module in the affected set): 60;
    - config and capabilities: 40.
    - Ties break by hunk size, then by path. The top 3 are kept.
  - **What the agent decided alone:**
    - `assumptions` evidence (`evidence.assumption_items`);
    - runs on the node whose `meta.rewritten_to` is set (written by `hooks/record_agent_run.py:185,210`);
    - lines of `.claude/work/<slug>/capability_findings.jsonl`, read before ship step 2 deletes it, and later L6 `finding` nodes of source `capability`;
    - escalations (decision rows with `severity`);
    - decisions whose `decided_by` is null but whose choice equals the recommendation.
  - **Verified / not verified:** the newest `verify_stamp`, the `acceptance` rows, the reviewers' **Not reviewed** lists from `contract.md`'s Evidence section, and the affected tests that the stamp's run did not include.
- **The blast-radius data shape** (consumed by `present.py: svg_blast_radius`, which lays it out in columns by depth and rows by key):

  ```
  {"snapshot_id": int, "sha": str,
   "touched": [key],                                    # depth 0, sorted
   "nodes": [{"key", "kind", "path", "depth", "is_test": bool, "routes": [str], "owners": [handle]}],
                                                        # sorted (depth, key); cap 40: all of depth 0-1, then by key
   "edges": [{"src": key, "dst": key, "rel"}],          # both ends in nodes; rel in IMPACT_RELS; sorted
   "truncated": {"nodes": int, "edges": int},
   "counts": {"symbols", "routes", "tests"},
   "intent": {"stories": [{"slug","state"}], "records": [{"slug","record_type"}],
              "findings": [{"slug","severity","state"}], "contracts": [{"slug","state"}]}}
                                                        # each [] until L3 / L4 / L6 supply it
  ```

- **`scripts/present.py`:** pack `change` (Level 2), diagrams `blast_radius` and `risk_hunks`. The change pack also writes a Markdown summary (`--md-out`): the headline, what and why, where to look, and the path of the HTML file. GitHub shows HTML as source, so the PR body carries the Markdown. `templates/present/narrative/change.schema.json` **(new)**.
- **`skills/ship/SKILL.md`:** a new step 0b before the shipper:
  1. launch `factory:implementer` with `[sdlc stage=ship node=<slug>] explain: change data` to run `uv run --project "${CLAUDE_PLUGIN_ROOT}" sdlc-graph explain change <slug> --base origin/<base> --out .sdlc-stage/<slug>/change.json`;
  2. then the drafter writes the narrative;
  3. the shipper's step 1 renders it with `present.py change`, adds the Markdown to the PR body under **Change explainer** and stages the HTML with `git add -- docs/reviews/<file>`;
  4. step 5a adds `review_pack` with `shown_for.transition="in_review"`.

  With no graph, the explainer still renders from git and the work files, with an empty blast radius and a "graph off" line.
- **`scripts/worktree_fingerprint.py`** ignores exactly `docs/reviews/*.html`, as it ignores `.claude/work/`, so adding the explainer does not break the verify stamp. New `scripts/check_packs.py` **(new, stdlib)** fails CI on any `docs/reviews/*.html` that lacks `present.py`'s generator meta tag, holds a `<script src`, `<link href=http…>` or `url(http…)`, or exceeds 400 KB. Run it in `.github/workflows/test.yml` and `templates/ci/*.yml`.
- **`templates/pull_request_template.md`:** a `## Change explainer` section, and in the reviewer checklist `- [ ] I read the change explainer and the three hunks it names`. This box is the only "explainer opened" signal (see X4).
- **`skills/review-pr/SKILL.md`:** step 0 runs `explain change` for the PR and prints "Where to look" first. The existing step 5 already asks for "the three places to read by hand"; it now starts from those three.
- **`templates/ci/{python,node,go,rust}.yml`:** a new job `review` on `pull_request_review: types: [submitted]` with `permissions: {contents: read, pull-requests: read, id-token: write}`. It runs no PR code and posts the binding `pr_review` evidence (below). When `state == "approved"` it also posts the existing `review_approval {reviewer, author, url}`, so the `in_review → approved` guard gets a real CI writer for the first time. Today only `demo/run_demo.sh:243` and tests write it.
  - **The slug** comes from the ledger: `sdlc-graph evidence find --kind pr_url --url <html_url>` **(new CLI)**. Do not use `.claude/work/current.json`, which `/factory:ship` step 2 deletes.
  - **Gap found:** the existing `evidence` job reads `current.json`, so on a shipped PR it always skips. Fix it with the same lookup in this change.
- **`ledger/evidence.py`:** add `"pr_review": ("url", "reviewer", "author", "state", "submitted_at", "window_start", "additions", "deletions", "changed_files", "comments", "files_commented", "explainer_ack")`:
  - `state` is one of `approved`, `changes_requested`, `commented`;
  - both times are ISO-8601 with a zone;
  - the counts are ints ≥ 0;
  - `files_commented` is `{path: int}`;
  - `explainer_ack` is a bool;
  - optional `paths` is a list of at most 300 entries.
- **Where each signal comes from:**
  - `submitted_at`, `state`, `body` and `reviewer`: the event payload (`github.event.review`).
  - `additions`, `deletions`, `changed_files` and `author`: `github.event.pull_request`.
  - `comments` and `files_commented`: `gh api repos/{repo}/pulls/{n}/reviews/{id}/comments`, which gives each inline comment's `path`. A body of at least 40 characters counts as one comment.
  - `window_start`: the latest of `ready_for_review` or `created_at`, the reviewer's `review_requested`, and the newest head commit's committer date. These come from `gh api repos/{repo}/issues/{n}/timeline`. The committer date is not the push time; see Risks.
  - `explainer_ack`: the reviewer checklist box, ticked in `github.event.pull_request.body`. It is self-reported.
  - `paths`: `gh api repos/{repo}/pulls/{n}/files`.
- **`mcp_server.py`:** `explain_data(kind, ref, repo=None, since=None)` goes in READ_ONLY (16 read, 10 write, 26 total). Update `test_mcp.py`. Settings allow `explain_data` in both spellings, `Bash(uv run --project * sdlc-graph explain *)`, and `Bash(python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/present.py" *)` if X1 has not added it.
- **`shapes.PACK_KINDS += ("change",)`.**

**Graph lock-step checklist:**
- Registry: `pr_review` in `EVIDENCE_KINDS`; `change` in `PACK_KINDS`.
- `graph_impact`: `edges`. Its `intent.*` lists stay L3/L4/L6's.
- Spanner: `QUERIES["blast_radius"]` = `GRAPH SdlcGraph MATCH (c:Node {slug: {slug}}), (s:Symbol) WHERE CONCAT(s.kind,':',s.qualname,'@',s.path) IN UNNEST(c.touched_symbols) MATCH (d:Symbol)-[e:SymRel WHERE e.rel IN ('imports','calls','tests')]->{1,4}(s) RETURN DISTINCT d.kind, d.qualname, d.path`, mirrored in `spanner/queries.gql`.
  - Parity is against `affected(..., include_edges=True)["nodes"]`, restricted to graph-reached symbols. Exclude conftest expansion, closure and mirror additions, as the `tests_reaching` comment does.
- Conformance: `pr_review` validation is exercised; `PACK_KINDS` equality; `graph_impact`'s schema exposes `edges`.

**Tests:**
- `tests/graph/test_impact_edges.py` **(new, db):** the edges are exactly the reached pairs, sorted; `include_edges=False` output is byte-identical to today's.
- `tests/graph/test_explain_change.py` **(new, db)**:
  - `rank_hunks` puts an auth hunk above a larger plain hunk;
  - a rewrite in run meta appears under "decided alone";
  - a missing graph gives an empty blast radius, not an error;
  - the 40-node cap reports `truncated`.
- `tests/sdlc/test_present_change.py` **(new):** golden SVG for a fixture blast radius; the number guard refuses "approved in 3 minutes" when 3 is not in the data.
- `tests/sdlc/test_check_packs.py` **(new):** a pack with `<script src=…>` fails.
- `tests/sdlc/test_scripts.py`: the fingerprint ignores `docs/reviews/x.html` and still sees `docs/reviews/x.md`; the CI templates contain the `review` job and the `evidence find` lookup.
- `tests/graph/test_mcp.py`: counts.
- `tests/graph/test_spanner_explain.py::blast_radius` **(new, spanner + db)**.

**Exit check:** the CI commands, plus `python3 -I scripts/check_packs.py --root .`, plus `pytest tests/graph/test_spanner_explain.py -m spanner` with the emulator. **Demonstration:** `/factory:ship` on the demo `rate-limit` contract opens a PR whose body has the explainer summary. `docs/reviews/<date>-change-rate-limit.html` shows the blast radius from `app/ratelimit.py` to its route and tests. A test review event, replayed through `act` or the job's script on a recorded payload, writes `pr_review`.

**Risks:**
- **Excluding `docs/reviews/*.html` from the fingerprint** opens a path that is not verified. It is narrowed to one glob and one extension, and `check_packs.py` holds it to the pack form. The edit guard still applies.
- **`window_start` approximates when the reviewer could start.** A reviewer who read an earlier push looks faster than they were. X4 treats a breach as routing, not blame.
- **Fork PRs get no secrets and no OIDC token**, so `pr_review` is missing for them, like `ci_run` today. The metric reports `n` and coverage.
- **Repo weight:** the explainer is committed on every PR, typically 60–150 KB. See the open questions.

**Size:** L.

## X4: Attention signals and the drift digest

**Goal:** decision latency, rubber stamps and the drift digest as `metrics_snapshot` figures at team, stage and tier level. A breach becomes a `process` finding that names a team and a gate, never a person. **Why here:** process findings need L6; rubber stamps need X3's `pr_review` rows; pack-to-decision latency needs X2; "by stage" needs L2's `stage_of`.

**Files:**
- **`ledger/attention.py` (new):** `team_of` (above), `latency`, `rubber_stamps`, `drift`, `breaches`, plus a `group(rows, keys, cfg)` that applies `min_group` and drops person fields unless `per_person` is set.
- **Decision latency.** The data is the `decision` table (`created_at`, `resolved_at`, `decided_by`, `severity`, `meta.pack` from X2, `meta.key` from L2), `transition` rows and `node.tier`. Here `stage(d)` is `lifecycle.stage_of(node.kind, the node's state at d.created_at)`, where that state is the `to_state` of the last transition at or before `created_at`.
  - `decision_hours(d) = (d.resolved_at − d.created_at) / 3600`, for resolved rows with a human `decided_by`.
  - `pack_to_decision_hours(d) = (d.resolved_at − review_pack[d.meta.pack.evidence_id].at) / 3600`, when X2 attached a pack.
  - `waiting(d) = now − d.created_at`, for unresolved rows; reported as a count and the oldest age.
  - `human_gate_hours(t) = (t.at − prev(t).at) / 3600`, for each transition `t` into a `PROTECTED_STATES` state by a human actor, where `prev(t)` is the node's previous transition. Its stage is `stage_of(kind, t.from_state)`.
  - Each figure is reported as `{n, median, p90}` grouped by `(team, stage, tier)`.
- **Rubber stamps.** The data is `pr_review` with `state = approved`, joined to the contract's `tier`.
  - `lines = additions + deletions`.
  - `approval_seconds = submitted_at − window_start`.
  - `floor(tier, lines) = base[tier] + per_100[tier] × ceil(lines / 100)`. The defaults in `attention_policy.json` are high 300 + 120, medium 120 + 60, and low and trivial exempt (sampled, never flagged).
  - **stamp** when `approval_seconds < floor` and `comments == 0`. A high-tier change of 900 lines has a floor of 300 + 120 × 9 = 1380 s, so an approval in 40 s is a stamp.
  - `rubber_stamp_rate = stamps / approvals` per `(team, tier)`.
  - `explainer_ack_rate = acks / approvals` is reported beside it, labelled "self-reported".
- **Drift digest.** For the window, everything agents decided alone, counted by team and stage:
  - `assumptions` items;
  - runs with `meta.rewritten_to`;
  - `finding` nodes of source `capability` (posted by L6's adapter, since `capability_findings.jsonl` is deleted at ship);
  - decisions with a `severity` (escalations);
  - decisions where `choice == recommendation` and `decided_by` is null (agent defaults that nobody confirmed).
- **Breaches** go through `findings.post(source="process", …)`, fingerprinted so a repeat is counted, not filed twice:
  - an open decision older than `decision_sla_hours[severity or tier]` (defaults P0 4, P1 24, P2 72, high 48, other 120) or past `due` → `process|decision_latency|<team>|<stage>`;
  - `rubber_stamp_rate ≥ 0.3` over at least `min_group` high or medium approvals in 28 days → `process|rubber_stamp|<team>|<tier>`.
  - The finding's title and payload carry counts, the team and the gate, and never a handle.
- **`ledger/metrics.py: snapshot`** gains `attention: {decision_latency, human_gate_hours, rubber_stamps, drift, coverage: {pr_review_share, explainer_ack_share}}`. The signature gains `attention_cfg=None`, defaulting to the private defaults.
- **`ledger/findings/finding_policy.json` (L6):** a `process` source with a response time of 30 days for each severity. L6's conformance check (every adapter source is in the policy) covers it.
- **CLI:** `sdlc-graph metrics attention [--repo] [--since] [--me @h] [--json]` and `sdlc-graph attention post-findings [--repo] [--since]` (actor `ci:cron` or human). **Pack:** `drift` (Level 1) through `explain drift`.
- **`skills/explain/SKILL.md` (new; or extend X1's generic pack skill if it adds one):** `/factory:explain <kind> [ref]` for the weekly and on-demand packs (drift; later module, portfolio, release notes, audit, incident). It hands the data and the narrative to subagents as X1 does.
- **`templates/ci/attention-weekly.yml` (new):** a weekly schedule. It runs `attention post-findings` with OIDC and uploads the digest data as an artifact. It never pushes to the protected branch; a human runs `/factory:explain drift` to commit the pack.
- **`docs/DESIGN.md` §17.5:** "by decider" → "by team, stage and tier".
- **Settings:** deny `* sdlc-graph metrics attention * --me *` in both Bash spellings. Allow `Bash(uv run --project * sdlc-graph metrics attention *)`.

**Graph lock-step checklist:**
- No kind, edge, machine or evidence kind; `PACK_KINDS += ("drift",)`.
- Spanner: no new query. These are aggregates over the `decision`, `transition` and `evidence` tables, which are exported but not in the graph. Column parity for `decision.meta` and `severity` is L2's.
- Conformance: every source `attention.breaches` can emit is in `finding_policy.json`, and `attention_policy.json` has an SLA for each severity and each tier in `node.tier`'s CHECK.

**Tests:**
- `tests/graph/test_attention.py` **(new, db):**
  - the latency formulas on fixed timestamps;
  - the stage taken at `created_at`, not today;
  - `pack_to_decision_hours` only with `meta.pack`;
  - the rubber-stamp floor at 900 lines, high tier;
  - a commented fast approval is not a stamp;
  - low tier is never flagged;
  - a breach re-posted counts `finding_seen` instead of inserting.
- `tests/graph/test_attention_team.py` **(new, db):** `team_of` with `@org/team`, with an individual through `cfg.teams`, with ties, and with `"(no team)"`.
- `tests/graph/test_attention_privacy.py` **(new, db)**:
  - with `per_person: false`, walking `metrics_snapshot()["attention"]` recursively finds no key or value matching an individual handle (`^@[A-Za-z0-9-]+$`, no slash);
  - the same holds for every `process` finding's title, attrs and payload, and for the rendered `drift` HTML of the fixture;
  - a group under `min_group` is shown as `"<3"`;
  - with `per_person: true`, `by_person` appears;
  - `--me @a` returns only `@a`'s rows.
- `tests/sdlc/test_present_drift.py`.

**Exit check:** the CI commands. **Demonstration:** on the demo ledger, with decision 31 left open for 30 hours of fixture time and a 40-second approval of a 900-line high-tier contract, `metrics attention --json` shows both by team. `attention post-findings` opens `process|decision_latency|@northwind/api|design` and `process|rubber_stamp|@northwind/api|high`, and no handle appears in either.

**Risks:**
- **A metric that is gamed.** Ticking the box costs nothing. The figure is labelled self-reported and never gates.
- **Teams of one.** `min_group` hides them, and a finding on a small team still names only the team.
- **A thin data history.** Until X3 has run for a while, `coverage.pr_review_share` is low. The pack prints the coverage beside each rate.

**Size:** L.

## X5: Comprehension debt and the "explain this module" pack

**Goal:** per module, the share of lines written by agents, when a human last reviewed it in depth, and who can explain it (teams by default). A module that agents wrote and nobody read becomes a `process` finding and earns a Level 2 module pack. **Why here:** it reuses X4's `attention.py`, team derivation, policy and process-finding path, and X3's `pr_review` is its deep-review signal.

**Files:**
- **`sql/013_authorship.sql` (new; 012 is L2's):** `CREATE TABLE IF NOT EXISTS module_authorship (snapshot_id bigint NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE, path text NOT NULL, lines int NOT NULL, agent_ledger int NOT NULL, agent_trailer int NOT NULL, other int NOT NULL, last_change_at timestamptz, PRIMARY KEY (snapshot_id, path));`. It is derived per SHA like `sym` and never edited. `status.py` gets a marker for 013.
- **`codegraph/authorship.py` (new):** `index(conn, repo_path, snapshot_id)` runs `git blame --line-porcelain -w -M -C <sha> -- <path>` for every `module` symbol path. It is called from `snapshot.index` behind `--authorship`, because blame is slow. Each blamed commit `c` is classified in this order:
  1. **`agent_ledger`:** `c` equals a `merge_sha.sha` of a contract that has run rows with `stage IN ('implement','tests')`, or `c` is in `git rev-list <merge_sha>^1..<merge_sha>` for a true merge commit.
  2. **`agent_trailer`:** `c`'s message matches `scripts/maturity.py: AI_TRAILER_RE`. The factory's own commits carry `Co-Authored-By: Claude` from `templates/settings.project.json:17`.
  3. **`other`:** everything else.
- **Limits of the attribution, printed on the pack and in `metrics_snapshot`:**
  - Trailers can be turned off, so the count is a floor, as `maturity.py` says.
  - A trailer means agent-**assisted**, and a human's assisted commit counts.
  - Rebase merges create SHAs that no `merge_sha` names, so only the trailer path applies to them.
  - Run rows hold no commit SHA, and `tool_use_id` identifies a spawn, not a commit, so there is no direct run→commit join.
  - A human's later edit to an agent's line counts as human, which is right. A pure reformat moves blame (`-w` handles whitespace only).
- **Optional strengthening (in this increment):** `hooks/record_agent_run.py` adds `meta.head_after` (`git rev-parse HEAD`) for runs at stage `implement` and `ship`. This gives a third join for local history. Its limit: squash and rebase rewrite the SHA, so it only helps merge-commit repositories.
- **`ledger/attention.py: comprehension(conn, repo, cfg)`:** module paths are rolled up to their package directory (`attention_policy.comprehension.rollup_depth`, default: the parent directory).
  - `agent_share = (agent_ledger + agent_trailer) / lines`, with both parts reported.
  - `last_deep_review_at = max(pr_review.submitted_at)` over rows where the package has a file in `files_commented`, or where `explainer_ack` is true, `comments ≥ 1` and a file of the package is in `paths`. An approval with no comment never counts.
  - `explainers` is the number of distinct reviewers with a deep review in 180 days, reported as a count plus the owning teams. Names appear only with `per_person`.
  - Debt is flagged when `agent_share ≥ 0.5`, `lines ≥ 50` and `last_deep_review_at` is null or older than 90 days. It becomes the process finding `process|comprehension|<package path>`.
- **`explain/module.py` (new):** the module pack's data:
  - the module's symbols and imports in and out, from `sym_edge`;
  - the routes it exposes and the tests reaching it;
  - the owners (`queries.owners`);
  - the authorship figures;
  - open findings (L6 `findings_on_node` logic) and governing records (L4 `records.governing`) on its paths;
  - the contracts whose `touched_symbols` fall in it, oldest first ("how it came to be").
- **Pack:** `module` (Level 2), with diagram `module_map` (in-package symbols, imports across the boundary, routes). `shapes.PACK_KINDS += ("module",)`. `metrics.snapshot` adds `attention.comprehension`.
- **CLI:** `sdlc-graph explain module <path>` and `sdlc-graph snapshot index --authorship`. The `/factory:explain module <path>` skill. `explain_data` serves kind `module`, so there is no new tool.

**Graph lock-step checklist:**
- No node kind. The new derived table `module_authorship` is on the code-graph half.
- Spanner: `spanner/ddl.py` gets the table and `_TABLES` exports it, keyed by `snapshot_id` like `sym`. `QUERIES["module_contracts"]` = `MATCH (c:Node {kind:'contract'}), (s:Symbol) WHERE s.path STARTS WITH {path} AND CONCAT(s.kind,':',s.qualname,'@',s.path) IN UNNEST(c.touched_symbols) RETURN DISTINCT c.slug, c.state`, mirrored in `queries.gql`. The `{path}` substitution in `export.query` is new, beside `{slug}` and `{symbol}`.
- Conformance: the column parity of `module_authorship` with Spanner; `status._MARKERS` tells 013 apart.

**Tests:**
- `tests/graph/test_authorship.py` **(new, db, a git fixture repo with three commits):**
  - a squash merge named by `merge_sha` with implement runs gives `agent_ledger`;
  - a trailer-only commit gives `agent_trailer`;
  - a human edit on top moves those lines to `other`;
  - a rebase-merged SHA falls back to the trailer.
- `tests/graph/test_comprehension.py` **(new, db):**
  - an approval with no comments does not reset `last_deep_review_at`;
  - an inline comment on a file in the package does;
  - the debt flag thresholds;
  - privacy (`explainers` holds no handles unless `per_person`).
- `tests/sdlc/test_present_module.py` (golden `module_map`).
- `tests/graph/test_spanner_explain.py::module_contracts`.

**Exit check:** the CI commands, plus `sdlc-graph db init` applying 013 twice as a no-op. **Demonstration:** `sdlc-graph snapshot index --authorship` on the sample app, then `explain module app/ratelimit/`. It shows 100% agent share (ledger), no deep review, a `process|comprehension|app/ratelimit` finding, and the rendered map.

**Risks:**
- **Blame cost on large repos.** It is opt-in, incremental (only paths in `sym_delta` since the last authorship snapshot) and capped at 2,000 files per run.
- **Merge strategy.** The figures depend on it. The pack prints which joins applied.

**Size:** L.

## X6: Lifecycle packs (each lands with its §16 increment)

**Goal:** one pack per lifecycle gate, each added through the X1 registry. **Why split:** each pack explains a stage that only exists once its L increment lands.

| Sub | Pack kind | Lands after | Data (`explain/<kind>.py`, new) | Wired into | Recorded as |
|---|---|---|---|---|---|
| X6a | `record` | L4 | the record file, `governs`, `supersedes`, `records.governing` on its paths, the `blast_radius` of its `paths:` | `skills/design/SKILL.md` promotion step; `skills/brief/SKILL.md` PDR draft | `review_pack` `shown_for.transition="accepted"` |
| X6b | `acceptance` | L3 | story criteria, each with `acceptance` rows, the release's `ready_poll`/`smoke`, the contracts that covered it (`story_ac`) | `skills/accept/SKILL.md` (L3) before printing the accept command | `shown_for.transition="accepted"` |
| X6b | `outcome` | L3 | `lifecycle_view`, `guardrail_metric` vs the newest `metrics_snapshot`, accepted and rejected stories; Level 4 timeline of transitions | `skills/feed/SKILL.md` when the kind is `metrics_snapshot` on an epic or feature | `shown_for.transition="measured"` or `"evaluated"` |
| X6c | `findings` | L6 | `findings.queue`, past-response-time, proposed triage (`finding_triage`), duplicates | `skills/findings/SKILL.md` (L6) | `shown_for.decisions=[triage ids]` |
| X6d | `idea_shortlist` | L7 | captured ideas with problem, who, evidence, near-duplicates (`similar_open`) | `skills/ideate/SKILL.md` (L7) | `shown_for.transition="shortlisted"` |

**Files:** for each pack, a `PACKS` entry, `templates/present/narrative/<kind>.schema.json`, `explain/<kind>.py`, glossary terms and a fixture. `shapes.PACK_KINDS` grows by each kind. The skill lines above call `evidence_add review_pack` exactly as X2 does.

**Graph lock-step checklist:** no new kinds or edges. They reuse the Spanner queries L3–L7 add (`story_contracts`, `governing_records`, `findings_on_node`, `idea_funnel`), and the parity tests run on each pack's data function. Conformance: every `PACK_KINDS` entry has a schema file and a fixture.

**Tests:** `tests/sdlc/test_present_packs.py` **(new)**, parametrised over `PACKS` (the number guard, the style lint, no network references, both themes, the last slide is the ask), plus a `tests/graph/test_explain_<kind>.py` per data module.

**Exit check:** the CI commands at each landing. **Demonstration:** for each sub-increment, its skill renders the pack and the gate's transition row cites it.

**Risks:** the packs drift from the gates they explain. Mitigation: the registry test fails when a `PROTECTED_STATES` gate with a human skill has no pack kind mapped in `present.GATE_PACKS` **(new dict)**.

**Size:** M overall (each S).

## X7: Outward packs

**Goal:** packs for people outside the team. **Why here:** the portfolio reads L8's board, release notes need L3's accepted stories, the audit cites X2's packs, and "waits on whom" is X4's.

**Files:**
- **`explain/portfolio.py`:** `board.board(conn, repo)` (L8), shipped work against the outcome (`metrics_snapshot` evidence), stuck items (`parent_stage` progress unchanged for 14 days), and decisions waiting by **team** and gate (X4 rules apply). The pack is Level 1.
- **`explain/release_notes.py`:** stories in `accepted` whose contracts are `shipped_in` the release, with each story's title and criteria in Level 1 words. Commits are never read. The pack is `release_notes`, and a human approves it before it goes to a customer (open question 5).
- **`explain/audit.py`:** per merged contract, the chain:
  - transition rows with actors;
  - `review_pack` rows, with the file's sha256 recomputed and a mismatch flagged;
  - `review_approval`, `pr_review`, binding `ci_run` with `signed_by`;
  - `verify_stamp`, `merge_sha`.

  The pack says "shown", never "read".
- **`explain/incident.py`:** the Level 4 timeline from intent (`caused_by` back to the contract and its story) to merge, release, incident, and the rule or test that closed it (the closers logic of `_repeat_incidents`). Diagram `timeline`.
- `shapes.PACK_KINDS += ("portfolio", "release_notes", "audit", "incident")`. `/factory:explain` gains the four kinds. `attention-weekly.yml` also uploads the portfolio data.

**Graph lock-step checklist:**
- Spanner: `QUERIES["release_stories"]` (`(s:Node {kind:'story'})-[:Rel {rel:'decomposes_into'}]->(c)-[:Rel {rel:'shipped_in'}]->(r:Node {slug:{slug}})`) and `QUERIES["incident_chain"]` (`(n)-[:Rel {rel:'caused_by'}]->(i:Node {slug:{slug}})` plus `governed_by` rules), both with parity tests.
- The audit chain reads `evidence` and stays Postgres-only, which its docstring says.
- Conformance: `board.COLUMNS` is the portfolio's column order.

**Tests:**
- `tests/graph/test_explain_outward.py` **(new):**
  - release notes list only accepted stories, never commit subjects;
  - the audit flags an edited pack file;
  - the portfolio passes the X4 privacy walk.
- Parity for both queries.

**Exit check:** the CI commands. **Demonstration:** `/factory:explain portfolio` and `/factory:explain release_notes <release>` on the demo ledger.

**Risks:** release notes reach customers. The pack is a draft until a human approves it.

**Size:** M.

## X8: Decision feedback

**Goal:** rates that say how good the factory's recommendations are, and a `process` finding for a recommendation pattern that humans keep overriding. **Why here:** reversals need L4's superseded records, and findings go through X4's path.

**Files:**
- **`ledger/attention.py: feedback(conn, repo, since, cfg)`.** The pattern is `meta.key` if set, else the question normalised (lowercase, digits and quoted text stripped). Each rate is grouped by `(stage, tier, pattern)` and by `proposed_by`'s agent type:
  - `override_rate = |resolved, recommendation set, choice ≠ recommendation| / |resolved, recommendation set|`.
  - `reversal_rate`:
    - records: |decision nodes accepted, then superseded within 180 days| / |accepted|;
    - rows: |resolved rows where a later row on the same node has the same `meta.key`| / |resolved rows with a key|.
  - `contradiction_rate = |assumption items whose decision names a ledger decision id and whose default_applied ≠ that choice| / |items naming a decision id|`. Items that cite contract-table numbers (`D1`) cannot be joined to ledger ids and are counted as `unlinked`.
  - `with_pack_vs_without`: `override_rate` split by `meta.pack` present, the one measure of whether packs help.
  - **Breach:** a pattern with n ≥ 5 and `override_rate ≥ 0.5` in 90 days → `process|override|<stage>|<pattern hash>`.
- `metrics.snapshot` gains `attention.feedback`. The decisions pack (X1) shows "humans overrode this kind of recommendation N of M times" when `n ≥ min_group`.

**Graph lock-step checklist:** Spanner `QUERIES["superseded_records"]` (`(n:Node {kind:'decision'})-[:Rel {rel:'supersedes'}]->(o:Node {kind:'decision'})`) with parity. No other change.

**Tests:** `tests/graph/test_feedback.py` **(new):** each rate on a fixture; `unlinked` counted, never guessed; the breach threshold; the privacy walk.

**Exit check:** the CI commands. **Demonstration:** five fixture decisions with key `counter_store`, four overridden, produce the finding.

**Risks:** a small n gives noisy rates. `min_group` and n ≥ 5 apply.

**Size:** S.

## Open questions

1. **Rubber-stamp floors and SLA defaults.** Are 300 s + 120 s per 100 lines (high) and 120 s + 60 s (medium), and the latency SLAs, the right defaults? They are product rules in one file, but the first values set how many findings teams see.
2. **Is a self-reported checkbox acceptable as the "explainer opened" signal?** The pack contract (no network references) rules out telemetry. The alternative is no "opened" signal at all, with deep review meaning only comments.
3. **Commit every explainer, or commit the sha only?** DESIGN §17.2 says packs are committed with the artifact. At 60–150 KB per PR that grows the repo. The other option is to upload the explainer as a CI artifact and keep the sha in `review_pack`.
4. **Who commits the weekly packs (drift, portfolio)?** The cron job cannot push to a protected branch. The plan has a human run `/factory:explain`. An automatic weekly PR is the other option.
5. **Do release notes need a human gate before they reach a customer?** If yes, that is a new evidence kind or a decision row, which this plan does not add.
6. **The comprehension roll-up unit.** Is it per file, per package directory (planned) or per CODEOWNERS rule? And is one finding per package right, or one per team?
7. **Is `min_group = 3` the right default,** and should it also apply to per-person views when the organisation opts in?

### Critical Files for Implementation
- /Users/ishprasad/caylent/demo/lathe/plugins/factory/sdlc_graph/ledger/evidence.py
- /Users/ishprasad/caylent/demo/lathe/plugins/factory/sdlc_graph/ledger/metrics.py
- /Users/ishprasad/caylent/demo/lathe/plugins/factory/sdlc_graph/impact/affected.py
- /Users/ishprasad/caylent/demo/lathe/plugins/factory/skills/ship/SKILL.md
- /Users/ishprasad/caylent/demo/lathe/plugins/factory/templates/ci/python.yml

## Addendum: the review lens (DESIGN §16.11, lifecycle increment 9)

Written after this plan: L9 puts Claude Code's `/code-review` into verify, review-pr, the CI review
job and assess. It needs L6 (findings) and slots in after L6 and X4 in the merged sequence, beside
X6c (the findings triage pack shows its findings). Its two headless facts (can a `claude -p`
executor invoke the built-in skill; do its subagents pass the spawn guard) are checked before it is
planned file by file.
