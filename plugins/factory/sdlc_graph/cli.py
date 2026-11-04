"""``sdlc-graph`` command line.

Structure (owners edit ONLY the body of their own ``register_<area>`` function):

* ``register_db``        scaffold (implemented)
* ``register_codegraph`` G: index, delta, verify-snapshot, query
* ``register_ledger``    L: node, evidence, decision, workflow, run, metrics
* ``register_impact``    I: impact, capabilities
* ``register_hooks``     H: hooks
* ``register_spanner``   S: spanner
* ``register_mcp``       M: mcp

Conventions every handler follows:

* Add leaf commands with :func:`add_command`; it adds ``--json`` and binds the handler.
* A handler takes the parsed ``argparse.Namespace`` and RETURNS a JSON-serialisable result; it
  does not print. :func:`main` prints it (``--json``: indented JSON; otherwise a table/list).
* A handler gets its connection from ``sdlc_graph.db.connect()`` (honours DATABASE_URL and
  SDLC_SCHEMA) and closes it (``with db.connect() as conn:``).
* Errors are exceptions, mapped to exit codes by :func:`main`: ValueError/PermissionError -> 2,
  LookupError -> 4, NotImplementedError -> 3, psycopg.OperationalError -> 5. A handler that wants
  a non-zero exit with a normal result returns ``CommandResult(result, exit_code)``.
* JSON-valued options (``--payload``, ``--capabilities``) are parsed with :func:`json_arg`.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import decimal
import json
import sys
import uuid
from typing import Any, Callable

Handler = Callable[[argparse.Namespace], Any]


@dataclasses.dataclass
class CommandResult:
    """A handler result with an explicit exit code (e.g. ``verify-snapshot`` that found a diff)."""

    result: Any
    exit_code: int = 0


# --------------------------------------------------------------------------- helpers (scaffold)


def json_arg(text: str) -> Any:
    """argparse ``type=`` for JSON options. ``@path`` reads the JSON from a file."""
    try:
        if text.startswith("@"):
            with open(text[1:], encoding="utf-8") as fh:
                return json.load(fh)
        return json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise argparse.ArgumentTypeError(f"not valid JSON: {exc}") from exc


def add_command(subparsers: argparse._SubParsersAction, name: str, handler: Handler, help: str) -> argparse.ArgumentParser:
    """Add a leaf command with a ``--json`` flag bound to ``handler``. Returns its parser so the
    caller can add arguments."""
    parser = subparsers.add_parser(name, help=help, description=help)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.set_defaults(func=handler)
    return parser


def add_group(subparsers: argparse._SubParsersAction, name: str, help: str) -> argparse._SubParsersAction:
    """Add a command group (``sdlc-graph node ...``) and return its sub-parsers."""
    parser = subparsers.add_parser(name, help=help, description=help)
    group = parser.add_subparsers(dest=f"{name}_command", metavar="COMMAND")
    group.required = True
    return group


def not_implemented(area: str) -> Handler:
    """Placeholder handler for commands whose owner has not wired them yet."""

    def handler(args: argparse.Namespace) -> Any:
        raise NotImplementedError(f"{area}: not implemented yet")

    return handler


def _default(obj: Any) -> Any:
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, (uuid.UUID,)):
        return str(obj)
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    return str(obj)


def to_json(result: Any) -> str:
    """Serialise a handler result (datetimes ISO, uuids/Decimals handled, sets as lists)."""
    return json.dumps(result, indent=2, sort_keys=False, default=_default)


def _cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=_default)
    else:
        text = "" if value is None else str(_default(value) if not isinstance(value, (str, int, float, bool)) else value)
    return text if len(text) <= 60 else text[:57] + "..."


def to_table(result: Any) -> str:
    """Human rendering: a list of dicts becomes a column table, a dict becomes ``key: value``
    lines (nested values as compact JSON), anything else ``str()``."""
    if isinstance(result, list) and result and all(isinstance(r, dict) for r in result):
        cols: list[str] = []
        for row in result:
            cols.extend(k for k in row if k not in cols)
        cells = [[_cell(row.get(c)) for c in cols] for row in result]
        widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(cols)]
        lines = ["  ".join(c.ljust(w) for c, w in zip(cols, widths)), "  ".join("-" * w for w in widths)]
        lines += ["  ".join(v.ljust(w) for v, w in zip(r, widths)) for r in cells]
        return "\n".join(lines)
    if isinstance(result, dict):
        width = max((len(str(k)) for k in result), default=0)
        return "\n".join(f"{str(k).ljust(width)}  {_cell(v) if isinstance(v, (dict, list)) else v}" for k, v in result.items())
    if isinstance(result, list):
        return "\n".join(str(r) for r in result)
    return "" if result is None else str(result)


def emit(result: Any, as_json: bool) -> None:
    """Print a handler result to stdout."""
    text = to_json(result) if as_json else to_table(result)
    if text:
        print(text)


# --------------------------------------------------------------------------- db (scaffold)


def _db_init(args: argparse.Namespace) -> Any:
    from sdlc_graph import db

    return db.init(args.schema)


def _db_reset(args: argparse.Namespace) -> Any:
    from sdlc_graph import db

    return db.reset(args.schema)


def register_db(subparsers: argparse._SubParsersAction) -> None:
    """``sdlc-graph db init|reset [--schema S]`` (schema default: SDLC_SCHEMA or public)."""
    group = add_group(subparsers, "db", "create or reset the database schema")
    for name, handler, help in (("init", _db_init, "create the schema and apply sql/*.sql (idempotent)"),
                                ("reset", _db_reset, "drop sdlc-graph tables in the schema and re-init")):
        p = add_command(group, name, handler, help)
        p.add_argument("--schema", default=None, help="target schema (default: $SDLC_SCHEMA or public)")


# --------------------------------------------------------------------------- codegraph (owner G)


def register_codegraph(subparsers: argparse._SubParsersAction) -> None:
    """index, delta, verify-snapshot, query deps|impact|owners|search. Owner: G."""
    from contextlib import closing

    def run(fn: Handler) -> Handler:
        """Open a connection for one codegraph command (closed afterwards) and call fn(conn, args)."""

        def handler(args: argparse.Namespace) -> Any:
            from sdlc_graph import db

            with closing(db.connect()) as conn:
                return fn(conn, args)

        return handler

    def snap_id(conn: Any, args: argparse.Namespace) -> int:
        from sdlc_graph.codegraph import snapshot

        return snapshot.resolve_snapshot(conn, args.repo, args.sha)

    def do_index(conn: Any, args: argparse.Namespace) -> Any:
        from sdlc_graph.codegraph import snapshot

        return snapshot.index(conn, args.repo, args.sha, name=args.name)

    def do_delta(conn: Any, args: argparse.Namespace) -> Any:
        from sdlc_graph.codegraph import snapshot

        return snapshot.delta(conn, args.repo, args.from_sha, args.to_sha, name=args.name)

    def do_verify(conn: Any, args: argparse.Namespace) -> Any:
        from sdlc_graph.codegraph import snapshot

        result = snapshot.verify(conn, args.repo, args.sha, name=args.name)
        return CommandResult(result, 0 if result["ok"] else 1)

    def do_deps(conn: Any, args: argparse.Namespace) -> Any:
        from sdlc_graph.codegraph import queries

        return queries.deps(conn, snap_id(conn, args), args.symbol, depth=args.depth, direction=args.direction,
                            rels=args.rel)

    def do_impact(conn: Any, args: argparse.Namespace) -> Any:
        from sdlc_graph.codegraph import queries

        paths, symbols = _split_targets(args.targets, args.paths, args.symbols)
        if not paths and not symbols:
            raise ValueError("query impact needs at least one path or symbol (positional, --paths or --symbols)")
        return queries.impact(conn, snap_id(conn, args), paths=paths, symbols=symbols, depth=args.depth)

    def do_owners(conn: Any, args: argparse.Namespace) -> Any:
        from sdlc_graph.codegraph import queries

        return queries.owners(conn, args.repo, args.paths, sha=args.sha)

    def do_search(conn: Any, args: argparse.Namespace) -> Any:
        from sdlc_graph.codegraph import queries

        return queries.search(conn, snap_id(conn, args), args.text, kind=args.kind, limit=args.limit)

    def do_snapshots(conn: Any, args: argparse.Namespace) -> Any:
        from sdlc_graph.codegraph import queries

        return queries.snapshots(conn, args.repo)

    p = add_command(subparsers, "index", run(do_index), "index one commit as a full code-graph snapshot")
    p.add_argument("--repo", required=True, help="path to a git checkout (read-only)")
    p.add_argument("--sha", default="HEAD", help="commit (default HEAD)")
    p.add_argument("--name", default=None, help="repo name to store (default: origin owner/name or dir name)")

    p = add_command(subparsers, "delta", run(do_delta), "store --to as a delta snapshot against --from")
    p.add_argument("--repo", required=True)
    p.add_argument("--from", dest="from_sha", required=True)
    p.add_argument("--to", dest="to_sha", required=True)
    p.add_argument("--name", default=None)

    p = add_command(subparsers, "verify-snapshot", run(do_verify), "re-extract a commit and diff it against the stored snapshot")
    p.add_argument("--repo", required=True)
    p.add_argument("--sha", default="HEAD")
    p.add_argument("--name", default=None)

    q = add_group(subparsers, "query", "read-only code-graph queries")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=None, help="repo name (default: latest snapshot overall)")
    common.add_argument("--sha", default=None, help="commit or prefix (default: latest snapshot of the repo)")

    p = add_command(q, "deps", run(do_deps), "dependency neighbourhood of a symbol")
    p.add_argument("symbol", help="kind:qualname@path, qualname, or path")
    p.add_argument("--depth", type=int, default=1)
    p.add_argument("--direction", choices=("out", "in", "both"), default="out")
    p.add_argument("--rel", action="append", default=None, help="restrict to rel (repeatable)")
    _merge(p, common)

    p = add_command(q, "impact", run(do_impact), "static blast radius of paths/symbols")
    p.add_argument("targets", nargs="*", help="repo-relative paths or kind:qualname@path keys")
    p.add_argument("--paths", nargs="*", default=None)
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--depth", type=int, default=4)
    _merge(p, common)

    p = add_command(q, "owners", run(do_owners), "CODEOWNERS handles for paths")
    p.add_argument("paths", nargs="+")
    p.add_argument("--repo", required=True)
    p.add_argument("--sha", default=None, help="commit whose CODEOWNERS to use (default: the newest indexed commit)")

    p = add_command(q, "search", run(do_search), "search symbols by name/path")
    p.add_argument("text")
    p.add_argument("--kind", default=None)
    p.add_argument("--limit", type=int, default=20)
    _merge(p, common)

    p = add_command(q, "snapshots", run(do_snapshots), "list stored snapshots, newest first")
    p.add_argument("--repo", default=None, help="repo name (default: all repos)")


def _split_targets(targets: list[str] | None, paths: list[str] | None,
                   symbols: list[str] | None) -> tuple[list[str] | None, list[str] | None]:
    """Sort positional impact targets into paths and symbol keys (``kind:qualname@path``)."""
    from sdlc_graph import shapes

    out_paths, out_symbols = list(paths or []), list(symbols or [])
    for t in targets or []:
        try:
            shapes.parse_key(t)
        except ValueError:
            out_paths.append(t)
        else:
            out_symbols.append(t)
    return out_paths or None, out_symbols or None


def _merge(parser: argparse.ArgumentParser, parent: argparse.ArgumentParser) -> None:
    """Copy a parent's optional arguments into ``parser`` (argparse parents only work at creation)."""
    for action in parent._actions:
        if action.option_strings:
            parser.add_argument(*action.option_strings, dest=action.dest, default=action.default, help=action.help)


