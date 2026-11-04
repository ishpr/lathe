# Lifecycle build plan (DESIGN §16)

This is the file-level plan for the eight increments in DESIGN §16.10. Each increment ships alone, keeps every CI job green and leaves the factory working. Paths are relative to `plugins/factory/` unless they start with `docs/DESIGN.md`, `.github/` or `CHANGELOG.md`, which are at the repo root.

**CI commands every increment must pass** (copied from `.github/workflows/test.yml`):

```
python3 -I plugins/factory/scripts/check_skill_permissions.py --root plugins/factory
python3 -m pytest plugins/factory/tests/sdlc -q
uv run --project plugins/factory sdlc-graph db init
uv run --project plugins/factory python -m pytest plugins/factory/tests/graph -q
bash plugins/factory/tests/epic/check_examples.sh && python3 -m pytest plugins/factory/tests/epic -q
bash plugins/factory/demo/run_demo.sh
```

The Spanner tests follow the pattern in `tests/graph/test_spanner.py`. They have no pytest marker. Instead they skip at runtime through `_emulator_running()`, which skips unless an emulator is already listening (`make spanner-up`). The pyproject `spanner` marker is declared but unused. Increment 2 moves `_emulator_running` into `tests/graph/graph_fakes.py` so the parity tests can reuse it. Every parity test also gets `@pytest.mark.spanner`, so `-m spanner` selects them.

## Choices that span all increments

- **Spanner keeps one `Node` label and one `Rel` label.** `kind` and `rel` stay properties, as `spanner/ddl.py` has them today. "Spanner knows a kind" means three things:
  - the `node` table gets a `CHECK (kind IN (...))` and the `edge` table a `CHECK (rel IN (...))`, both generated from `shapes.NODE_KINDS` and `shapes.NODE_RELS`, never typed by hand;
  - every lifecycle relation has a named query in `spanner/export.py: QUERIES` and the same text in `spanner/queries.gql`;
  - that query runs in the parity test.

  Per-kind labels would need one node table or view per kind. That is listed under open questions.
- **Guard grammar extensions** (all in `ledger/transitions.py`, all landing in increment 2 so the new machines load):
  - `all_out:<rel>:<kind>=<s1>|<s2>`: every outgoing `<rel>` edge to a node of `<kind>` lands in one of those states. It holds trivially when there are no such edges, so pair it with `edge_out:<rel>` when at least one child is required.
  - `all_in:<rel>:<kind>=<states>`: the same check over incoming edges.
  - `decision:<key>`: the node has a resolved `decision` row with `meta->>'key' = <key>` whose `decided_by` is a human `@handle`.
  - Simple guards: `owner_present`, `actor_is_owner`, `record_file_present`, `story_criteria_covered`, `criteria_signed_off`, `risk_expired`, `problem_present`, and later `design_review_if_required` (increment 5).
  - `context()` gains `children` and `parents`: `[{rel, kind, state, slug, acceptance, attrs}]` over outgoing and incoming edges. `decisions` gains the resolved rows with their `meta` and `due`.
  - `evaluate_detail()` takes these as new keyword arguments that default to empty, so every existing caller and pure test keeps working.
- **PROTECTED_STATES gets one definition.** `mcp_server.PROTECTED_STATES` becomes `from sdlc_graph.ledger.transitions import PROTECTED_STATES`; today the same set is written twice (`mcp_server.py:63` and `transitions.py:94`). The set grows only in the increment that first uses each state (listed under "Autonomy" below).
- **No column changes.** Fingerprint, owner, severity, record type and problem all go in `node.attrs`, with an expression index where a lookup needs one. The one data change is in increment 2: story and decision nodes leave the stateless `active` state. That is a single step because nothing keys on `active` for those kinds. The only readers are `transitions.STATELESS`, `nodes.create` and the `models.py:37` docstring.

## Migrating existing ledgers

- **Contracts without a story: make the story optional for legacy contracts, and required for new ones.**
  - A contract linked straight from a feature (`feature -decomposes_into-> contract`, which is how every ledger looks today) keeps working.
  - `all_out:decomposes_into:story=...` holds trivially on a feature with no stories, and the lifecycle view treats a storyless contract as its own story.
  - From increment 3, `intake.contract_draft` given a `feature` and no `story` creates the one-to-one story in the same transaction, so every new contract has one.
  - **Why not a backfill?** A backfilled story would need a human `accepted` transition before `released -> evaluated`. That puts retroactive paperwork on already-released features, and the acceptance criteria would be invented rather than written by a product owner.
  - `sdlc-graph story adopt <contract> --actor @you` wraps an in-flight legacy contract in a story when someone wants one. It copies the contract's acceptance rows and sets `story_ac`.
- **Existing `story` nodes** (stateless today) are remapped in `012` by their contracts: no contract → `drafted`, every contract merged → `built`, otherwise `building`. **Existing `decision` nodes** go to `proposed`, never `accepted`, so no record governs anything until a named owner accepts it. Both remaps write `transition` rows with `actor = 'ci:migrate-012'`, so the change is audited.
- **touched_symbols backfill** goes through `sdlc-graph lifecycle backfill --repo R [--apply]`, which is a dry run unless `--apply` is given, and idempotent:
  - stories get the union of their contracts' keys (kept current afterwards by `stories.refresh_symbols` on every contract draft, update and link);
  - accepted records get their front-matter `paths:` resolved against the newest snapshot (bare paths are kept, since `nodes.validate_touched` accepts them);
  - findings carry symbols from their adapter at posting time, so they need no backfill.

  Re-resolving per SHA (§16.9 rule 4) is `sdlc-graph lifecycle resolve --repo R --sha S`. It runs after each `snapshot.index` and writes `symbols_resolved` evidence.

## Increment 1: Rename (`epic` → `brief`; `ticket` + `contract` → `design`)

**Goal:** every command carries its stage name, changed in one go with no aliases. **Why first:** every later skill, message and test is then written once, under the final names.

