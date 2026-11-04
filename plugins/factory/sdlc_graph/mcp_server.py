"""MCP server exposing the code graph (read-only) and the ledger (guarded writes).

Built on the official Python SDK, ``mcp>=1.2,<2`` (``from mcp.server.fastmcp import FastMCP``).
Server name ``graph`` so Claude Code shows tools as ``mcp__graph__<tool>`` (``mcp__plugin_factory_graph__<tool>`` when the factory plugin provides it). Every tool
returns the library function's dict (JSON), never free text.

The server starts with no database at all (a team that only wants the hooks has none): connections open per
call, and while no reachable, initialised database exists every tool answers
``{"ok": false, "error": "graph not configured", "hint": "graph on but not reachable: ...", "reason": ...}`` (see
``sdlc_graph.status``); ``graph_status`` says which of reachable and initialised is missing. With ``graph.enabled``
false in ``.claude/sdlc.json`` every tool answers ``{"ok": false, "error": "graph off", "hint": ...}`` and opens nothing. Errors: LookupError, ValueError and
PermissionError are caught and returned as ``{"ok": false, "error": {"type", "message"}}``; a tool
never raises, a list result is wrapped as ``{"ok": true, "result": [...]}``, and a guard failure is the ledger's normal ``{"ok": false, "missing": [...]}``.

Tools (read-only first; signatures and return shapes are in docs/graph-architecture.md):
    graph_status, graph_deps, graph_impact, graph_owners, graph_search, graph_snapshot, node_get, node_search,
    incident_similar, intake_context, node_neighbours, workflow_next, metrics_snapshot,
    epic_decompose                                                   (never write)
    contract_draft, contract_update, contract_transition, evidence_add, decision_propose,
    decision_resolve, run_start, run_finish, release_request         (guarded writes)

Identity comes from the server, not from the caller. Over stdio the server acts as a fixed actor
(``SDLC_ACTOR``, default ``agent:mcp``, always an ``agent:*`` label; anything else refuses to start).
Over Streamable HTTP with OAuth each call acts as ``agent:oauth/<sub>``, the subject of the verified
bearer token on that request (:func:`oauth_actor`): still agent-class, so a token never grants a human
or CI transition; it attributes the write to the caller who made it. No tool takes an ``actor``; an ``actor`` argument a client sends anyway is ignored, so an agent
cannot call itself ``@human`` or ``ci:github``. ``evidence_add`` always stores local, advisory
evidence (no ``source`` or ``signed_by`` argument), so an agent cannot forge binding CI rows.
Approve, merge, release, close, ``decision_resolve`` and CI evidence ingest therefore go through the
CLI (``sdlc-graph node transition --actor @you``, ``evidence add --source ci:...``), which a human
or CI job runs outside the agent's tool sandbox. Caveat, stated plainly: the CLI's ``--actor`` is
itself a self-declared string, so the guard holds against an agent that only has MCP tools, not
against one that can run shell commands as the operator.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib
import json
import os
import sys
from urllib.parse import unquote
from typing import Any, Callable

import psycopg
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

# Hints for clients (Claude Code can auto-approve read-only tools); the guards are still enforced
# server-side by the ledger whatever a client does with the hints.
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_GUARDED_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

SERVER_NAME = "graph"

READ_ONLY_TOOLS: tuple[str, ...] = ("graph_status", "graph_deps", "graph_impact", "graph_owners", "graph_search", "graph_snapshot",
                                    "node_get", "node_search", "incident_similar", "intake_context", "node_neighbours", "workflow_next", "metrics_snapshot",
                                    "epic_decompose")
WRITE_TOOLS: tuple[str, ...] = ("contract_draft", "contract_update", "contract_transition", "evidence_add",
                                "decision_propose", "decision_resolve", "run_start", "run_finish", "release_request")
TOOL_NAMES: tuple[str, ...] = READ_ONLY_TOOLS + WRITE_TOOLS

# States an agent actor may never move a contract into (the ledger enforces this too).
PROTECTED_STATES = frozenset({"approved", "merged", "released", "closed"})

_ERRORS: tuple[tuple[type[BaseException], str], ...] = (
    (LookupError, "not_found"),
    (PermissionError, "forbidden"),
    (ValueError, "invalid"),
    (NotImplementedError, "not_implemented"),
    (psycopg.Error, "database"),
    (ImportError, "unavailable"),
)


def _error(exc: BaseException) -> dict[str, Any]:
    kind = next((label for cls, label in _ERRORS if isinstance(exc, cls)), "internal")
    message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else (str(exc) or type(exc).__name__)
    return {"ok": False, "error": {"type": kind, "message": message}}


def _lib(module: str) -> Any:
    """Import a library module at call time so the server starts even if one is unfinished."""
    return importlib.import_module(f"sdlc_graph.{module}")


def _status() -> Any:
    return importlib.import_module("sdlc_graph.status")


def _redact(exc: BaseException) -> str:
    from sdlc_graph.db import redact_secrets

    return redact_secrets(f"{type(exc).__name__}: {exc}".strip())[:300]


def _default_factory() -> Callable[[], psycopg.Connection]:
    from sdlc_graph import db

    return db.connection_factory()


DEFAULT_ACTOR = "agent:mcp"


def server_actor(actor: str | None = None) -> str:
    """The identity every tool call acts as: ``actor``, else ``$SDLC_ACTOR``, else ``agent:mcp``.

    Raises:
        ValueError: it is not an ``agent:<alias>`` label (the MCP server never acts as a human or CI).
    """
    from sdlc_graph.ledger.transitions import actor_class

    chosen = actor or os.environ.get("SDLC_ACTOR") or DEFAULT_ACTOR
    if actor_class(chosen) != "agent":
        raise ValueError(f"the MCP server may only act as an agent:<alias> actor, not {chosen!r}; "
                         "humans and CI use the CLI")
    return chosen


OAUTH_ACTOR_PREFIX = "agent:oauth/"


def oauth_actor(subject: str) -> str:
    """The per-request actor for a verified OAuth subject: ``agent:oauth/<sub>``, an agent-class label, so a
    token never lifts a caller to human or CI rights; it only says who made the call. Characters the actor
    grammar does not allow become ``_`` and a short hash of the original subject is appended, so two subjects
    never share a label."""
    import hashlib
    import re

    clean = re.sub(r"[^A-Za-z0-9_.\-]", "_", subject)[:96]
    if clean != subject:
        clean += "." + hashlib.sha256(subject.encode("utf-8")).hexdigest()[:10]
    return OAUTH_ACTOR_PREFIX + clean


def _request_access_token(mcp: FastMCP) -> Any:
    """The verified AccessToken of the HTTP request carrying the current tool call, or None.

    The bearer middleware puts an ``AuthenticatedUser`` on the request scope; the Streamable HTTP transport hands
    that request to the tool call (``request_context.request``), which is read first because it is per message.
    The SDK's ``get_access_token()`` context variable is the fallback."""
    try:
        request = mcp.get_context().request_context.request
    except (LookupError, ValueError, AttributeError):
        request = None
    scope = getattr(request, "scope", None)
    user = scope.get("user") if isinstance(scope, dict) else None
    token = getattr(user, "access_token", None)
    if token is None:
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
    return token