# --------------------------------------------------------------------------- ledger (owner L)


def register_ledger(subparsers: argparse._SubParsersAction) -> None:
    """node, evidence, decision, workflow, run, metrics. Owner: L."""
    from sdlc_graph import db

    def _with_conn(fn: Callable[[Any, argparse.Namespace], Any]) -> Handler:
        def handler(args: argparse.Namespace) -> Any:
            with db.connect() as conn:
                return fn(conn, args)
        return handler

    def _node_create(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import nodes

        if a.kind == "contract":
            raise ValueError("contracts are created with 'sdlc-graph node draft' (intake scan, dedupe, overlap rule)")
        return nodes.create(conn, kind=a.kind, slug=a.slug, title=a.title, repo=a.repo, created_by=a.actor,
                            tier=a.tier, attrs=a.attrs, acceptance=a.acceptance)

    def _node_draft(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import intake

        # --ticket is always literal text: an issue body that starts with "@mention" must never be
        # read as a path. Files are an explicit, separate flag chosen by the operator.
        if a.ticket_file is not None:
            try:
                ticket = sys.stdin.read() if a.ticket_file == "-" else open(a.ticket_file, encoding="utf-8").read()
            except OSError as exc:
                raise ValueError(f"cannot read --ticket-file {a.ticket_file!r}: {exc.strerror or exc}") from None
        else:
            ticket = a.ticket
        contract_text = None
        if a.contract is not None:
            try:
                contract_text = open(a.contract, encoding="utf-8").read()
            except OSError as exc:
                raise ValueError(f"cannot read --contract {a.contract!r}: {exc.strerror or exc}") from None
        result = intake.contract_draft(conn, slug=a.slug, title=a.title, repo=a.repo, ticket_text=ticket, actor=a.actor,
                                       feature=a.feature, touched_symbols=a.touched, capabilities=a.capabilities,
                                       acceptance=a.acceptance, depends_on=a.depends_on, tier=a.tier,
                                       defaults=a.default, contract_text=contract_text, strict=a.strict)
        return result if result["status"] == "created" else CommandResult(result, 1)

    def _contract_update(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import intake

        try:
            text = sys.stdin.read() if a.from_file == "-" else open(a.from_file, encoding="utf-8").read()
        except OSError as exc:
            raise ValueError(f"cannot read --from-file {a.from_file!r}: {exc.strerror or exc}") from None
        result = intake.contract_update(conn, a.slug, text, actor=a.actor)
        return result if result["status"] in ("updated", "unchanged") else CommandResult(result, 1)

    def _contract_parse(a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import contract_md

        try:
            text = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
        except OSError as exc:
            raise ValueError(f"cannot read {a.file!r}: {exc.strerror or exc}") from None
        parsed = contract_md.parse_contract(text)
        return parsed if not parsed["missing"] else CommandResult(parsed, 1)

    def _node_get(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import nodes

        return nodes.get(conn, a.slug)

    def _node_neighbours(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import nodes

        rows = nodes.neighbours(conn, a.slug, rel=a.rel, direction=a.direction)
        if a.json:
            return rows
        return [{"direction": r["direction"], "rel": r["rel"], "slug": r["node"]["slug"], "kind": r["node"]["kind"],
                 "state": r["node"]["state"]} for r in rows]

    def _node_link(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import nodes

        return nodes.link(conn, a.src, a.dst, a.rel)

    def _node_transition(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import transitions

        result = transitions.transition(conn, a.slug, a.to_state, a.actor, evidence_ids=a.evidence_ids, reason=a.reason)
        return result if result["ok"] else CommandResult(result, 1)

    def _evidence_add(a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import evidence, oidc

        token = oidc.resolve_token(a.oidc_token) if a.oidc_token else None
        if token and str(a.source).strip().startswith("ci:"):
            # Fail fast without a connection: a refused token (or a slow JWKS fetch) never holds one open.
            # evidence.add verifies again (the keys are cached), so this is not a trust shortcut.
            oidc.verify_token(token)
        with db.connect() as conn:
            return evidence.add(conn, a.slug, a.kind, a.payload, source=a.source, oidc_token=token)

    def _evidence_find(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import evidence

        rows = evidence.find(conn, a.kind, url=a.url, repo=a.repo, pr=a.pr)
        if a.json:
            return rows
        return "\n".join(r["slug"] for r in rows)

    def _decision_propose(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import decisions

        return decisions.propose(conn, a.slug, a.question, a.options, actor=a.actor, recommendation=a.recommendation,
                                 due=a.due)

    def _decision_resolve(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import decisions

        return decisions.resolve(conn, a.decision_id, a.choice, actor=a.actor)

    def _workflow_next(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import workflow

        return workflow.next_step(conn, a.slug)

    def _run_start(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import runs

        return runs.start(conn, stage=a.stage, requested_model=a.model, slug=a.node, tool_use_id=a.tool_use_id,
                          agent_type=a.agent_type, model_tier=a.tier, enforce_budget=not a.record_only)

    def _run_finish(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import runs

        return runs.finish(conn, a.run_id, outcome=a.outcome, usage=a.usage)

    def _run_budget(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import runs

        return runs.budget(conn, a.slug)

    def _metrics_snapshot(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import metrics

        return metrics.snapshot(conn, repo=a.repo, since=a.since)

    def _epic_decompose(conn: Any, a: argparse.Namespace) -> Any:
        from sdlc_graph.ledger import decompose

        return decompose.decompose(conn, a.slug, root=a.root)

    h = _with_conn

    g = add_group(subparsers, "node", "ledger nodes")
    p = add_command(g, "create", h(_node_create), "create a node in its initial state (contracts: use 'node draft')")
    p.add_argument("--kind", required=True)
    p.add_argument("--slug", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--actor", required=True, help="@handle | agent:<alias> | ci:<system>")
    p.add_argument("--tier", default=None, choices=("trivial", "low", "medium", "high"))
    p.add_argument("--attrs", type=json_arg, default=None)
    p.add_argument("--acceptance", type=json_arg, default=None)
    p = add_command(g, "draft", h(_node_draft), "draft a contract (intake scan, dedupe, overlap rule)")
    p.add_argument("--slug", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--actor", required=True)
    tk = p.add_mutually_exclusive_group(required=True)
    tk.add_argument("--ticket", default=None, help="ticket text, taken literally (an @ is just a character)")
    tk.add_argument("--ticket-file", default=None, help="read the ticket text from this file ('-' for stdin); "
                    "pass it only paths you chose, never text taken from a ticket")
    p.add_argument("--feature", default=None)
    p.add_argument("--touched", nargs="*", default=None, help="symbol keys or paths")
    p.add_argument("--capabilities", type=json_arg, default=None)
    p.add_argument("--acceptance", type=json_arg, default=None)
    p.add_argument("--depends-on", nargs="*", default=None)
    p.add_argument("--tier", default=None, choices=("trivial", "low", "medium", "high"))
    p.add_argument("--contract", default=None, metavar="CONTRACT_MD",
                   help="a contract.md whose '## Defaults when ambiguous' and '## Capabilities' blocks are attached "
                        "at draft time (the drafted -> ready guard requires both)")
    p.add_argument("--default", action="append", default=None, metavar="LINE",
                   help="one 'Defaults when ambiguous' line (repeat it; replaces the defaults --contract held)")
    p.add_argument("--strict", action="store_true",
                   help="refuse the draft (status refused, exit 1, nothing created) when a block the ready guard "
                        "needs is missing")
    p = add_command(g, "get", h(_node_get), "node view: state, open decisions, evidence summary, edges")
    p.add_argument("slug")
    p = add_command(g, "neighbours", h(_node_neighbours), "adjacent nodes")
    p.add_argument("slug")
    p.add_argument("--rel", default=None)
    p.add_argument("--direction", choices=("out", "in", "both"), default="both")
    p = add_command(g, "link", h(_node_link), "add a ledger edge src -[rel]-> dst")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--rel", required=True)
    p = add_command(g, "transition", h(_node_transition), "evaluate guards and record a transition")
    p.add_argument("slug")
    p.add_argument("to_state")
    p.add_argument("--actor", required=True)
    p.add_argument("--evidence-ids", nargs="*", type=int, default=None)
    p.add_argument("--reason", default=None)

    g = add_group(subparsers, "contract", "contract.md on a contract node")
    p = add_command(g, "update", h(_contract_update),
                    "re-read a contract.md onto a drafted or ready contract (paths, capabilities, defaults, "
                    "acceptance, tier, decisions); records contract_updated evidence")
    p.add_argument("slug")
    p.add_argument("--from-file", required=True, metavar="CONTRACT_MD", help="the contract.md ('-' for stdin)")
    p.add_argument("--actor", required=True, help="@handle | agent:<alias> (an agent may not change a ready "
                   "contract's paths, capabilities, defaults, acceptance or tier)")
    p = add_command(g, "parse", _contract_parse, "parse a contract.md and print its blocks (touches nothing; "
                    "exit 1 when a block is missing)")
    p.add_argument("file", help="the contract.md ('-' for stdin)")

    g = add_group(subparsers, "evidence", "evidence rows")
    p = add_command(g, "add", _evidence_add, "store one evidence row")
    p.add_argument("slug")
    p.add_argument("--kind", required=True)
    p.add_argument("--payload", type=json_arg, required=True, help="JSON or @file")
    p.add_argument("--source", default="local", help="'local' | 'ci:<run url>' | 'github:<review id>'")
    p.add_argument("--oidc-token", default=None, metavar="JWT|@PATH|env:VAR",
                   help="GitHub Actions OIDC token; required for a ci: source, verified (signature, issuer, "
                   "audience $SDLC_OIDC_AUDIENCE or 'sdlc-graph', expiry, event $SDLC_OIDC_EVENTS, the trusted "
                   "job_workflow_ref $SDLC_OIDC_WORKFLOW_REF, the node's owner/name repository); its sub becomes "
                   "signed_by and its verified claims go into payload.oidc. "
                   "There is no --signed-by: a typed subject would be forgeable")
    p = add_command(g, "find", h(_evidence_find),
                    "the node(s) whose evidence names a URL; for --kind pr_url, the contract whose pr_url is this "
                    "pull request (same owner/name and number, and the node's repo is that owner/name). Prints one "
                    "slug per line, nothing when none match")
    p.add_argument("--kind", required=True)
    p.add_argument("--url", default=None, help="the URL (a PR's web or API URL for pr_url)")
    p.add_argument("--repo", default=None, help="owner/name, with --pr, instead of --url")
    p.add_argument("--pr", type=int, default=None, help="pull request number, with --repo")

    g = add_group(subparsers, "decision", "decisions a human makes first")
    p = add_command(g, "propose", h(_decision_propose), "propose a decision")
    p.add_argument("slug")
    p.add_argument("--question", required=True)
    p.add_argument("--options", type=json_arg, default=[])
    p.add_argument("--recommendation", default=None)
    p.add_argument("--due", default=None)
    p.add_argument("--actor", required=True)
    p = add_command(g, "resolve", h(_decision_resolve), "resolve a decision (humans only)")
    p.add_argument("decision_id", type=int)
    p.add_argument("--choice", required=True)
    p.add_argument("--actor", required=True)

    g = add_group(subparsers, "epic", "epics")
    p = add_command(g, "decompose", h(_epic_decompose), "proposed feature nodes from the epic's file, walking skeleton first (writes nothing)")
    p.add_argument("slug")
    p.add_argument("--root", default=None, help="checkout holding the epic file (default $CLAUDE_PROJECT_DIR or the working directory)")

    g = add_group(subparsers, "workflow", "workflow navigation")
    p = add_command(g, "next", h(_workflow_next), "next allowed step for a node")
    p.add_argument("slug")

    g = add_group(subparsers, "run", "agent runs and the Fable budget")
    p = add_command(g, "start", h(_run_start), "record a run start")
    p.add_argument("--stage", default=None)
    p.add_argument("--model", required=True, help="requested model alias or ID")
    p.add_argument("--node", default=None, help="node slug")
    p.add_argument("--tool-use-id", default=None)
    p.add_argument("--agent-type", default=None, help="Agent subagent_type, recorded on the run row")
    p.add_argument("--tier", default=None, help="model tier to record (default: the tier of --model); the guard hook "
                   "reserves a planner slot with --tier planner")
    p.add_argument("--record-only", action="store_true",
                   help="record a spawn that already ran: skip the Fable budget refusal (PostToolUse hook)")
    p = add_command(g, "finish", h(_run_finish), "record a run finish")
    p.add_argument("run_id", type=int)
    p.add_argument("--outcome", required=True)
    p.add_argument("--usage", type=json_arg, default=None)
    p = add_command(g, "budget", h(_run_budget), "Fable budget of a node's epic")
    p.add_argument("slug")

    g = add_group(subparsers, "metrics", "ledger metrics")
    p = add_command(g, "snapshot", h(_metrics_snapshot), "per-stage metrics from the ledger")
    p.add_argument("--repo", default=None)
    p.add_argument("--since", default=None, help="ISO date/time")


# --------------------------------------------------------------------------- impact (owner I)


def _impact_snapshot(conn: Any, args: argparse.Namespace) -> int:
    from sdlc_graph.impact import affected

    return affected.latest_snapshot_id(conn, args.repo, args.sha)


def _impact_inputs(conn: Any, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Snapshot id and the affected set for ``--node`` or ``--paths/--symbols``."""
    from sdlc_graph.impact import affected

    if args.node:
        row = conn.execute("SELECT repo FROM node WHERE slug = %s", (args.node,)).fetchone()
        if row is None:
            raise LookupError(f"unknown node {args.node!r}")
        sid = affected.latest_snapshot_id(conn, args.repo or row["repo"], args.sha)
        return sid, affected.affected_for_node(conn, args.node, sid)
    sid = _impact_snapshot(conn, args)
    return sid, affected.affected(conn, sid, paths=args.paths, symbols=args.symbols, depth=args.depth)


def _impact_affected(args: argparse.Namespace) -> Any:
    from sdlc_graph import db
    from sdlc_graph.impact import record

    with db.connect() as conn:
        sid, result = _impact_inputs(conn, args)
        if args.save:
            result = {**result, "prediction": record.predict(conn, sid, result, slug=args.node)}
        return result


def _impact_predict(args: argparse.Namespace) -> Any:
    from sdlc_graph import db
    from sdlc_graph.impact import record

    with db.connect() as conn:
        sid, result = _impact_inputs(conn, args)
        inputs = {"paths": args.paths or [], "symbols": args.symbols or [], "depth": args.depth}
        return record.predict(conn, sid, result, slug=args.node, inputs=inputs)


def _impact_outcome(args: argparse.Namespace) -> Any:
    from sdlc_graph import db
    from sdlc_graph.impact import record

    if args.junit and args.failing:
        raise ValueError("give --failing or --junit, not both")
    with db.connect() as conn:
        return record.record_outcome(conn, args.prediction, args.junit or args.failing or [])


def _impact_summary(args: argparse.Namespace) -> Any:
    from sdlc_graph import db
    from sdlc_graph.impact import record

    with db.connect() as conn:
        return record.summary(conn, args.repo)


def _capabilities_compile(args: argparse.Namespace) -> Any:
    import json as _json
    from pathlib import Path

    from sdlc_graph import db
    from sdlc_graph.impact import capabilities

    if args.file is not None:
        result = {"slug": None, "capabilities": args.file, **capabilities.compile_full(args.file)}
    elif args.slug:
        with db.connect() as conn:
            result = capabilities.compile_for(conn, args.slug)
    else:
        raise ValueError("give a contract slug or --file")
    if args.write:
        out = Path(args.write)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(result["settings"], indent=2) + "\n", encoding="utf-8")
        result = {**result, "written": str(out)}
    return result


def register_impact(subparsers: argparse._SubParsersAction) -> None:
    """impact affected|predict|outcome|record|summary, capabilities compile. Owner: I."""
    g = add_group(subparsers, "impact", "affected tests and predicted-vs-actual")

    def selectors(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo", default=None, help="repo name (default: the node's repo, else the latest snapshot)")
        p.add_argument("--sha", default=None, help="commit SHA or prefix (default: latest snapshot)")
        p.add_argument("--paths", nargs="*", default=None)
        p.add_argument("--symbols", nargs="*", default=None)
        p.add_argument("--node", default=None, help="use a contract's touched_symbols/capabilities.paths")
        p.add_argument("--depth", type=int, default=4)

    p = add_command(g, "affected", _impact_affected, "affected symbols, routes, tests (closest first) and owners")
    selectors(p)
    p.add_argument("--save", action="store_true", help="store the result as a prediction")
    p = add_command(g, "predict", _impact_predict, "compute the affected set and store it as a prediction")
    selectors(p)
    for name in ("outcome", "record"):
        p = add_command(g, name, _impact_outcome, "record the tests that actually failed and score the prediction")
        p.add_argument("--prediction", type=int, required=True)
        p.add_argument("--failing", nargs="*", default=[], help="pytest node ids that failed")
        p.add_argument("--junit", default=None, help="JUnit XML report (pytest --junitxml) instead of --failing")
    p = add_command(g, "summary", _impact_summary, "mean precision/recall of recorded outcomes")
    p.add_argument("--repo", default=None)

    g = add_group(subparsers, "capabilities", "contract capabilities")
    p = add_command(g, "compile", _capabilities_compile,
                    "compile a contract's capabilities into a Claude Code settings fragment")
    p.add_argument("slug", nargs="?", default=None)
    p.add_argument("--file", type=json_arg, default=None, help="capabilities JSON or @file instead of a slug")
    p.add_argument("--write", default=None, metavar="PATH", help="write the settings fragment (JSON) to PATH")


# --------------------------------------------------------------------------- hooks (owner H)


def register_hooks(subparsers: argparse._SubParsersAction) -> None:
    """hooks settings. Owner: H (wired by the integrator)."""

    def settings(args: argparse.Namespace) -> Any:
        import json as _json
        import shlex
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "hooks"
        fragment = _json.loads((root / "hooks.example.json").read_text())
        python = args.python or sys.executable
        # The example uses $CLAUDE_PROJECT_DIR-relative paths; this prints absolute ones for this
        # checkout. The hooks are stdlib-only and find .venv/bin/sdlc-graph next to themselves.
        for event, script in (("PreToolUse", "guard_agent_spawn.py"), ("PostToolUse", "record_agent_run.py")):
            for group in fragment["hooks"][event]:
                for hook in group["hooks"]:
                    hook["command"] = f"{shlex.quote(python)} -I {shlex.quote(str(root / script))}"
        return fragment

    g = add_group(subparsers, "hooks", "Claude Code hooks for model routing and run recording")
    p = add_command(g, "settings", settings, "print the settings.json hooks fragment that installs both hooks")
    p.add_argument("--python", default=None, help="interpreter path for the hook commands (default: this venv)")


# --------------------------------------------------------------------------- spanner (owner S)


def register_spanner(subparsers: argparse._SubParsersAction) -> None:
    """spanner ddl|smoke|export|query. Owner: S."""
    import os

    def ddl_cmd(args: argparse.Namespace) -> Any:
        from sdlc_graph.spanner import ddl

        text = ddl.render()
        if args.out:
            import pathlib

            pathlib.Path(args.out).write_text(text)
            return {"ok": True, "out": args.out, "statements": len(ddl.ddl_statements())}
        sys.stdout.write(text)
        return None

    def smoke_cmd(args: argparse.Namespace) -> Any:
        from sdlc_graph.spanner import emulator_smoke

        res = emulator_smoke.smoke(host=args.host, from_pg=args.from_pg)
        return CommandResult(res, 0 if res["ok"] else 1)

    def export_cmd(args: argparse.Namespace) -> Any:
        from contextlib import closing

        from sdlc_graph import db
        from sdlc_graph.spanner import export

        queries: dict[str, dict[str, str]] = {"chain": {}}
        if args.symbol:
            queries["tests_reaching"] = {"symbol": args.symbol}
        if args.slug:
            queries["contract_routes"] = {"slug": args.slug}
        with closing(db.connect()) as conn:
            try:
                if args.project and args.instance and not args.host and not os.environ.get("SPANNER_EMULATOR_HOST"):
                    # a real instance (gcloud created it and the database with the DDL): mirror rows, keep the schema
                    return export.mirror(conn, project=args.project, instance=args.instance, database=args.database,
                                         snapshot_id=args.snapshot, queries=queries)
                return export.export(conn, snapshot_id=args.snapshot, host=args.host, database=args.database,
                                     queries=queries)
            except RuntimeError as exc:  # no emulator: a normal failure result, exit 1
                return CommandResult({"ok": False, "error": str(exc)}, 1)

    def query_cmd(args: argparse.Namespace) -> Any:
        from sdlc_graph.spanner import export

        try:
            return export.query(args.gql, slug=args.slug, symbol=args.symbol, host=args.host, database=args.database)
        except RuntimeError as exc:
            return CommandResult({"ok": False, "error": str(exc)}, 1)

    g = add_group(subparsers, "spanner", "Spanner Graph export and emulator smoke test")
    p = add_command(g, "ddl", ddl_cmd, "print GoogleSQL DDL with CREATE PROPERTY GRAPH")
    p.add_argument("--out", default=None, help="write the DDL to this path instead of stdout")
    p = add_command(g, "smoke", smoke_cmd, "apply the DDL on the emulator and run a GQL query")
    p.add_argument("--host", default=None, help="emulator host:port (default $SPANNER_EMULATOR_HOST or localhost:9110)")
    p.add_argument("--from-pg", action="store_true", help="copy the ledger from Postgres instead of the sample")
    p = add_command(g, "export", export_cmd,
                    "load the Postgres ledger and one code-graph snapshot into the emulator and run the GQL queries")
    p.add_argument("--host", default=None, help="emulator host:port (default $SPANNER_EMULATOR_HOST or localhost:9110)")
    p.add_argument("--snapshot", type=int, default=None, help="snapshot id (default: newest)")
    p.add_argument("--database", default="sdlc_export")
    p.add_argument("--slug", default=None, help="contract slug for the contract_routes query")
    p.add_argument("--symbol", default=None, help="qualname for the tests_reaching query")
    p.add_argument("--project", default=None, help="Google Cloud project of a real instance (with --instance; ADC credentials)")
    p.add_argument("--instance", default=None, help="real Spanner instance: rows are mirrored into --database, whose "
                   "schema gcloud created from `spanner ddl`; without these the emulator is used")
    p = add_command(g, "query", query_cmd, "run a named query (chain, tests_reaching, contract_routes) or raw GQL")
    p.add_argument("gql", help="query name or a GQL/SQL statement")
    p.add_argument("--host", default=None)
    p.add_argument("--database", default="sdlc_export")
    p.add_argument("--slug", default=None)
    p.add_argument("--symbol", default=None)


# --------------------------------------------------------------------------- mcp (owner M)


def register_mcp(subparsers: argparse._SubParsersAction) -> None:
    """mcp (stdio server), status, provisioned. Owner: M."""
    import os

    from sdlc_graph import db

    def handler(args: argparse.Namespace) -> Any:
        from sdlc_graph import mcp_server

        argv = ["--http", "--port", str(args.port)] if args.http else []
        # Blocks until the client disconnects; exit directly so nothing is printed on stdout
        # after a stdio session (stdout is the protocol channel).
        raise SystemExit(mcp_server.main(argv))

    p = add_command(subparsers, "mcp", handler, "run the MCP server (stdio; --http for Streamable HTTP)")
    p.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    p.add_argument("--port", type=int, default=8765, help="HTTP port (with --http)")

    def do_status(args: argparse.Namespace) -> Any:
        from sdlc_graph import status

        result = status.graph_status()
        return CommandResult(result, 0 if result["configured"] else 1)

    add_command(subparsers, "status", do_status,
                "is the graph configured: enabled, provider, reachable, schema version, snapshot and node counts "
                "(exit 1 when not configured or off; never writes)")

    def do_provisioned(args: argparse.Namespace) -> Any:
        from sdlc_graph import provisioned
        from sdlc_graph.codegraph.snapshot import repo_name

        repo = args.repo or repo_name(args.repo_dir)
        actor = args.actor or os.environ.get("SDLC_ACTOR") or "agent:claude"
        with db.connect() as conn:
            return provisioned.record(conn, repo=repo, provider=args.provider, where=args.where, schema=args.schema,
                                      actor=actor, extra=args.extra)

    p = add_command(subparsers, "provisioned", do_provisioned,
                    "record graph_provisioned {provider, where, schema} on the repo node (once per database; a repeat "
                    "reports already_there)")
    p.add_argument("--provider", required=True, choices=("local", "neon", "aws", "spanner"))
    p.add_argument("--where", required=True, help="where the database lives, without credentials")
    p.add_argument("--schema", required=True, help="the ledger schema version graph_status reported (e.g. 010)")
    p.add_argument("--repo-dir", default=".", help="checkout whose origin names the repo (default .)")
    p.add_argument("--repo", default=None, help="repo name instead of the one read from --repo-dir")
    p.add_argument("--actor", default=None, help="default $SDLC_ACTOR or agent:claude")
    p.add_argument("--extra", type=json_arg, default=None, help="extra payload keys (JSON object)")


# --------------------------------------------------------------------------- index (owner: embeddings)


def register_index(subparsers: argparse._SubParsersAction) -> None:
    """index build|search|status, attached to the existing ``index`` command. Owner: embeddings.

    ``index --repo P`` (extract one commit; ``register_codegraph``) keeps working. This function
    adds the ``build``, ``search`` and ``status`` subcommands to that same ``index`` parser, and
    makes ``--repo`` optional there so ``index build`` and ``index search`` do not need it; the
    bare form still refuses to run without ``--repo``. If no ``index`` parser exists it makes one.
    """
    import os
    from contextlib import closing

    def resolve_repo(value: str | None) -> str | None:
        """``--repo`` is a repo name or a git checkout path (as in ``index --repo``)."""
        if value and os.path.isdir(os.path.join(value, ".git")):
            from sdlc_graph.codegraph import snapshot

            return snapshot.repo_name(value)
        return value

    def provider_for(args: argparse.Namespace) -> Any:
        from sdlc_graph.index import embed

        return embed.get_provider(getattr(args, "provider", None))

    def guard(fn: Callable[[], Any]) -> Any:
        from sdlc_graph.index import embed

        try:
            return fn()
        except (embed.ProviderUnavailable, embed.ProviderError) as exc:
            raise ValueError(str(exc)) from exc

    def do_build(args: argparse.Namespace) -> Any:
        from sdlc_graph import db
        from sdlc_graph.index import chunk

        provider = provider_for(args)
        with closing(db.connect()) as conn:
            # a checkout path also names the rule files (.claude/rules, REVIEW.md) to index as rule nodes
            root = args.repo if args.repo and os.path.isdir(os.path.join(args.repo, ".git")) else None
            return guard(lambda: chunk.build(conn, provider, repo=resolve_repo(args.repo), dry_run=args.dry_run,
                                              max_chunks=args.max_chunks, rules_root=root))

    def do_search(args: argparse.Namespace) -> Any:
        from sdlc_graph import db
        from sdlc_graph.index import search

        provider = provider_for(args)
        with closing(db.connect()) as conn:
            hits = guard(lambda: search.search(conn, args.text, provider=provider, scope=args.scope, kind=args.kind,
                                                repo=resolve_repo(args.repo), limit=args.limit))
        if args.json:
            return hits
        if not hits:
            print("no hits (is the index built? run: sdlc-graph index build --repo R)", file=sys.stderr)
        return [{"rank": i, "score": h["score"], "mode": h["mode"], "source": h["source"], "kind": h["kind"],
                 "ref": h["ref"], "section": h["section"], "text": search.snippet(h["text"], 80)}
                for i, h in enumerate(hits, start=1)]

    def do_status(args: argparse.Namespace) -> Any:
        from sdlc_graph import db
        from sdlc_graph.index import chunk, embed

        provider = embed.get_provider(None)
        with closing(db.connect()) as conn:
            out = chunk.stats(conn)
        out["provider"] = provider.name
        out["model"] = provider.model
        return out

    index_parser = subparsers.choices.get("index") if hasattr(subparsers, "choices") else None
    if index_parser is None:
        index_parser = add_command(subparsers, "index", not_implemented("index"), "retrieval index")
    legacy = index_parser.get_default("func")
    for action in index_parser._actions:
        if "--repo" in action.option_strings:
            action.required = False

    def bare(args: argparse.Namespace) -> Any:
        if getattr(args, "repo", None) is None and legacy is not None and not getattr(args, "index_command", None):
            raise ValueError("index: --repo is required (or use: index build | index search | index status)")
        return legacy(args)

    index_parser.set_defaults(func=bare)
    group = index_parser.add_subparsers(dest="index_command", metavar="COMMAND")

    p = add_command(group, "build", do_build,
                    "embed ledger nodes and the latest snapshot's symbol summaries (needs an embedding key)")
    p.add_argument("--repo", default=None, help="repo name or git checkout path (default: every repo)")
    p.add_argument("--provider", choices=("openai", "voyage", "none"), default=None,
                   help="embedding provider (default: $EMBED_PROVIDER, else openai if OPENAI_API_KEY is set, else none)")
    p.add_argument("--dry-run", action="store_true", help="count what would be embedded; call no API, write nothing")
    p.add_argument("--max-chunks", type=int, default=None, help="refuse to embed more than this many chunks")

    p = add_command(group, "search", do_search, "hybrid search (cosine + full-text, reciprocal rank fusion)")
    p.add_argument("text", help="query text")
    p.add_argument("--scope", choices=("node", "sym"), default=None, help="only ledger nodes, or only symbols")
    p.add_argument("--kind", default=None, help="node kind or symbol kind")
    p.add_argument("--repo", default=None, help="repo name or git checkout path")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--provider", choices=("openai", "voyage", "none"), default=None)

    add_command(group, "status", do_status, "chunk counts per source and embedding model, and the active provider")


# --------------------------------------------------------------------------- orchestrator (owner: orchestrator)


def register_orchestrator(subparsers: argparse._SubParsersAction) -> None:
    """orchestrate, feed, prime, escalations. Owner: orchestrator (increment 4)."""
    import os

    from sdlc_graph import db

    def do_orchestrate(a: argparse.Namespace) -> Any:
        from pathlib import Path

        from sdlc_graph import status as graph_status_mod
        from sdlc_graph.orchestrator import config, loop, store

        if not graph_status_mod.enabled(Path(a.repo) if a.repo else None):
            return CommandResult({"error": "graph_off",
                                  "message": "the orchestrator needs the ledger; run /factory:graph on. Nothing was started."}, 5)
        if a.stop:
            with db.connect() as conn:
                return loop.stop(conn, a.feature)
        explicit = a.executor_cmd or os.environ.get("ORCHESTRATOR_EXECUTOR")
        if not explicit and not a.live:
            raise ValueError("the default executor starts real claude sessions (they cost money): pass --live, "
                             "or --executor-cmd / ORCHESTRATOR_EXECUTOR for another executor (e.g. the fake one)")
        from sdlc_graph import status

        ledger = status.graph_status()
        if not ledger["configured"]:
            why = "unreachable" if not ledger["reachable"] else "not initialised"
            return CommandResult({"error": "ledger_unavailable",
                                  "message": f"the orchestrator needs the ledger, and it is {why} at {ledger['url']}: "
                                             f"{ledger['hint']}. Nothing was started.",
                                  "status": ledger}, 5)
        kw: dict[str, Any] = {}
        if a.plugin_dir:
            kw["plugin_dirs"] = [str(Path(d).resolve()) for d in a.plugin_dir]
        if a.no_cost_limit:
            kw["max_cost_usd"] = None
        elif a.max_cost is not None:
            kw["max_cost_usd"] = a.max_cost
        if a.test_command:
            import shlex

            kw["test_command"] = shlex.split(a.test_command)
        if a.no_require_tests:
            kw["require_test_command"] = False
        if a.session_database_url:
            kw["session_database_url"] = a.session_database_url
        cfg = config.Config(repo=Path(a.repo or os.getcwd()), base=a.base, tick=a.tick, max_runs=a.max_runs,
                            stall_minutes=a.stall_minutes, max_turns=a.max_turns, budget_usd=a.budget,
                            reviewer=a.reviewer, executor=config.executor_command(a.executor_cmd), executor_mode=a.executor,
                            conformance=not a.no_conformance, judge=a.judge, refinery=a.refinery, **({"stack": True} if a.stack else {}), **kw)
        try:
            out = loop.orchestrate(a.feature, cfg, once=a.once, max_ticks=a.max_ticks, quiet=a.quiet)
        except store.LockHeld as exc:
            return CommandResult({"error": "lock_held", "message": str(exc), "holder": exc.holder}, 1)
        return out

    p = add_command(subparsers, "orchestrate", do_orchestrate,
                    "run the goal loop for a feature: dispatch each contract's next step, gate it, transition")
    p.add_argument("feature", help="feature slug")
    p.add_argument("--once", action="store_true", help="run one tick and exit (tests, cron)")
    p.add_argument("--tick", type=float, default=60.0, help="seconds between ticks (default 60)")
    p.add_argument("--max-runs", type=int, default=2, help="max concurrent executor sessions (default 2)")
    p.add_argument("--refinery", action="store_true", help="goal is merged; merge a PR only when GitHub reports it approved")
    p.add_argument("--stack", action="store_true", default=None,
                   help="stacked pull requests: a contract whose one unmerged dependency is in review branches from that dependency instead of waiting for its merge (default from ORCHESTRATOR_STACK)")
    p.add_argument("--repo", default=None, help="source checkout; worktrees go next to it as <repo>-<slug> (default: cwd)")
    p.add_argument("--base", default=None, help="base branch (default: origin/HEAD or the current branch)")
    p.add_argument("--stall-minutes", type=float, default=30.0, help="no progress for this long = stalled (default 30)")
    p.add_argument("--max-turns", type=int, default=40, help="per session (default 40)")
    p.add_argument("--budget", type=float, default=5.0, help="USD per session, passed as --max-budget-usd (default 5)")
    p.add_argument("--max-cost", type=float, default=None,
                   help="USD cap for the whole feature (default 50): spent plus --budget reserved per open session; then a P2 decision")
    p.add_argument("--no-cost-limit", action="store_true", help="no cost cap (explicit opt-out of the default)")
    p.add_argument("--test-command", default=None,
                   help="command the orchestrator runs itself on the acceptance tests, e.g. 'python3 -m pytest -q': must fail "
                        "after the tests step and pass after verify (default: the stack's commands.test from .claude/sdlc.json)")
    p.add_argument("--no-require-tests", action="store_true",
                   help="allow a run with no test command at all: tests and verify then rest on evidence the session wrote itself "
                        "(default: refused when the contract names acceptance tests)")
    p.add_argument("--session-database-url", default=None,
                   help="ledger URL given to sessions (a restricted role; default ORCHESTRATOR_SESSION_DATABASE_URL, else the daemon's own)")
    p.add_argument("--reviewer", default="@reviewer", help="named human for the draft PRs")
    p.add_argument("--executor", choices=("local", "runner"), default="local",
                   help="where the executor's Claude comes from. local (default): this machine's own Claude Code login. runner: the same "
                        "command, but refuses to start (exit 2) without ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, Claude Platform on AWS "
                        "(CLAUDE_CODE_USE_ANTHROPIC_AWS=1 + ANTHROPIC_AWS_WORKSPACE_ID) or CLAUDE_CODE_USE_BEDROCK=1")
    p.add_argument("--executor-cmd", default=None,
                   help="executor command line (default ORCHESTRATOR_EXECUTOR, else the live step executor); was --executor before the mode flag")
    p.add_argument("--plugin-dir", action="append", default=[], help="plugin dir for every session (repeatable)")
    p.add_argument("--no-conformance", action="store_true", help="skip the conformance reviewer after implement")
    p.add_argument("--judge", action="store_true", help="enable the bounded Opus judge (after a step failed twice)")
    p.add_argument("--max-ticks", type=int, default=None, help="stop after N ticks")
    p.add_argument("--live", action="store_true", help="allow the default executor to start real claude sessions")
    p.add_argument("--quiet", action="store_true", help="no per-tick status line on stderr")
    p.add_argument("--stop", action="store_true", help="release the lock (the daemon exits at its next tick); worktrees stay")

    def do_feed(a: argparse.Namespace) -> Any:
        from sdlc_graph.orchestrator import feed

        with db.connect() as conn:
            return feed.feed(conn, feature=a.feature, since=a.since, limit=a.limit)

    p = add_command(subparsers, "feed", do_feed, "the ledger as a timeline: transitions, evidence, runs, decisions (newest first)")
    p.add_argument("--feature", default=None, help="only this feature and its contracts")
    p.add_argument("--since", default=None, help="ISO timestamp")
    p.add_argument("--limit", type=int, default=50)

    def do_prime(a: argparse.Namespace) -> Any:
        try:
            from sdlc_graph.orchestrator import prime

            return prime.prime_data(a.project_dir) if a.json else prime.prime(a.project_dir)
        except Exception as exc:  # noqa: BLE001 - the SessionStart hook never fails
            return {"active": None, "error": type(exc).__name__} if a.json else ""

    p = add_command(subparsers, "prime", do_prime,
                    "SessionStart text: the active contract, its ledger state, next step and previous run (always exits 0)")
    p.add_argument("--project-dir", default=None, help="default: CLAUDE_PROJECT_DIR or cwd")

    def do_escalations(a: argparse.Namespace) -> Any:
        from sdlc_graph.orchestrator import feed

        with db.connect() as conn:
            return feed.escalations(conn, feature=a.feature, all_open=a.all)

    p = add_command(subparsers, "escalations", do_escalations, "open decisions with a severity (P0 first)")
    p.add_argument("--feature", default=None)
    p.add_argument("--all", action="store_true", help="include open decisions without a severity")


# --------------------------------------------------------------------------- incident open


def register_incident(subparsers: argparse._SubParsersAction) -> None:
    """incident open: create the incident and, by band, log it, run a read-only diagnosis or open a P1 decision."""
    from sdlc_graph import db

    def do_open(a: argparse.Namespace) -> Any:
        import os
        from pathlib import Path

        from sdlc_graph.orchestrator import config, diagnose

        repo_path = Path(a.repo_path or os.getcwd()).resolve()
        repo = a.repo or repo_path.name
        executor = config.executor_command(a.executor_cmd) if a.executor_cmd else None
        if a.dry_run:
            return diagnose.open_incident(None, a.slug, a.title, a.band, actor=a.actor, repo=repo, repo_path=repo_path,
                                          dry_run=True, executor=executor)
        with db.connect() as conn:
            result = diagnose.open_incident(conn, a.slug, a.title, a.band, actor=a.actor, repo=repo, repo_path=repo_path,
                                            executor=executor)
        return result if result.get("diagnosis", {}).get("ok", True) else CommandResult(result, 1)

    existing = subparsers.choices.get("incident")
    group = next((act for act in existing._actions if isinstance(act, argparse._SubParsersAction)), None) \
        if existing is not None else add_group(subparsers, "incident", "incidents")
    p = add_command(group, "open", do_open, "open an incident: band 1 logs it, band 2 runs one read-only diagnosis "
                    "and posts it as evidence, band 3 opens a P1 decision for a human")
    p.add_argument("slug")
    p.add_argument("--title", required=True)
    p.add_argument("--band", type=int, choices=(1, 2, 3), required=True,
                   help="1 = log only; 2 = read-only diagnosis; 3 = P1 decision, no automatic action")
    p.add_argument("--repo", default=None, help="repo name for the node (default: the directory name)")
    p.add_argument("--repo-path", default=None, help="the checkout the diagnosis reads (default: the current directory)")
    p.add_argument("--actor", default="agent:incident", help="who opens it (@handle | agent:<alias> | ci:<system>)")
    p.add_argument("--executor-cmd", default=None, help="step-executor command (default: ORCHESTRATOR_EXECUTOR or "
                   "the bundled step_executor.sh)")
    p.add_argument("--dry-run", action="store_true", help="print the diagnosis command and change nothing")


# --------------------------------------------------------------------------- entry point (scaffold)

REGISTRARS: tuple[Callable[[argparse._SubParsersAction], None], ...] = (
    register_db, register_codegraph, register_index, register_ledger, register_impact, register_hooks, register_spanner,
    register_mcp, register_orchestrator, register_incident,
)


def build_parser() -> argparse.ArgumentParser:
    """The full parser with every area registered."""
    parser = argparse.ArgumentParser(
        prog="sdlc-graph",
        description="Derived code graph, intent ledger and impact-ordered verification.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True
    for register in REGISTRARS:
        register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse, run the handler, print the result, map exceptions to exit codes."""
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = getattr(args, "json", False)
    try:
        result = args.func(args)
    except NotImplementedError as exc:
        emit({"error": "not_implemented", "message": str(exc)}, as_json)
        return 3
    except LookupError as exc:
        emit({"error": "not_found", "message": str(exc)}, as_json)
        return 4
    except (ValueError, PermissionError) as exc:
        emit({"error": type(exc).__name__, "message": str(exc)}, as_json)
        return 2
    except Exception as exc:  # noqa: BLE001 - last-resort mapping for the CLI only
        import psycopg

        from sdlc_graph.db import redact_secrets

        if isinstance(exc, psycopg.OperationalError):
            emit({"error": "database_unavailable", "message": redact_secrets(str(exc).strip())}, as_json)
            return 5
        if isinstance(exc, psycopg.Error) and redact_secrets(str(exc)) != str(exc):
            # a malformed URL makes libpq echo it (password included): never let that reach a log as a traceback
            emit({"error": "database_unavailable", "message": redact_secrets(str(exc).strip())}, as_json)
            return 5
        raise
    exit_code = 0
    if isinstance(result, CommandResult):
        result, exit_code = result.result, result.exit_code
    emit(result, as_json)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