**Files:**
- `skills/epic/` → `skills/brief/` (git mv). Frontmatter `name: brief`; body `/factory:epic` → `/factory:brief`. Keep `Bash(mkdir -p docs/epics)` and the `docs/epic-*` branch pattern.
- `skills/design/SKILL.md` (new) is `skills/ticket/SKILL.md` steps 0–9. Its step 8, "Record the contract in the ledger", inlines `skills/contract/SKILL.md` steps 2–7 (contract_draft and its statuses, `current.json`, contract_update, the never-mark-ready rule). `allowed-tools` is the union of the two skills' lists, with every MCP tool in both spellings. Delete `skills/ticket/` and `skills/contract/`.
- `sdlc_graph/ledger/workflow.py:34,:51`: `CMD + "design"`, plus the docstring table. `skills/next/SKILL.md:19` and `README.md:47` → `/factory:design`. `scripts/run_headless.sh:332`: `case */design|*:design`.
- `hooks/guard_edit.py` (the `/factory:ticket` messages, including :312) together with `tests/sdlc/test_hooks.py:405`.
- `tests/sdlc/test_scripts.py:1071-1076` (`HEADLESS_FEATURE_CMD`), `:2476/:2494/:2504` (read `skills/design/SKILL.md`). `tests/sdlc/test_autonomy_review.py:494-503` → `_skill(repo, "design")`; `:542, :578-584` → `brief`. `tests/graph/test_ledger_db.py:264, :436`.
- All the other files from the sweep: templates (11 files), docs, `demo/DEMO.md`, READMEs. `CHANGELOG.md` gets a "Breaking: commands renamed" entry.
- **Keep, deliberately:** `tests/epic/`, `docs/epic/`, `scripts/lint_epic.py`, the `docs/epics/` output dir, `scripts/ticket_intake.py`, the MCP tools `epic_decompose` and `contract_*`, the routing stage `contract.design` and the node kind `epic`. These name artifacts, kinds or scripts, not commands. Renaming them would move CI paths (`plugin.json` `evals`, `test.yml:153/:156`, the pyproject comment) for no gain anyone sees.

**Graph lock-step:** none (no kind, rel or machine changes).

**Tests:** `tests/sdlc/test_rename.py` checks that:
- no file under `plugins/factory/`, `README.md` or `docs/` matches `/factory:(ticket|contract|epic)(?![\w-])`; `CHANGELOG.md` is exempt;
- `skills/{ticket,contract,epic}` do not exist and `skills/{design,brief}` do;
- `workflow.CONTRACT_STEPS["drafted"]["command"] == "/factory:design"`.

**Exit check:** all CI commands above. Demonstration: `run_headless.sh` with `/factory:design` on the sample app reaches `drafted` and stops at Decisions.

**Risks:**
- A missed reference becomes a dead command. `test_rename.py` is the net.
- The headless `case` pattern is shell, not Python, so add one `test_scripts.py` case that runs it.

**Size:** M (43 files, mechanical).

## Increment 2: Registry, schema and lifecycle

**Goal:** one registry that a test enforces, then every new kind, rel, machine and evidence kind added through it, plus `lifecycle_view` in both dialects. **Why here:** every later increment needs the kinds, machines and guards. Building the conformance test first makes each later addition fail red until it reaches every place that must know it.

**Step A: the conformance test first, green on today's kinds.** New file `tests/graph/test_registry_conformance.py`, pure where possible. Each check below names the disagreement it exposes today, and that disagreement is fixed in this same step:
1. `shapes.NODE_KINDS` equals the kind list of the newest `node_kind_check` in `sql/*.sql` (parsed; a `db` twin reads `pg_get_constraintdef`). Today they agree, since `010_repo_node.sql` has `repo`.
2. `shapes.NODE_RELS` equals `edge_rel_check` (the 002 inline CHECK). Agrees today.
3. Every machine kind is in `NODE_KINDS`, and every `NODE_KIND` has a machine or is listed in a new `shapes.STATELESS_KINDS`. **Disagreement:** no such list exists. The `transitions.py:96` comment and `models.py:37` docstring say "story, change, decision, rule" and leave out `repo`. Add `STATELESS_KINDS = ("story","change","decision","rule","repo")` and import it in both places.
4. `tests/graph/test_scaffold.py:32` and `tests/graph/test_ledger.py:190` hard-code the machine set. Replace both with `set(machines) == set(NODE_KINDS) - set(STATELESS_KINDS)`.
5. Every transition into a `PROTECTED_STATES` state has an `actor:` guard without `agent`. Green today. Also `mcp_server.PROTECTED_STATES is transitions.PROTECTED_STATES` (today a duplicate literal).
6. Every non-terminal `(kind, state)` has an entry in `workflow.CONTRACT_STEPS` or `OTHER_STEPS`. **Disagreement:** `("release","verified")` has an outgoing `rolled_back` edge but no step. Add `{"command": CMD + "rollback", "stage": None}`.
7. Every `evidence:<kind>` guard is in `evidence.KINDS` with no package imported but `ledger`. **Disagreement:** `conformance` is registered only as a side effect of importing `sdlc_graph.orchestrator` (`orchestrator/__init__.py:39`). Move it into `evidence.KINDS`.
8. `status._MARKERS`, together with the constraint check, can tell apart every `sql/NNN_*.sql` prefix. **Disagreement:** an `011` schema reports `"010"` (`status.py:116-128`). Add a marker per migration.
9. Spanner: `ddl.render()` contains a `CHECK (kind IN (...))` equal to `NODE_KINDS` and a rel CHECK equal to `NODE_RELS`.
   - **Disagreement:** the DDL drops both CHECKs on purpose (`ddl.py` docstring). Generate them from `shapes`.
   - Column parity (a `db` test): every Postgres column of `node` and `decision` is exported or listed in `export.DROPPED`. **Disagreement:** `node.contract_md` (009) and `decision.severity`/`meta` (004) are neither. Export `decision.severity`/`meta` (the `decision:<key>` guard reads `meta`) and drop `contract_md` explicitly.
10. Index: `index/chunk.py` gets `KIND_SECTIONS: dict[kind, tuple[section, ...]]`. Keys must equal `NODE_KINDS`, and a pure `chunks_for_node(row, decisions)` (factored out of `node_chunks`) must emit those sections for a fixture row of each kind. Today `rule` alone adds `body`; every other kind gets the generic `title/acceptance/decisions/scope/attrs`.
11. `lifecycle.json` maps every `(kind, state)` of every machine to exactly one stage, or lists the kind under `outside`.
12. Board (enabled in increment 8): `board.COLUMNS == lifecycle.STAGES`.
13. Docs: DESIGN §2.1's kind list lacks `repo`. Fix the doc. Do not test it, since it lives outside the plugin.