def build_server(conn_factory: Callable[[], psycopg.Connection] | None = None, actor: str | None = None,
                 auth: Any = None) -> FastMCP:
    """Create the FastMCP server with every tool in ``TOOL_NAMES`` registered.

    Args:
        conn_factory: opens a connection per tool call (default ``db.connection_factory()``,
            which honours DATABASE_URL and SDLC_SCHEMA). Tests pass one bound to their schema.
        actor: the fixed identity of every call (default ``$SDLC_ACTOR`` or ``agent:mcp``).
        auth: an ``sdlc_graph.mcp_auth.AuthConfig``: bearer JWTs are then required on the HTTP transport and the
            protected-resource metadata is served (stdio never checks them). Default None: no auth.

    Returns:
        A ``mcp.server.fastmcp.FastMCP`` instance (not started).
    """
    factory = conn_factory
    identity = server_actor(actor)
    if auth is not None:
        from sdlc_graph.mcp_auth import JwtVerifier

        mcp = FastMCP(SERVER_NAME, auth=auth.settings(), token_verifier=JwtVerifier(auth))
    else:
        mcp = FastMCP(SERVER_NAME)

    def caller() -> str:
        """Who this call acts as. Without OAuth (stdio, or a loopback --insecure-no-auth server): the fixed
        ``identity``. With OAuth: ``agent:oauth/<sub>`` of the verified token on this request (audit attribution;
        still an agent, so the human-only and CI-only transitions stay refused). No verified subject: refused."""
        if auth is None:
            return identity
        token = _request_access_token(mcp)
        sub = getattr(token, "subject", None) or ((getattr(token, "claims", None) or {}).get("sub"))
        if not isinstance(sub, str) or not sub.strip():
            raise PermissionError("OAuth is on and this call carries no verified token subject")
        need = tuple(getattr(auth, "write_scopes", ()) or ())
        have = set(getattr(token, "scopes", None) or [])
        if need and not have.intersection(need):  # every caller() user is a write: reads never call it
            raise PermissionError(f"this tool writes, and the token carries none of the write scopes "
                                  f"({', '.join(need)}); ask the issuer for one")
        return oauth_actor(sub)

    def run(fn: Callable[[psycopg.Connection], Any]) -> Any:
        """Open a connection, run ``fn(conn)``, close it; exceptions become an error dict."""
        if not _status().enabled():  # graph.enabled false in .claude/sdlc.json: nothing is opened
            return _status().graph_off()
        try:
            conn = (factory or _default_factory())()
        except Exception as exc:  # database unreachable, bad URL, refused login: the graph is not configured
            return _status().not_configured(_redact(exc))
        try:
            result = fn(conn)
            # MCP would split a bare list into one content block per item; keep it one JSON value.
            return {"ok": True, "result": result} if isinstance(result, list) else result
        except psycopg.errors.UndefinedTable as exc:  # reachable, but `sdlc-graph db init` never ran here
            return _status().not_configured(_redact(exc), hint=_status().HINT_NO_SCHEMA)
        except Exception as exc:
            return _error(exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def snap(conn: psycopg.Connection, repo: str | None, sha: str | None) -> int:
        return _lib("codegraph.snapshot").resolve_snapshot(conn, repo, sha)

    # ----------------------------------------------------------------- read-only tools

    @mcp.tool(annotations=_READ_ONLY)
    def graph_status() -> Any:
        """Is the graph usable here? {configured, enabled, provider, reachable, schema_version, snapshots, nodes, hint}.
        Call it first. configured false: print the hint as one line ("graph off ..." when enabled is false, "graph on
        but not reachable: run /factory:graph provision <provider> ..." otherwise) and skip the graph steps; every
        other tool answers "graph off" or "graph not configured" then."""
        return _status().graph_status(factory)

    @mcp.tool(annotations=_READ_ONLY)
    def graph_deps(symbol: str, depth: int = 1, direction: str = "out", rels: list[str] | None = None,
                   repo: str | None = None, sha: str | None = None) -> Any:
        """Neighbours of a symbol in the derived code graph. `symbol` is a key `kind:qualname@path`
        or a qualname; `direction` is out, in or both; `rels` filters edge kinds."""
        return run(lambda c: _lib("codegraph.queries").deps(c, snap(c, repo, sha), symbol, depth=depth,
                                                             direction=direction, rels=rels))

    @mcp.tool(annotations=_READ_ONLY)
    def graph_impact(paths: list[str] | None = None, symbols: list[str] | None = None, depth: int = 4,
                     repo: str | None = None, sha: str | None = None) -> Any:
        """Affected set (symbols, routes, tests, owners, reasons) for changed paths and/or symbols.
        A proposal only; nothing is written."""
        return run(lambda c: _lib("impact.affected").affected(c, snap(c, repo, sha), paths=paths,
                                                               symbols=symbols, depth=depth))

    @mcp.tool(annotations=_READ_ONLY)
    def graph_owners(paths: list[str], repo: str, sha: str | None = None) -> Any:
        """CODEOWNERS handles per path (last matching rule wins), from the rules of commit `sha`
        (default: the newest indexed commit of `repo`)."""
        return run(lambda c: _lib("codegraph.queries").owners(c, repo, paths, sha=sha))

    @mcp.tool(annotations=_READ_ONLY)
    def graph_search(text: str, kind: str | None = None, limit: int = 20, repo: str | None = None,
                     sha: str | None = None) -> Any:
        """Search symbols by name or text, optionally filtered by symbol kind. Hybrid (embeddings plus
        full-text) when the index has been built with `sdlc-graph index build`, else name/trigram match."""
        return run(lambda c: _lib("index.search").graph_search(c, snap(c, repo, sha), text, kind=kind, limit=limit))

    @mcp.tool(annotations=_READ_ONLY)
    def node_search(text: str, kind: str | None = None, repo: str | None = None, limit: int = 20) -> Any:
        """Search ledger nodes (epics, contracts, rules, incidents, ...) by meaning and words: the section
        of each node that matched, best first. Hybrid when the index is built, else a title/slug match."""
        return run(lambda c: _lib("index.search").node_search(c, text, kind=kind, repo=repo, limit=limit))

    @mcp.tool(annotations=_READ_ONLY)
    def incident_similar(text: str, k: int = 3, repo: str | None = None) -> Any:
        """Past incidents nearest `text` and what closed each (the rule, test or contract linked by caused_by,
        governed_by or supersedes). If one exists, the new incident's triage is "the rule did not fire", not
        "write a rule". Hybrid when the index is built, else a title match."""
        return run(lambda c: _lib("index.search").similar_incidents(c, text, k, repo=repo))

    @mcp.tool(annotations=_READ_ONLY)
    def intake_context(repo: str, title: str, ticket_text: str = "", paths: list[str] | None = None) -> Any:
        """Before a contract is written: open contracts within 0.15 cosine distance of the ticket (a duplicate
        candidate: quote it back and stop), up to 3 prior contracts on the same `paths` (their decisions, test
        names, reviewer findings) and the rule files whose paths: match them. Writes nothing; empty lists when
        the index is not built."""
        return run(lambda c: _lib("index.search").intake_context(c, repo=repo, title=title, ticket_text=ticket_text,
                                                                 paths=paths or []))

    @mcp.tool(annotations=_READ_ONLY)
    def graph_snapshot(repo: str | None = None) -> Any:
        """List indexed snapshots (repo, commit sha, parent, storage), newest first; symbol and edge counts come from index or delta."""
        return run(lambda c: _lib("codegraph.snapshot").list_snapshots(c, repo))

    @mcp.tool(annotations=_READ_ONLY)
    def node_get(slug: str) -> Any:
        """One ledger node with its state, open decisions, evidence summary, edges and transitions."""
        return run(lambda c: _lib("ledger.nodes").get(c, slug))

    @mcp.tool(annotations=_READ_ONLY)
    def node_neighbours(slug: str, rel: str | None = None, direction: str = "both") -> Any:
        """Ledger nodes linked to `slug`, optionally filtered by edge `rel`; direction out, in or both."""
        return run(lambda c: _lib("ledger.nodes").neighbours(c, slug, rel=rel, direction=direction))

    @mcp.tool(annotations=_READ_ONLY)
    def workflow_next(slug: str) -> Any:
        """The next allowed step for a node: command, stage, model tier, missing evidence, Fable budget."""
        return run(lambda c: _lib("ledger.workflow").next_step(c, slug))

    @mcp.tool(annotations=_READ_ONLY)
    def epic_decompose(slug: str) -> Any:
        """PROPOSED feature nodes from an epic's markdown file (the node's path), walking skeleton first: title, slug
        suggestion, order, depends_on, flags (not_vertical, needs_slicing, exists) and a reason each. Nothing is
        created; a human accepts each proposal through the CLI's node create."""
        return run(lambda c: _lib("ledger.decompose").decompose(c, slug))

    @mcp.tool(annotations=_READ_ONLY)
    def metrics_snapshot(repo: str | None = None, since: str | None = None) -> Any:
        """Per-stage metrics computed from ledger rows (ISO timestamp for `since`)."""
        return run(lambda c: _lib("ledger.metrics").snapshot(c, repo=repo, since=since))

    # ----------------------------------------------------------------- guarded writes

    @mcp.tool(annotations=_GUARDED_WRITE)
    def contract_draft(slug: str, title: str, repo: str, ticket_text: str, feature: str | None = None,
                       touched_symbols: list[str] | None = None, capabilities: dict | None = None,
                       acceptance: list[dict] | None = None, depends_on: list[str] | None = None,
                       tier: str | None = None, defaults: list[str] | None = None,
                       contract_md: str | None = None, strict: bool = True,
                       allow_duplicate: bool = False) -> Any:
        """Draft a contract through intake: scans the ticket, dedupes, applies the overlap rule.
        Pass the WHOLE contract.md text, unedited, as `contract_md` (the file /factory:ticket writes at
        .claude/work/<slug>/contract.md): its Allowed paths become touched_symbols, and its Capabilities, Defaults
        when ambiguous, Acceptance criteria, Risk tier and Decisions table go onto the node, unless you pass that
        argument explicitly. With strict=true (the default) a draft whose blocks are incomplete is refused: status
        `refused`, nothing created, `blocks.missing` naming each missing block. Treat that as a stop: fix the
        contract.md and draft again; do not drop strict or invent the blocks. Returns status created, conflict,
        duplicate or refused; never marks anything ready. A `duplicate` lists the open contract(s) that match
        (slug, title, state, distance); show them to the human and stop. Pass allow_duplicate=true only after the
        human said it is a different contract. The result also carries `precedent` (prior contracts on the same
        paths) and `rules` (rule files whose paths match) to paste into the contract."""
        return run(lambda c: _lib("ledger.intake").contract_draft(
            c, slug=slug, title=title, repo=repo, ticket_text=ticket_text, actor=caller(), feature=feature,
            touched_symbols=touched_symbols, capabilities=capabilities, acceptance=acceptance,
            depends_on=depends_on, tier=tier, defaults=defaults, contract_text=contract_md, strict=strict,
            allow_duplicate=allow_duplicate))

    @mcp.tool(annotations=_GUARDED_WRITE)
    def contract_update(slug: str, contract_md: str) -> Any:
        """Re-read the WHOLE contract.md onto a contract that is drafted or ready (refused from tests_frozen on):
        replaces its touched paths, capabilities, defaults, acceptance, tier and decisions, and records a
        `contract_updated` evidence row naming the changed fields and the decisions newly confirmed. A decision
        counts as confirmed only when its Decided by names a human @handle; this server acts as an agent, so it
        never confirms one itself. Once the contract is ready, this server may change only its decisions."""
        return run(lambda c: _lib("ledger.intake").contract_update(c, slug, contract_md, actor=caller()))

    @mcp.tool(annotations=_GUARDED_WRITE)
    def contract_transition(slug: str, to_state: str, evidence_ids: list[int] | None = None,
                            reason: str | None = None) -> Any:
        """Evaluate the guard and move the node, or return the missing evidence. This server acts as an
        agent, which is refused for approved, merged, released and closed whatever the evidence."""
        if to_state in PROTECTED_STATES:
            try:
                who = caller()
            except PermissionError as exc:
                return _error(exc)
            return {"ok": False, "slug": slug, "to": to_state,
                    "missing": ["actor:not_agent"],
                    "error": {"type": "forbidden",
                              "message": f"{who} may not move a node into {to_state}; a human or CI actor must"}}
        return run(lambda c: _lib("ledger.transitions").transition(
            c, slug, to_state, caller(), evidence_ids=evidence_ids, reason=reason))

    @mcp.tool(annotations=_GUARDED_WRITE)
    def evidence_add(slug: str, kind: str, payload: dict) -> Any:
        """Store local, advisory evidence on a node. Binding (CI-signed) evidence is ingested by CI
        through the CLI; this tool cannot write it."""
        return run(lambda c: _lib("ledger.evidence").add(c, slug, kind, payload, source="local",
                                                                     actor=caller()))

    @mcp.tool(annotations=_GUARDED_WRITE)
    def decision_propose(slug: str, question: str, options: list[str],
                         recommendation: str | None = None, due: str | None = None) -> Any:
        """Open a decision on a node for a human to resolve."""
        return run(lambda c: _lib("ledger.decisions").propose(c, slug, question, options, actor=caller(),
                                                               recommendation=recommendation, due=due))

    @mcp.tool(annotations=_GUARDED_WRITE)
    def decision_resolve(decision_id: str, choice: str) -> Any:
        """Always refused here, by design: only a human (`@handle`) may resolve a decision, and every actor this
        server can be is an agent. Humans resolve with `sdlc-graph decision resolve --actor @you`. The tool stays
        listed so an agent that tries gets a clear refusal naming the CLI rather than "unknown tool"."""
        try:
            who = caller()
        except PermissionError as exc:
            return _error(exc)
        return {"ok": False, "error": {"type": "forbidden",
                                       "message": f"{who} may not resolve decisions; only a human (@handle) may, "
                                                  "through the CLI"}}

    @mcp.tool(annotations=_GUARDED_WRITE)
    def run_start(stage: str, model: str, slug: str | None = None, tool_use_id: str | None = None) -> Any:
        """Record the start of an agent run: which stage, which node, which model."""
        return run(lambda c: _lib("ledger.runs").start(c, stage=stage, requested_model=model, slug=slug,
                                                       tool_use_id=tool_use_id, actor=caller()))

    @mcp.tool(annotations=_GUARDED_WRITE)
    def run_finish(run_id: str, outcome: str, usage: dict | None = None) -> Any:
        """Record the outcome and token usage of a run."""
        return run(lambda c: _lib("ledger.runs").finish(c, run_id, outcome=outcome, usage=usage,
                                                        actor=caller()))

    @mcp.tool(annotations=_GUARDED_WRITE)
    def release_request(contract_slugs: list[str] | None = None, feature_slug: str | None = None,
                        slug: str | None = None) -> Any:
        """Open a `release` node in `requested`, with a `shipped_in` edge from each contract (or from the
        contracts of `feature_slug`). Refused unless every contract is `merged`. This server cannot move the
        release into `released` (a human does, through the CLI), nor into `verified` or `rolled_back`."""
        return run(lambda c: _lib("ledger.release").request(
            c, actor=caller(), contract_slugs=contract_slugs, feature_slug=feature_slug, slug=slug))

    # ----------------------------------------------------------------- resources (read-only)

    def node_json(kind: str, slug: str) -> str:
        def load(c: psycopg.Connection) -> Any:
            view = _lib("ledger.nodes").get(c, slug)
            actual = (view.get("node") or {}).get("kind")
            if actual != kind:
                raise LookupError(f"{slug} is a {actual}, not a {kind}")
            return view

        return json.dumps(run(load), indent=2, default=str)

    @mcp.resource("contract://{slug}", mime_type="application/json")
    def contract_resource(slug: str) -> str:
        """A contract node (view JSON)."""
        return node_json("contract", slug)

    @mcp.resource("epic://{slug}", mime_type="application/json")
    def epic_resource(slug: str) -> str:
        """An epic node (view JSON)."""
        return node_json("epic", slug)

    @mcp.resource("rules://{repo}/{glob}", mime_type="application/json")
    def rules_resource(repo: str, glob: str) -> str:
        """Rule nodes of `repo` whose path matches the glob (nodes without a path always match).
        The SDK's template matches one path segment, so slashes in the glob are sent percent-encoded
        (`app%2Froutes%2F**`); `**` and `*` both match across slashes (fnmatch)."""
        glob = unquote(glob)

        def load(c: psycopg.Connection) -> Any:
            rows = c.execute("SELECT slug, title, path, state, attrs FROM node WHERE kind = 'rule' AND repo = %s "
                             "ORDER BY slug", (repo,)).fetchall()
            return {"repo": repo, "glob": glob,
                    "rules": [r for r in rows if not r["path"] or fnmatch.fnmatch(r["path"], glob)]}

        return json.dumps(run(load), indent=2, default=str)

    return mcp


def main(argv: list[str] | None = None) -> int:
    """Run the server over stdio (default) or Streamable HTTP with ``--http [--port 8765]``.
    Blocks until the client disconnects. Returns the process exit code."""
    parser = argparse.ArgumentParser(prog="sdlc-graph-mcp", description="graph MCP server (code graph and ledger)")
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--actor", default=None, help="fixed agent:<alias> identity (default $SDLC_ACTOR or agent:mcp)")
    parser.add_argument("--insecure-no-auth", action="store_true",
                        help="with --http and no SDLC_MCP_OAUTH_* configuration: serve without auth, on 127.0.0.1 only")
    args = parser.parse_args(argv)
    auth = None
    if args.http:
        from sdlc_graph import mcp_auth

        try:
            auth = mcp_auth.from_env(host=args.host, port=args.port)
        except mcp_auth.AuthConfigError as exc:
            parser.error(str(exc))
        if auth is None:
            if not args.insecure_no_auth:
                parser.error("--http needs OAuth: set SDLC_MCP_OAUTH_ISSUER and SDLC_MCP_OAUTH_AUDIENCE "
                             "(docs/graph.md), or pass --insecure-no-auth for a local, unauthenticated server")
            if args.host not in ("127.0.0.1", "localhost", "::1"):
                parser.error("--insecure-no-auth binds to 127.0.0.1 only; drop --host or configure OAuth")
    server = build_server(actor=args.actor, auth=auth)
    if args.http:
        server.settings.host = args.host
        server.settings.port = args.port
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