**Step B: the new kinds through the registry.**
- `sql/012_lifecycle.sql` (idempotent):
  - `ALTER TABLE node DROP CONSTRAINT IF EXISTS node_kind_check; ALTER TABLE node ADD CONSTRAINT node_kind_check CHECK (kind IN ('epic','feature','story','contract','change','release','incident','decision','rule','repo','finding','idea'));`
  - `ALTER TABLE edge DROP CONSTRAINT IF EXISTS edge_rel_check; ALTER TABLE edge ADD CONSTRAINT edge_rel_check CHECK (rel IN ('decomposes_into','depends_on','verified_by','shipped_in','supersedes','caused_by','governed_by','promoted_to'));`
  - `CREATE UNIQUE INDEX IF NOT EXISTS node_finding_fingerprint_idx ON node (repo, (attrs->>'fingerprint')) WHERE kind = 'finding';`
  - `CREATE INDEX IF NOT EXISTS decision_key_idx ON decision ((meta->>'key')) WHERE meta ? 'key';`
  - The state remap: `WITH moved AS (UPDATE node ... WHERE kind IN ('story','decision') AND state='active' RETURNING id, state) INSERT INTO transition (node_id, from_state, to_state, actor, reason) SELECT id,'active',state,'ci:migrate-012','stateless -> machine (012)' FROM moved;`. The story CASE is the one under "Migrating existing ledgers".
- `sdlc_graph/shapes.py`:
  - `NODE_KINDS += ("finding","idea")`, `NODE_RELS += ("promoted_to",)`, plus `STATELESS_KINDS`;
  - a new `EVIDENCE_KINDS` tuple that `evidence.KINDS` keys must equal (the test checks);
  - `AffectedSet` documents a new `intent` key (filled from increment 3).
- `sdlc_graph/ledger/state_machines.json` gains the four machines below. `feature` and `epic` are not changed here; that waits for increments 3–5.

```json
"story": {"states": ["drafted","ready","building","built","accepted","split","rejected"], "transitions": [
  {"from":"drafted","to":"ready","guards":["acceptance_present","owner_present","decisions_resolved","actor:human"]},
  {"from":"ready","to":"building","guards":["edge_out:decomposes_into"]},
  {"from":"building","to":"built","guards":["edge_out:decomposes_into","all_out:decomposes_into:contract=merged","story_criteria_covered"]},
  {"from":"built","to":"accepted","guards":["evidence:acceptance_signoff","criteria_signed_off","actor:human"]},
  {"from":"drafted","to":"split","guards":["edge_in:supersedes","actor:human"]},
  {"from":"ready","to":"split","guards":["edge_in:supersedes","actor:human"]},
  {"from":"drafted","to":"rejected","guards":["actor:human"]},
  {"from":"ready","to":"rejected","guards":["actor:human"]},
  {"from":"built","to":"rejected","guards":["actor:human"]}]},
"decision": {"states": ["proposed","accepted","superseded","rejected"], "transitions": [
  {"from":"proposed","to":"accepted","guards":["record_file_present","owner_present","actor_is_owner","decisions_resolved","actor:human"]},
  {"from":"proposed","to":"rejected","guards":["actor_is_owner","actor:human"]},
  {"from":"accepted","to":"superseded","guards":["edge_in:supersedes","all_in:supersedes:decision=accepted","actor:human"]}]},
"finding": {"states": ["open","triaged","accepted","fixing","fixed","verified","closed","duplicate","false_positive","risk_accepted"], "transitions": [
  {"from":"open","to":"triaged","guards":["evidence:finding_triage","decision:triage"]},
  {"from":"triaged","to":"accepted","guards":["owner_present","actor:human"]},
  {"from":"triaged","to":"duplicate","guards":["edge_out:supersedes","actor:human"]},
  {"from":"triaged","to":"false_positive","guards":["decision:dismissal","actor:human"]},
  {"from":"triaged","to":"risk_accepted","guards":["decision:risk_acceptance","actor:human"]},
  {"from":"risk_accepted","to":"open","guards":["risk_expired"]},
  {"from":"accepted","to":"fixing","guards":["edge_in:caused_by"]},
  {"from":"fixing","to":"fixed","guards":["edge_in:caused_by","all_in:caused_by:contract=merged"]},
  {"from":"fixed","to":"verified","guards":["evidence:regression_test","evidence:fingerprint_absent.absent==true","evidence:fingerprint_absent.binding","actor:human|ci"]},
  {"from":"verified","to":"closed","guards":["actor:human"]},
  {"from":"fixing","to":"triaged","guards":[]},
  {"from":"fixed","to":"triaged","guards":[]},
  {"from":"verified","to":"open","guards":[]}]},
"idea": {"states": ["captured","shortlisted","promoted","parked","rejected"], "transitions": [
  {"from":"captured","to":"shortlisted","guards":["problem_present","owner_present","actor:human"]},
  {"from":"shortlisted","to":"promoted","guards":["edge_out:promoted_to","actor:human"]},
  {"from":"captured","to":"parked","guards":["actor:human"]},
  {"from":"shortlisted","to":"parked","guards":["actor:human"]},
  {"from":"parked","to":"captured","guards":["actor:human"]},
  {"from":"captured","to":"rejected","guards":["actor:human"]},
  {"from":"shortlisted","to":"rejected","guards":["actor:human"]}]}
```

- **Simple-guard semantics** (in `transitions.py`):
  - `owner_present`: `attrs.owner` is a human `@handle`.
  - `actor_is_owner`: the acting handle equals `attrs.owner`, compared case-insensitively.
  - `record_file_present`: `path` and `commit_sha` are set and `attrs.record_type` is one of `pdr`, `adr`, `ddr`.
  - `story_criteria_covered`: the union of child contracts' `acceptance[].story_ac` includes every `node.acceptance[].id`.
  - `criteria_signed_off`: the newest `acceptance_signoff.criteria` includes every `node.acceptance[].id`.
  - `risk_expired`: the `decision:risk_acceptance` row's `due` is before today.
  - `problem_present`: `attrs.problem`, `attrs.who` and `attrs.evidence` are all non-empty.
- **`ledger/evidence.py`** gets new `KINDS` entries, each validated in `validate()`:
  - `acceptance_signoff {criteria: [non-empty ids], release: slug, environment, checked_by: @handle}`
  - `design_review {path, pr_url, reviewer, author, merge_sha}` (`merge_sha` uses the `_GIT_SHA` rule)
  - `finding_triage {severity ∈ critical|high|medium|low, likelihood ∈ likely|possible|unlikely, owner, proposed_by}`, with optional `cvss` (a number from 0 to 10) and `duplicate_of`
  - `regression_test {test, commit}`
  - `fingerprint_absent {fingerprint, source, sha, absent: bool}`
  - `finding_seen {source, sha, count >= 1}`
  - `symbols_resolved {sha, resolved: {old: new}, vanished: [keys]}`
  - `conformance`, moved in from the orchestrator
- **`ledger/decisions.py`:** `propose(..., key=None)` stores `meta.key`, and `key == "risk_acceptance"` requires `due`.
- **`ledger/lifecycle.json`:**
  - `{"stages": ["ideate","brief","design","build","review","accept","done"], "map": {kind: {state: stage}}, "killed": {kind: [states]}, "derived": {"epic": ["accepted","in_progress"]}, "outside": ["incident","finding","release"]}`.
  - Mappings follow §16.1: contract `drafted→design`, `ready/tests_frozen/implementing→build`, `verified/in_review/approved→review`, `merged→done`; story `building→build`, `built→accept`.
  - Killed exits: idea `parked/rejected`, epic `rejected`, story `split/rejected`, decision `rejected/superseded`.
  - A kind under `derived` takes its stage from `parent_stage(children)`.
- **`ledger/lifecycle.py` (new):**
  - `STAGES`;
  - `stage_of(kind, state) -> str | None`;
  - `tree(conn, slug) -> rows`, a recursive CTE over `decomposes_into` and `promoted_to`, depth at most 6, with a cycle guard through `path uuid[]`;
  - `lifecycle_view(conn, slug) -> {slug, kind, state, stage, killed, progress, children: [...]}`;
  - **the TODO stub `parent_stage`** (below);
  - `ledger/__init__.py: LIFECYCLE_PATH`.
- **`spanner/ddl.py`:** CHECKs generated from `shapes`; `decision` table gains `severity STRING(MAX), meta JSON`. **`spanner/export.py`:** `_TABLES["decision"]` exports them; `QUERIES["lifecycle_tree"]` = `MATCH (r:Node {slug: {slug}})-[e:Rel WHERE e.rel IN ('decomposes_into','promoted_to')]->{1,6}(n:Node) RETURN DISTINCT n.slug, n.kind, n.state`. Mirror it in `spanner/queries.gql` as query 4.
- **`status.py`:** a marker that recognises `012` from `node_kind_check` containing `'finding'`. `tests/graph/test_mcp.py:348` changes `"010"` → `"012"`.
- **`mcp_server.py`:** add `lifecycle_view(slug)` to `READ_ONLY_TOOLS` (15 read, 9 write, 24 total). Update `test_mcp.py:54,:205`. Add `templates/settings.project.json` allow entries `mcp__plugin_factory_graph__lifecycle_view` and `mcp__graph__lifecycle_view`, and extend the `"mcp__*"` note string. CLI: `sdlc-graph lifecycle view|backfill|resolve`.
- **`PROTECTED_STATES`** adds the states the new machines guard as human-only: `accepted, rejected, split, superseded, risk_accepted, false_positive, duplicate, shortlisted, promoted, parked`. All of them already carry `actor:human`; check 5 keeps them that way.

```python
# sdlc_graph/ledger/lifecycle.py
class ChildStage(TypedDict):
    slug: str; kind: str; state: str; stage: str | None; killed: bool

def parent_stage(children: list[ChildStage]) -> tuple[str, dict[str, Any]]:
    """TODO(product): the stage a parent (epic in accepted/in_progress, feature) shows on the board, and its
    progress, e.g. ("build", {"done": 3, "total": 5, "label": "3/5 contracts merged", "straggler": "ct-x"}).
    DESIGN 16.1 proposes: the least advanced OPEN child's stage plus a count; killed children count as done.
    Decide: (1) least-advanced vs median child (one straggler parks an epic in Design for weeks);
    (2) killed children in the denominator or not ("3/5" vs "3/3 of what remains"); (3) immediate children
    or leaves (an epic over features over stories over contracts: whose count is the label?); (4) no children:
    the parent's own state, or "brief"; (5) all children killed: "done" flagged killed vs shipped, which the
    funnel must tell apart; (6) whether an escalated (P0/P1) child pins the parent regardless of stage."""
    raise NotImplementedError("parent_stage is a product rule; see docstring")
```

When the stub raises, `lifecycle_view` catches `NotImplementedError` and returns `stage: None, progress: {"todo": "parent_stage"}`, so the factory keeps working.

**Graph lock-step checklist:**

| Place | Change |
|---|---|
| registry (`shapes.py`) | `finding`, `idea`, `promoted_to`, `STATELESS_KINDS`, `EVIDENCE_KINDS` |
| CHECK constraints | `012` |
| state machines | story, decision, finding, idea |
| `lifecycle.json` | all four |
| `KIND_SECTIONS` | story `owner`; finding `fingerprint/severity/summary`; idea `problem/who/evidence`; decision `record_type/body` (filled in their increments; the sections are declared now) |
| Spanner | generated CHECKs; `lifecycle_tree` |
| `node_neighbours` | validates `rel` against `NODE_RELS`, so `promoted_to` works with no code change |
| `graph_impact`, `intake_context` | unchanged |
| board | check 12 is `xfail(strict=True)` until increment 8 |

**Tests:**
- `test_registry_conformance.py` (above).
- `tests/graph/test_guards_lifecycle.py`: pure cases for each new guard, including that `all_out` holds on zero children.
- `tests/graph/test_lifecycle.py` (db): the `012` remap and its transition rows; re-running `012` is a no-op; `lifecycle_view` on a fixture of idea → epic → feature → story → contract. `test_parent_stage_examples` is `xfail(raises=NotImplementedError, strict=True)`, so it fails loudly once the rule exists and its marker should come off.
- `tests/graph/test_lifecycle_parity.py` (spanner + db): `lifecycle.tree` equals `QUERIES["lifecycle_tree"]` on the same fixture.

**Exit check:** the CI commands, plus with an emulator `make spanner-up` and `pytest tests/graph/test_lifecycle_parity.py -m spanner`. Demonstration: `sdlc-graph lifecycle view <epic> --json` on the demo ledger prints the tree with a stage per node.

**Risks:**
- The `012` remap touches live rows. It is idempotent (`WHERE state='active'`) and audited through transition rows.
- The emulator may not support CHECK constraints or `{1,6}`. The parity test shows it, and the fallback is DDL CHECKs only when `ddl.render(checks=False)` is not requested.
- New machines change no existing kind.

**Size:** L.

## Increment 3: Stories

**Goal:** stories become the unit of acceptance in `/factory:design`, in the orchestrator and in `/factory:accept`. **Why here:** records, the design doc and findings all hang off stories.

**Files:**
- **`ledger/stories.py` (new):**
  - `draft(conn, *, slug, title, repo, feature, acceptance, owner, path, actor)`, where `path` is the story's section of its brief, `docs/epics/<slug>.md#S<n>`, and `acceptance` keeps only each criterion's id and a sha256 of its text (`node.acceptance = [{id, hash}]`), never the text;
  - `drift(conn, story, root) -> [changed ids]`: re-reads the section at HEAD and compares the hashes; a change moves a `ready` story back to `drafted` (actor `ci:drift`, reason "criteria changed in the file") so its owner re-confirms it. It runs in `scripts/verify.py` and in the CI gate, like `freeze_tests.py --check`;
  - `refresh_symbols(conn, story)`;
  - `coverage(conn, story) -> uncovered ids`;
  - `rollup(conn, slug, actor) -> [GuardResult]`, which moves `ready → building` and `building → built` when the guards hold (called after a contract transition);
  - `accept(conn, story, *, actor, criteria, release, environment)`, which is human only: it refuses unless `actor_class == "human"` and the release is `verified`, writes `acceptance_signoff`, then transitions;
  - `adopt(conn, contract, actor)`.
- **`ledger/intake.py: contract_draft`** gains `story=`. Given a feature and no story, it creates `story-<slug>` from the contract's acceptance and sets each contract row's `story_ac`. It refreshes the story's symbols.
- **`state_machines.json` feature:**
  - `building→released` adds `all_out:decomposes_into:story=built|accepted|split|rejected`;
  - `released→evaluated` adds `all_out:decomposes_into:story=accepted|split|rejected`.
- **`ledger/workflow.py` `OTHER_STEPS`:**
  - `("story","drafted"): design/contract.design`;
  - `("story","ready"): /factory:orchestrate`;
  - `("story","building"): None`;
  - `("story","built"): /factory:accept`.
  - `HUMAN_INVOKED` adds `/factory:accept`.
- **`ledger/metrics.py:270`**: the feature→contract join also walks feature→story→contract.
- **`ledger/nodes.py: overlaps`** gains `kinds=("contract",)` and `open_states=None`.
- **Orchestrator:**
  - `orchestrator/loop.py: contracts_of` returns direct contracts plus contracts of the feature's stories.
  - `topo_order` lifts story `depends_on`: every contract of story A depends on every contract of story B when A depends on B. Cycles still raise `DependencyCycle`.
  - `tick` moves a story `ready→building` when it first dispatches one of its contracts (as `loop.py:283` does for features), and calls `stories.rollup(..., ACTOR)` for each story whose contracts all reached `merged` (refinery mode).
  - `orchestrator/steps.py` adds `STORY_HUMAN_STATES = frozenset({"drafted","built"})`, which are reported and never dispatched.
- **`sdlc_graph/cli.py`:** `story draft|accept|adopt|rollup`. `node transition` calls `stories.rollup` after any contract → `merged`.
- **`mcp_server.py`:** `story_draft` goes in WRITE (10 write, 25 total); `contract_draft` gains `story`. Neither tool can accept a story. `evidence_add` refuses `HUMAN_EVIDENCE = {"acceptance_signoff"}`.
- **`index/search.py: intake_context`** gains `kind="contract"`, and `similar_open(kind="story")` returns story duplicates. `graph_impact` (MCP) adds `intent.contracts` and `intent.stories` from `nodes.overlaps(kinds=("contract","story"))`.
- **Skills:**
  - `skills/design/SKILL.md`: story first (criteria in product language, owner), then the contract(s). Each contract acceptance row names its `story_ac`.
  - `skills/accept/SKILL.md` (new, `disable-model-invocation: true`): lists the `built` stories of a released feature with their criteria, checks each in the released system with the human, and prints the `sdlc-graph story accept ... --actor @you` command for the human's terminal. The MCP server acts as an agent, so the skill never performs the acceptance itself.
- **No story file or template.** A story is a `### S<n> · <title>` section of `docs/epics/<slug>.md`, in the agent-ready story format `/factory:brief` already writes (`docs/epic/agent-ready.md`). `scripts/lint_epic.py` gains a check that every story heading carries a unique, stable `S<n>` id, since the ledger's `path` anchors on it.
- **`templates/settings.project.json`:**
  - allow `story_draft` in both MCP spellings;
  - deny `Bash(uv run * sdlc-graph story accept *)`, `Bash(* sdlc-graph story accept *)`, `Bash(uv run * sdlc-graph node transition * accepted *)` and `Bash(* sdlc-graph node transition * accepted *)`, matching the `merged`/`released` pattern at lines 250–259.

**Graph lock-step checklist:**
- `KIND_SECTIONS["story"]` is emitted, and the chunker indexes `owner`.
- Spanner `QUERIES["story_contracts"]` (story → contract states) is mirrored in `queries.gql`.
- `graph_impact` returns `intent.stories`; `intake_context` returns story duplicates.
- The conformance test asserts the feature machine's new guards parse, that every `story` state is in `lifecycle.json`, and that `story_contracts` exists.

**Tests:**
- `tests/graph/test_stories.py` (db):
  - a one-to-one draft creates story and contract with `story_ac` set;
  - a two-contract story is not `built` until both are `merged`, and `story_criteria_covered` refuses partial coverage;
  - an agent `accepted` is refused with `actor:not_agent`;
  - accepting without a verified release is refused;
  - a legacy feature with direct contracts still releases.
- `tests/graph/test_orchestrator_deps.py`: story `depends_on` ordering; rollup by `agent:orchestrator`.
- `tests/graph/test_mcp.py`: tool counts, and `evidence_add` refusing `acceptance_signoff`.
- `tests/graph/test_lifecycle_parity.py::story_contracts`.

**Exit check:** the CI commands. Demonstration: on the demo ledger, one story with two contracts is orchestrated to merged (refinery) and becomes `built`; `sdlc-graph story accept --actor @po` takes it to `accepted`; `lifecycle view` shows Accept → Done.

**Risks:**
- The orchestrator's set of contracts changes. Direct contracts stay included, and the existing `test_orchestrator*.py` suite must pass unchanged.
- In default mode (done at `in_review`) the loop never sees `merged`. The rollup also runs from the CLI merge transition, and from CI when it records `merge_sha`.

**Size:** L.

## Increment 4: Decision records

**Goal:** PDRs, ADRs and DDRs as `decision` nodes. Agents propose them, owners accept them, the accepted file is frozen, and `intake_context` returns the records that govern a contract. **Why here:** the design doc's required-when rule (increment 5) counts promoted records.

**Files:**
- **`ledger/records.py` (new):**
  - `propose(conn, *, slug, title, repo, record_type, path, commit_sha, owner, governs: [slug], paths: [glob], supersedes=None, actor)` creates the node in `proposed`, the `governed_by` edges and an optional `supersedes` edge;
  - `accept(conn, slug, actor)` is human only, and transitions the superseded record in the same transaction;
  - `governing(conn, repo, paths) -> [record]` returns accepted, non-superseded records whose `attrs.paths` match, using `search.globs_match`;
  - **the TODO stub `should_promote`** (below).
- **`mcp_server.py: decision_propose`** gains optional `record_type, path, paths, governs, owner, supersedes`. With a `record_type` it calls `records.propose`. Tool count is unchanged. The `contract_draft` and `story_draft` results gain `promote: [{decision, record_type}]` from `should_promote`. When the stub raises, the result is `promote: null, promote_todo: true`.
- **`index/search.py: intake_context`** returns `records`; `index/chunk.py` indexes the record file's text as `body`, the way `sync_rules` does for rules. **`graph_impact`** adds `intent.records`.
- **`state_machines.json` epic:** `drafted→accepted` adds `all_out:governed_by:decision=accepted` (the PDRs are accepted).
- **The frozen accepted file:**
  - `records.accept` (CLI) writes `docs/decisions/accepted.json` `{path: sha256}`;
  - `hooks/guard_edit.py` refuses Edit and Write on any path listed there (rule `record-accepted`, with a message pointing to "write a superseding record");
  - `lib/sdlc.py: ALWAYS_PROTECTED` adds `docs/decisions/accepted.json`;
  - new `scripts/check_records.py` fails CI when a listed file's hash differs, which covers humans editing outside Claude.
- **CLI:** `sdlc-graph record propose|accept|reject|list [--legacy]`.
- **Templates:** `templates/decisions/{pdr,adr,ddr}.template.md` (fields per §16.3, front matter `paths:` and `owner:`). **Skills:** `skills/design/SKILL.md` runs the promotion step; `skills/brief/SKILL.md` drafts PDRs. **Settings:** deny `* sdlc-graph record accept *` in both Bash spellings.
- **CI:** a `check_records.py` step in `.github/workflows/test.yml` and in `templates/ci/*.yml`.

```python
# sdlc_graph/ledger/records.py
RecordType = Literal["pdr", "adr", "ddr"]

def should_promote(decision: dict[str, Any], node: dict[str, Any]) -> RecordType | None:
    """TODO(product): whether a resolved decision row on `node` (story/contract/feature) becomes a record, and
    which type. DESIGN 16.3: promote when hard to reverse, when it spans more than one feature, or when it
    touches money, security, data or customer-visible behaviour; everything else stays a row.
    Inputs: decision (question, options, choice, recommendation, severity, meta) and node (kind, tier,
    capabilities.paths, touched_symbols). Trade-offs: reusing transitions.SENSITIVE is consistent with the ready
    check but over-promotes ("data" matches almost anything); "spans features" needs a graph query (owners or
    modules across the paths) that this pure function cannot make, so pass it in or leave it out; reversibility
    has no clean signal (schema migration, public route, package added?); precedence when several apply (a
    customer-visible data change: ddr or pdr?); a deterministic rule versus a utility-tier proposal (models
    propose, the owner accepts either way, so false positives cost paperwork, false negatives bury decisions)."""
    raise NotImplementedError("should_promote is a product rule; see docstring")
```

**Graph lock-step checklist:**
- `KIND_SECTIONS["decision"]` is emitted (`record_type`, `body`, `scope`).
- Spanner `QUERIES["governing_records"]`: `(n:Node {slug})-[:Rel {rel:'governed_by'}]->(d:Node {kind:'decision', state:'accepted'})`, minus any `d` with an incoming `supersedes`.
- `node_neighbours` needs no change. `intake_context` and `graph_impact` return `records`.
- The conformance test asserts every `record_type` has a template.

**Tests:**
- `tests/graph/test_records.py` (db): only the owner accepts; an agent is refused; superseding moves the old record in one transaction; `intake_context` returns the governing record for matching paths and never a superseded one.
- `tests/sdlc/test_hooks.py`: the edit guard refuses an accepted record and allows a proposed one.
- `tests/sdlc/test_scripts.py`: `check_records.py` catches an edited accepted record.
- `test_should_promote_examples`: `xfail(strict, raises=NotImplementedError)`.
- Parity for `governing_records`.

**Exit check:** the CI commands plus `python3 -I scripts/check_records.py --root .`. Demonstration: propose an ADR from `/factory:design`, the owner accepts it from the CLI, then an Edit on the file is denied and a new contract on its paths lists it under `records`.

**Risks:**
- The `accepted.json` manifest is a second list beside the ledger. `check_records.py` cross-checks the two when the graph is reachable.
- Legacy `decision` nodes become `proposed`: `record list --legacy` shows them.

**Size:** M.

## Increment 5: Feature design document

**Goal:** a per-feature RFC when the required-when rule says so, a `design_decompose` that the code graph drives, and the `design_review` guard. **Why here:** it needs stories (increment 3) and promoted records (increment 4).

**Files:**
- **`ledger/design_doc.py` (new):**
  - `required(conn, feature) -> {required, reasons}`: more than one contract under the feature (direct or through stories), any contract at tier `high`, or any record that governs it and was promoted from a row;
  - `parse_breakdown(text) -> rows`;
  - `design_decompose(conn, feature, root=None)`:
    - takes each row's allowed paths from `impact.affected` over the paths it names;
    - proposes `depends_on` from pairwise `nodes.symbols_overlap`;
    - **refuses** (`status: refused, unordered: [[a, b, symbols]]`) when an overlapping pair has no `depends_on` either way.
  - Nothing is created; it proposes, as `ledger/decompose.py` does for epics.
- **`transitions.py`:** the simple guard `design_review_if_required`. When `required`, the newest `design_review` must be binding, reviewer and author must be non-empty and different, and `payload.path == attrs.design_path`. Feature `proposed→ready` adds it.
- **`mcp_server.py`:** `design_decompose(slug)` goes in READ_ONLY (16 read, 26 total), with settings allow entries in both spellings.
- **`templates/design.template.md`:** the five sections of §16.4, with the breakdown table columns `contract | story | depends_on | allowed paths | tier | records`. **`templates/ci/*.yml`:** a step that posts `design_review` (binding, OIDC) on merge of a PR touching `docs/design/**`. **`skills/design/SKILL.md`:** the feature mode writes stories, records, the doc when required, then calls `design_decompose`; a human creates each proposed contract.
- **`spanner/export.py`:** `QUERIES["feature_contracts"]` (feature → story? → contract with `depends_on`).

**Graph lock-step checklist:**
- No new kind or rel.
- `KIND_SECTIONS["feature"]` adds `design`, the doc path.
- Spanner `feature_contracts` gets a parity test.
- The conformance test asserts `design_review` is in `EVIDENCE_KINDS` and that the new guard parses.

**Tests:** `tests/graph/test_design_doc.py`:
- the required-when rule on the three triggers and on a single low contract (not required);
- `design_decompose` refuses an unordered overlap and accepts it once `depends_on` is added;
- the guard refuses local `design_review` evidence and author == reviewer.

Plus parity for `feature_contracts`.

**Exit check:** the CI commands. Demonstration: a two-contract feature on the sample app is refused `ready` until CI posts `design_review`.

**Risks:**
- `impact.affected` returns more paths than a contract needs. The proposal is a starting point a human edits, never written by itself.

**Size:** M.

## Increment 6: Findings

**Goal:** one finding queue for every source, deduped by fingerprint, with response times, and verified only by the fingerprint being absent. **Why here:** a fix is a story or contract (increment 3) that a record may govern (increment 4).

**Files:**
- **`ledger/findings.py` (new):**
  - `post(conn, *, repo, source, fingerprint, title, severity=None, likelihood=None, cvss=None, symbols, paths, payload, actor)`. The same `(repo, fingerprint)` adds `finding_seen` and reopens a `fixed`/`verified`/`closed` finding to `open`; it never inserts twice (the unique index from `012`).
  - `queue(conn, repo, state=None, overdue=False)`.
  - `expire(conn, today)` moves `risk_accepted→open` with actor `ci:cron`.
  - `verify(conn, slug, sha, report, source)` writes `fingerprint_absent`.
  - `recurrence(conn, finding)`: a second finding with the same rule class opens a P2 decision "rule or checker for `<rule>`" (§16.5 "never again").
  - `finding_policy.json`: `{"response_days": {"critical": 7, "high": 30, "medium": 90, "low": null}}`.
- **`sdlc_graph/findings/adapters.py` (new):** one parser per source, each yielding `post()` kwargs from JSON the source already writes:
  - ratchet keys from the verify stamp (the key is the fingerprint);
  - `security_assess.py --json-out`;
  - `/factory:assess` and `maturity.py`;
  - `capability_findings.jsonl` (`hooks/observe_capabilities.py`, `scripts/verify.py`);
  - reviewer and security-reviewer output, fingerprinted `<rule>|<path>|<symbol>`;
  - a failed `ci_run`.
- **`ledger/lifecycle.py: resolve`:** a vanished symbol on a finding in `fixing`/`fixed` moves it to `triaged`, never to `closed` (rule 4).
- **`ledger/metrics.py: snapshot`** adds `findings: {by_state, past_response_time}`.
- **`mcp_server.py`:** `finding_post` goes in WRITE and `finding_list` in READ (17 read, 11 write, 28 total). An agent proposes triage with `evidence_add(kind="finding_triage")` plus `decision_propose(key="triage")`. `accepted`, `duplicate`, `false_positive`, `risk_accepted` and `closed` are already protected.
- **`index/search.py: intake_context`** returns `findings` (open findings overlapping the touched symbols); `graph_impact` adds `intent.findings`.
- **CLI:** `sdlc-graph finding post|list|triage|accept|dismiss|accept-risk|verify|expire`. **Skills:**
  - `skills/findings/SKILL.md` (new): the triage queue, run through `agents/triage.md` (utility tier); it proposes triage and never accepts or dismisses.
  - `skills/{verify,review,security,assess}/SKILL.md` call `finding_post`.
- **Settings:** allow `finding_post` and `finding_list` in both MCP spellings; deny `* sdlc-graph finding accept|dismiss|accept-risk *` in both Bash spellings.

**Graph lock-step checklist:**
- `KIND_SECTIONS["finding"]` is emitted.
- Spanner `QUERIES["findings_on_node"]`: `MATCH (c:Node {slug}), (f:Node {kind:'finding'}) WHERE EXISTS (SELECT 1 FROM UNNEST(f.touched_symbols) k WHERE k IN UNNEST(c.touched_symbols))`, compared by exact key. The Postgres side also uses exact `&&` for the parity case (the fuzzy path containment in `symbols_overlap` stays Postgres-only, and the docstring says so).
- `node_neighbours` walks `caused_by` (no change needed).
- The conformance test asserts each adapter's source name is in `finding_policy.json` and that every `finding` state is under `outside`.

**Tests:** `tests/graph/test_findings.py`:
- a re-posted fingerprint counts instead of inserting;
- a fix the scanner still reports stays `fixed`;
- local `fingerprint_absent` does not verify;
- risk acceptance needs a `due` and reopens after it;
- a vanished symbol goes back to triage;
- an agent cannot dismiss.

Also `tests/graph/test_finding_adapters.py` (each parser on a fixture file) and parity for `findings_on_node`.

**Exit check:** the CI commands. Demonstration: a planted `route-auth|GET /admin` ratchet key becomes a finding, a contract `caused_by` it merges, the CI rerun is clean, and the finding goes to `verified`.

**Risks:**
- Noisy sources flood the queue. Posting is deduped and low severity has no deadline.
- GQL `EXISTS` subquery support in the emulator. The parity test shows it; the fallback is an `UNNEST` join.

**Size:** L.

## Increment 7: Ideas

**Goal:** a cheap, deduped place for ideas, and their promotion to epics. **Why here:** promotion needs `/factory:brief` (increment 1) and `promoted_to` (increment 2). It changes nothing upstream.

**Files:**
- **`ledger/ideas.py` (new):**
  - `capture(conn, *, slug, title, repo, problem, who, evidence, source, actor)` dedupes against open ideas and epics with `search.similar_open(kind=...)` and returns `duplicate` like `contract_draft` does;
  - `promote(conn, idea, epic, actor)` is human only: the `promoted_to` edge, then the transition.
- **`mcp_server.py`:** `idea_capture` goes in WRITE (12 write, 29 total), with allow entries in both spellings.
- **CLI:** `sdlc-graph idea capture|shortlist|promote|park|reject`. Deny `* sdlc-graph idea promote *` and `* sdlc-graph idea shortlist *` in both Bash spellings.
- **Skills and templates:** `skills/ideate/SKILL.md` (new) asks for the problem, the person who has it and the evidence, never the solution, and writes `docs/ideas/<slug>.md`. `skills/brief/SKILL.md` gains `--from-idea <slug>` (reads the idea and prints the promote command). New `templates/idea.template.md`.
- **`ledger/metrics.py`:** the funnel counts ideas.

**Graph lock-step checklist:**
- `KIND_SECTIONS["idea"]` is emitted.
- Spanner `QUERIES["idea_funnel"]`: `(i:Node {kind:'idea'})-[:Rel {rel:'promoted_to'}]->(e)-[:Rel {rel:'decomposes_into'}]->{0,3}(n)`.
- `lifecycle_tree` already follows `promoted_to`; `intake_context` dedupes ideas.
- The conformance test asserts `promoted_to` is used by at least one guard and one query.

**Tests:** `tests/graph/test_ideas.py` (dedupe against an epic, agent promotion refused, the lifecycle view follows the idea into the epic) and parity for `idea_funnel`.

**Exit check:** the CI commands. Demonstration: `/factory:ideate` → shortlist → `/factory:brief --from-idea` → promote; `lifecycle view <idea>` shows Brief.

**Risks:** dedupe noise on short text. It uses the same distance as contracts (`DEDUPE_DISTANCE`) and is advisory only.

**Size:** S.

## Increment 8: The board and the funnel

**Goal:** `sdlc-graph board` and the funnel metrics, both over the lifecycle view. **Why last:** it reads every kind the earlier increments added.

**Files:**
- **`sdlc_graph/board.py` (new):** `COLUMNS = lifecycle.STAGES`; `board(conn, repo, root=None) -> {columns: {stage: [card]}}`. A card is the top-level item with `parent_stage` progress. Killed items appear under Done, flagged.
- **`ledger/metrics.py`:** `funnel {ideas, briefs, shipped, outcome_kept, killed_by_stage}` and median days per stage, computed from transitions through `stage_of`.
- **`mcp_server.py`:** `lifecycle_board(repo)` goes in READ (18 read, 30 total), with allow entries in both spellings. CLI `sdlc-graph board [--repo] [--json]`.
- **`docs/graph.md`:** the board section. The DESIGN §15 console reads the same tool.
- **`spanner/export.py`:** `QUERIES["funnel"]`.

**Graph lock-step checklist:** conformance check 12 loses its xfail (`board.COLUMNS == STAGES`, and every kind in `lifecycle.json` that is not under `outside` renders), plus parity for `funnel`.

**Tests:** `tests/graph/test_board.py` (a fixture with one item per stage, killed counted as Done, and `parent_stage` progress once the user has written the rule; until then the card shows `todo`) and `test_metrics_survival.py` extended for the funnel.

**Exit check:** the CI commands. Demonstration: `sdlc-graph board --repo sample-app` on the demo ledger shows seven columns.

**Risks:** the board shows nothing useful until `parent_stage` exists. That is intended and visible.

**Size:** M.

## Orchestrator and autonomy (§12.2, §14)

- **Orchestrator changes, all in increment 3:**
  - `orchestrator/loop.py: contracts_of` (through stories) and `topo_order` (story `depends_on` lifted onto contracts);
  - `tick`: story `ready→building` on first dispatch and `building→built` by `agent:orchestrator` (deterministic, not protected);
  - `orchestrator/steps.py: STORY_HUMAN_STATES`.
  - `HUMAN_STATES` for contracts is unchanged.
- **No agent accepts, dismisses or promotes.** `PROTECTED_STATES` (single definition) gains `accepted, rejected, split, superseded, risk_accepted, false_positive, duplicate, shortlisted, promoted, parked` on top of `approved, merged, released, closed`. Each of those transitions also carries `actor:human` (or `actor:human|ci`), and conformance check 5 pins that pairing.
- **Human-only paths outside transitions:**
  - `decisions.resolve` (exists);
  - `stories.accept`, `records.accept`, `ideas.promote` and `findings.accept/dismiss/accept-risk` check `actor_class == "human"` themselves;
  - MCP `evidence_add` refuses `acceptance_signoff`;
  - the Bash deny rules in `settings.project.json` stop an agent from running those CLI commands.
- **Allowed for agents:** `verified` (finding, human or CI only by guard), `fixing`, `fixed`, `triaged` (only after a human resolved `decision:triage`), `built`, `building`, `open` (reopen).

## Open questions

1. *Settled:* a story's content is a section of its brief's `docs/epics/<slug>.md`; the ledger holds its state, `path#S<n>`, SHA and per-criterion hashes, with a drift check (DESIGN §16.2).
2. *Settled:* briefs stay in `docs/epics/`; ideas go to `docs/ideas/`.
3. *Settled:* Spanner keeps one `Node` label and one `Rel` label, with the kind and rel CHECKs generated from `shapes.py`.
4. *Settled:* CI may record a story `building → built` once every contract is `merged`. It is a deterministic fact, so the `workflow_run` evidence job (fix/oidc-binding) records it as a `ci:` actor. `accepted` stays human-only.
5. **Legacy `decision` nodes:** do they all become `proposed` (planned), or does a human get a bulk "accept as-is" step for nodes that already carry `governed_by` edges?
6. **When a risk acceptance expires,** does the finding reopen to `open` (planned) or to `triaged`, keeping its confirmed severity and owner?
7. **"Low: next debt epic":** one standing debt epic per repo or per quarter, and who creates it?
8. **Release `rolled_back`:** which lifecycle stage is it (planned: Accept, outside the funnel)? Does a rollback reopen story acceptance?

### Critical Files for Implementation
- /Users/ishprasad/caylent/demo/lathe/plugins/factory/sdlc_graph/shapes.py
- /Users/ishprasad/caylent/demo/lathe/plugins/factory/sdlc_graph/ledger/transitions.py
- /Users/ishprasad/caylent/demo/lathe/plugins/factory/sdlc_graph/ledger/state_machines.json
- /Users/ishprasad/caylent/demo/lathe/plugins/factory/sdlc_graph/orchestrator/loop.py
- /Users/ishprasad/caylent/demo/lathe/plugins/factory/sdlc_graph/mcp_server.py
