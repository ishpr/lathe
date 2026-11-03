"""Ask before, never during (docs/DESIGN.md section 14): what the orchestrator does so an
unattended step never waits on a question or a permission prompt.

Three pieces, each used by dispatch, the gates or the loop:

**The allow-list** (:func:`allowed_tools`). A step's ``--allowedTools`` is compiled, never typed: the
pack's base list, read from the pack's own ``scripts/run_headless.sh`` (its ``DEFAULT_TOOLS='...'``
line and the per-plugin-root template line; nothing is copied here), plus the orchestrator's own
tools (``Agent``, ``TodoWrite``, the graph MCP read tools and ``evidence_add``), plus the stack's
test, lint, format and typecheck commands from ``.claude/sdlc.json`` (over the pack's
``stacks/<stack>.json`` defaults; a ``{file}`` placeholder ends the prefix), plus the contract's
compiled capabilities (``commands`` -> ``Bash(<command> *)``, ``paths`` -> ``Edit(./<path>)`` in the
worktree, the session's cwd). Two kinds of base rule are dropped: branch creation (``git switch -c``,
``git checkout -b``; the orchestrator owns the branch) always, and push/PR creation outside ``ship``.
The executor always runs with ``--permission-mode dontAsk --permission-prompts none`` and never
``--bare``, so anything outside the list is denied and reported, never waited on.

**Denials** (:func:`denials`). A denial still happening is a contract defect, not a reason to retry.
The session's stream-json is read for ``system`` events whose subtype is ``permission_denied``, the
``result`` event's ``permission_denials`` list, and tool results whose text starts "Permission to use X has
been denied" (nothing looser: a shell "Permission denied" or a test that prints the words is not a denial);
each is matched to its ``tool_use`` and reported with the exact tool and command or path and the rule that
would have allowed it (and where to declare it). A PreToolUse hook block in the stream ("hook blocking error
... Blocked: <why>" in the tool result, or a ``system``/``hook_response`` event with exit code 2 and "Blocked:" on
stderr) and every record the pack's guards wrote to ``.claude/work/blocks.jsonl`` since the run
started are denials too, with reason ``guard_refused`` and the guard's rule named. The gate turns the step
into ``permission_denied``; the loop opens a P1 decision carrying that text instead of retrying.
Denials of form (:func:`is_form`) are the exception: a Bash command refused only for its shell shape (operators,
a loop, a here-document, a redirection; no part of it forbidden on its own) and the commit guard's message rules.
No rule could allow them and no contract is missing one, so they do not escalate by themselves: the session redoes
the call in an allowed form, the gates judge the step on its evidence as usual, and the run records the denials
(and a failed step's retry is told about them).

**Assumptions** (:func:`ingest_assumptions`, :func:`assumption_issues`). The session applies the
contract's ``## Defaults when ambiguous`` instead of asking and records each applied default in
``.claude/work/<slug>/assumptions.md`` (staged in ``.sdlc-stage/<slug>/assumptions.md`` and moved
with the pack's ``scripts/workfile.py``; Claude Code refuses Write under ``.claude/`` unattended)::

    - default_applied: followed the dependency style of app/auth/api_key.py
      where: app/ratelimit/dependency.py
      why: the contract does not say how the limiter is wired in

(one line ``- default_applied: X | where: Y | why: Z | decision: none`` and a ``| default_applied | where | why |
decision |`` table work too; the pack's table headings ``Default applied`` and ``What was ambiguous`` read as
``default_applied`` and ``why``). ``decision:`` is required: ``none``, or ``#12`` (a ledger decision) or ``D1`` (a row of
the contract.md Decisions table) naming the decision the assumption relies on. After every
step the orchestrator reads the file itself and records ``assumptions`` evidence when it changed
(``{"assumptions": [{default_applied, where, why}], ...}``). The conformance gate fails when
assumptions exist but the contract has no defaults block, when an item is malformed or carries no
``decision:`` field, or when an assumption contradicts a decision (the ledger's resolved ones and the contract.md
Decisions table): it cites a decision without applying its choice, or its ``default_applied`` applies another
option (a long option by its distinctive words, a hyphen is a word edge, "instead of memory" is a contrast and
not an application; mentioning the chosen option as well does not excuse it; options shorter than three
characters and yes/no/none are not matched by text; name the decision instead). In every step that is not the
conformance step, the model judge (:func:`sdlc_graph.orchestrator.judge.assumptions_review`) then reads each
assumption with the decisions inline, because only a model catches "returned 200 when the backend errors" against
"fail closed".
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg

from sdlc_graph.impact import capabilities as caps_mod
from sdlc_graph.ledger import evidence, transitions

from . import ASSUMPTION_KEYS, ASSUMPTIONS_KIND
from .config import TOOL_PREFIXES

PERMISSION_MODE = "dontAsk"
PERMISSION_PROMPTS = "none"
RUN_HEADLESS_ENV = "ORCHESTRATOR_PACK_RUN_HEADLESS"

#: Tools the orchestrator adds for its own protocol (not the pack's list): subagents, the todo list, and
#: the graph MCP tools a step reads with and records evidence through (every server prefix).
ORCH_TOOLS: tuple[str, ...] = (
    "Agent", "TodoWrite",
    *(f"{prefix}{tool}" for prefix in TOOL_PREFIXES
      for tool in ("graph_impact", "node_get", "workflow_next", "evidence_add")),
)
#: Extra rule the ship step needs beyond the pack's push and PR rules.
SHIP_TOOLS: tuple[str, ...] = ("Bash(gh pr view*)",)
#: Base rules no orchestrated step gets (the worktree's branch is the orchestrator's).
ALWAYS_EXCLUDED: tuple[str, ...] = ("Bash(git switch -c", "Bash(git checkout -b")
#: Base rules only ``ship`` gets.
SHIP_ONLY: tuple[str, ...] = ("Bash(git push", "Bash(gh pr create", "Bash(gh pr merge")
#: Stack command keys compiled into Bash rules (test, lint and format; typecheck is a lint).
STACK_COMMAND_KEYS: tuple[str, ...] = ("test", "verify", "lint", "lint_fix", "lint_report", "format", "typecheck")


# --------------------------------------------------------------------------- allow-list


def split_rules(text: str) -> list[str]:
    """Split a space-separated tool list, keeping spaces inside parentheses (``Bash(make *)``)."""
    out, cur, depth = [], [], 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        if ch.isspace() and depth == 0:
            if cur:
                out.append("".join(cur))
                cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def find_run_headless(plugin_dirs: list[str]) -> Path:
    """The pack's ``run_headless.sh``: ``$ORCHESTRATOR_PACK_RUN_HEADLESS``, else the first plugin dir that
    has ``scripts/run_headless.sh``, else this plugin's own root.

    Raises:
        FileNotFoundError: none exists (a step without the pack's list would run with no base rules).
    """
    from .config import SDLC_PACK_ROOT

    env = os.environ.get(RUN_HEADLESS_ENV)
    cands = [Path(env)] if env else []
    cands += [Path(d) / "scripts" / "run_headless.sh" for d in plugin_dirs]
    cands.append(SDLC_PACK_ROOT / "scripts" / "run_headless.sh")
    for c in cands:
        if c.is_file():
            return c.resolve()
    raise FileNotFoundError("the pack's scripts/run_headless.sh was not found (set ORCHESTRATOR_PACK_RUN_HEADLESS "
                            "or pass its plugin dir); it is the source of the base --allowedTools list")


_BASE = re.compile(r"^\s*DEFAULT_TOOLS='([^']*)'\s*$", re.M)
_ROOT_TEMPLATE = re.compile(r'^\s*DEFAULT_TOOLS="\$DEFAULT_TOOLS (.*)"\s*$', re.M)


def pack_base_tools(run_headless: Path, plugin_roots: list[str]) -> list[str]:
    """The pack's default list and its per-plugin-root rules (``$pr`` substituted), parsed from the script.

    Raises:
        ValueError: the script no longer has a ``DEFAULT_TOOLS='...'`` line (the pack changed shape).
    """
    text = run_headless.read_text(encoding="utf-8")
    m = _BASE.search(text)
    if not m:
        raise ValueError(f"{run_headless}: no DEFAULT_TOOLS='...' line to read the base allow list from")
    rules = split_rules(m.group(1))
    t = _ROOT_TEMPLATE.search(text)
    if t:
        template = t.group(1).replace('\\"', '"')
        for root in plugin_roots:
            if any(c.isspace() for c in root):
                continue   # --allowedTools cannot carry whitespace (run_headless.sh skips these too)
            rules += split_rules(template.replace("$pr", root))
    return rules


def stack_command_rules(repo: Path, pack_root: Path | None) -> tuple[list[str], list[str]]:
    """``(rules, notes)`` for the stack's test/lint/format commands: ``.claude/sdlc.json`` ``commands``
    over the pack's ``stacks/<stack>.json`` defaults. A command a rule cannot carry safely is skipped and noted."""
    cfg: dict[str, Any] = {}
    try:
        cfg = json.loads((repo / ".claude" / "sdlc.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    cmds: dict[str, Any] = {}
    stack = cfg.get("stack") if isinstance(cfg, dict) else None
    if pack_root and isinstance(stack, str) and re.fullmatch(r"[a-z0-9_-]+", stack):
        try:
            cmds.update(json.loads((pack_root / "stacks" / f"{stack}.json").read_text(encoding="utf-8")).get("commands") or {})
        except (OSError, ValueError):
            pass
    if isinstance(cfg, dict) and isinstance(cfg.get("commands"), dict):
        cmds.update(cfg["commands"])
    rules, notes = [], []
    for key in STACK_COMMAND_KEYS:
        cmd = cmds.get(key)
        if not isinstance(cmd, str) or not cmd.strip():
            continue
        prefix = cmd.split("{", 1)[0].strip()
        why = caps_mod.command_problem(prefix) if prefix else "is empty before its placeholder"
        if why:
            notes.append(f"stack command {key} ({cmd!r}) not allowed: {why}")
            continue
        rules.append(f"Bash({prefix} *)")
    return rules, notes


def allowed_tools(node: dict[str, Any], step_name: str, cfg: Any) -> dict[str, Any]:
    """The step's ``--allowedTools``. Returns ``{"tools": [...], "sources": {name: [...]}, "notes": [...],
    "run_headless": path}``; ``tools`` keeps the order base, orchestrator, stack, contract, without repeats.

    Raises:
        FileNotFoundError / ValueError: the pack's run_headless.sh is missing or changed shape.
    """
    rh = find_run_headless(list(cfg.plugin_dirs))
    pack_root = rh.parent.parent
    roots = list(dict.fromkeys([str(pack_root), *[str(Path(d).resolve()) for d in cfg.plugin_dirs if Path(d).is_dir()]]))
    base = pack_base_tools(rh, roots)
    dropped = [r for r in base if r.startswith(ALWAYS_EXCLUDED) or (step_name != "ship" and r.startswith(SHIP_ONLY))]
    base = [r for r in base if r not in dropped]
    orch = list(ORCH_TOOLS) + (list(SHIP_TOOLS) if step_name == "ship" else [])
    stack, notes = stack_command_rules(Path(cfg.repo), pack_root)
    caps = node.get("capabilities") or {}
    contract = list(caps_mod.compile_full(caps)["settings"]["permissions"]["allow"])
    tools = list(dict.fromkeys([*base, *orch, *stack, *contract]))
    if dropped:
        notes.append("not allowed in this step: " + ", ".join(dropped))
    return {"tools": tools, "sources": {"pack": base, "orchestrator": orch, "stack": stack, "contract": contract},
            "notes": notes, "run_headless": str(rh)}


# --------------------------------------------------------------------------- denials

# Claude Code's own denial, and nothing looser: the result text starts "Permission to use Write has been denied
# because Claude Code is running in don't ask mode". A shell "Permission denied" or a failing test that prints the
# words is not a denial (review finding: any is_error result containing "permission denied" escalated a 403 story's
# red run, and a PreToolUse hook block was never seen).
_DENIED_TEXT = re.compile(r"\A\s*Permission to use (\S+?)(?: with [^\n]*?)? has been denied\b", re.I)
# A PreToolUse hook block as the stream shows it: "PreToolUse:Bash hook blocking error from command: ...: Blocked: <why>".
_HOOK_BLOCK = re.compile(r"\A\s*(?:\w+:)?(\w+)?\s*hook (?:blocking )?(?:error|blocked)\b.*?\bBlocked:\s*(.+)\Z", re.I | re.S)
GUARD_REFUSED = "guard_refused"
BLOCKS_LOG = ".claude/work/blocks.jsonl"


def _rel(path: str, wt: Path | None) -> str:
    if wt is not None:
        try:
            return os.path.relpath(os.path.realpath(path), os.path.realpath(wt))
        except ValueError:
            return path
    return path


def describe(tool: str, tool_input: Any, wt: Path | None = None) -> dict[str, Any]:
    """``{"tool", "target", "rule", "declare"}``: what was denied and the rule that would have allowed it."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    if tool == "Bash":
        cmd = str(inp.get("command") or "").strip()
        why = caps_mod.command_problem(cmd) if cmd else "no command recorded"
        return {"tool": tool, "target": cmd, "rule": f"Bash({cmd} *)" if cmd else "Bash(<command> *)",
                "declare": ("add it to the contract's ## Capabilities commands, or to .claude/sdlc.json commands "
                            "when every step needs it") if not why else
                           f"a contract cannot pre-approve it ({why}); change the skill or the step so it is not needed"}
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
        rel = _rel(path, wt) if path else "<path>"
        if rel.startswith(".claude/") or "/.claude/" in rel:
            return {"tool": tool, "target": rel, "rule": f"Edit(./{rel})",
                    "declare": "Claude Code refuses writes under .claude/ unattended whatever the rules: stage the file in "
                               ".sdlc-stage/ and move it with the pack's scripts/workfile.py"}
        outside = rel.startswith("..") or os.path.isabs(rel)
        return {"tool": tool, "target": rel, "rule": f"Edit(//{path.lstrip('/')})" if outside else f"Edit(./{rel})",
                "declare": "outside the worktree: a contract cannot grant it" if outside else
                           "add the path (or a glob over it) to the contract's ## Capabilities paths"}
    if tool == "Read":
        path = str(inp.get("file_path") or "")
        rel = _rel(path, wt) if path else "<path>"
        outside = rel.startswith("..") or os.path.isabs(rel)
        return {"tool": tool, "target": rel, "rule": f"Read(//{path.lstrip('/')})" if outside else f"Read(./{rel})",
                "declare": "a read outside the worktree; credential stores stay denied" if outside else
                           "a deny rule refused it: list the file in the contract's capabilities secrets only if it is meant to be read"}
    if tool in ("WebFetch", "WebSearch"):
        host = urlparse(str(inp.get("url") or "")).hostname or ""
        return {"tool": tool, "target": str(inp.get("url") or inp.get("query") or ""),
                "rule": f"WebFetch(domain:{host})" if host else tool,
                "declare": "add the host to the contract's ## Capabilities egress" if host else "the contract has no rule for web search"}
    target = json.dumps(inp, sort_keys=True)[:200] if inp else ""
    return {"tool": tool, "target": target, "rule": tool,
            "declare": "an MCP tool: add it to the orchestrator's tool list or the project settings" if tool.startswith("mcp__")
                       else "add the tool to the project settings' permissions.allow"}


#: Shell structure a compound command is split on to judge its parts: control operators, pipes, newlines.
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;&|\n]")
#: A here-document's body (``<<'EOF' ... EOF``): text fed to a command, not commands.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^[ \t]*\2[ \t]*$", re.DOTALL | re.MULTILINE)
#: Redirections (``> f``, ``2>&1``, ``< f``, ``>> f``): the part they belong to is judged without them.
_REDIRECT = re.compile(r"\d*>>?&?\s*\S+|\d*<\s*\S+")
_REDIRECT_TARGET = re.compile(r"\d*>>?&?\s*(\S+)|\d*<\s*(?!<)(\S+)")
#: Shell keywords that open or close a compound statement; the command after them is what runs.
_KEYWORDS = re.compile(r"^(?:do|then|else|elif|if|while|until|!|\{|\()\s+")
_CLOSERS = frozenset({"done", "fi", "esac", "}", ")", "do", "then", "else"})
#: The commit guard's message rules (``check_commit_message.py``): the session rewrites the message, nothing else.
_COMMIT_MESSAGE_WHY = re.compile(r"\Acommit message problems\b", re.IGNORECASE)
#: The commit guard's skeleton rule: a tests commit carried a file with logic in it; the session takes the logic out of
#: the skeleton and runs the same commit again (the console's first contract did exactly that and finished the step).
_SKELETON_WHY = re.compile(r"not a skeleton here", re.IGNORECASE)


def _form_why(why: str) -> bool:
    return bool(_COMMIT_MESSAGE_WHY.match(why) or _SKELETON_WHY.search(why))


def shape_only(command: str) -> bool:
    """Whether a denied Bash ``command`` was refused for its shape alone: it has shell operators, a loop, a
    here-document or a redirection (so no ``Bash(<command> *)`` rule can allow it, and no contract is missing
    one), and none of its parts is something the design forbids on its own (``command_problem``: publishing,
    history rewrites, deletion, a wrapper or an inline-code interpreter). A command substitution (``$(...)``,
    backticks) is never shape only: what it runs is not visible as a part."""
    cmd = (command or "").strip()
    if not cmd or not caps_mod._CMD_FORBIDDEN.search(cmd) or "$(" in cmd or "`" in cmd:
        return False
    body = _HEREDOC.sub("", cmd)
    for m in _REDIRECT_TARGET.finditer(body):
        t = (m.group(1) or m.group(2) or "").strip("'\"")
        if t not in ("/dev/null", "&1", "&2", "1", "2") and (t.startswith(("/", "~")) or ".." in t.split("/")):
            return False      # a redirection out of the worktree is a write the contract cannot grant, not a shape
    for seg in _SEGMENT_SPLIT.split(body):
        seg = _REDIRECT.sub("", seg).strip()
        while (kw := _KEYWORDS.match(seg)) is not None:
            seg = seg[kw.end():].strip()
        if not seg or seg in _CLOSERS or seg.split()[0] in ("for", "case", "select", "in"):
            continue
        # judge the part by its words: a variable or a glob in it ("rm $f", "cat *.md") is shape, the verb is not
        why = caps_mod.command_problem(re.sub(r"[;&|`<>*?\\$\n\r]", "", seg).strip() or seg)
        if why and not why.startswith("may not contain shell operators"):
            return False
    return True


def _guard_item(tool: str, inp: Any, why: str, rule: str, source: str, *, form: bool = False) -> dict[str, Any]:
    inp = inp if isinstance(inp, dict) else {}
    target = str(inp.get("command") or inp.get("file_path") or inp.get("path") or inp.get("url") or "")
    return {"tool": tool, "target": target or json.dumps(inp, sort_keys=True)[:200], "rule": rule,
            "declare": f"the pack's guard refused it on purpose ({why[:200]}); change the step so it is not needed, or a human does it",
            "reason": GUARD_REFUSED, "source": source, "form": form}


def is_form(d: dict[str, Any]) -> bool:
    """A denial of form, not of capability: the commit guard's message rules, or a Bash command refused for its
    shape alone (:func:`shape_only`). Nothing is missing from the contract; the session redoes the call in an
    allowed form (a shorter subject, one command per call, the Write tool). Every other denial (a command, path,
    host or tool no rule allows, any other guard rule) is a capability the step did not have."""
    if d.get("reason") == GUARD_REFUSED:
        return bool(d.get("form"))
    return d.get("tool") == "Bash" and shape_only(str(d.get("target") or ""))


def _blocks_since(wt: Path | None, since: Any) -> list[dict[str, Any]]:
    """The guard hooks' block log (``.claude/work/blocks.jsonl``): one record per deny since ``since``."""
    if wt is None or since is None:
        return []
    import datetime as dt

    try:
        t0 = since if isinstance(since, dt.datetime) else dt.datetime.fromisoformat(str(since))
    except ValueError:
        return []
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=dt.timezone.utc)
    path = wt / BLOCKS_LOG
    out: list[dict[str, Any]] = []
    try:
        if not path.is_file() or path.is_symlink():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            rec = json.loads(line)
            at = dt.datetime.fromisoformat(str(rec["ts"]).replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=dt.timezone.utc)
        if at >= t0 and isinstance(rec, dict) and rec.get("rule") != "crash":
            out.append(rec)
    return out


def denials(log_path: str | Path | None, result: dict[str, Any] | None = None, wt: str | Path | None = None,
            since: Any = None) -> list[dict[str, Any]]:
    """Every denial in the session: Claude Code permission denials from the stream-json (``system`` events of
    subtype ``permission_denied``, the ``result`` event's and the result file's ``permission_denials``, and the
    exact "Permission to use X has been denied" tool result), PreToolUse hook blocks in the stream (the tool
    result's "hook blocking error ... Blocked:" text, and ``system``/``hook_response`` events with exit code 2 and
    "Blocked:" on stderr; never an ``is_error`` result that merely mentions "permission denied"), and, when
    ``since`` (the run's start) is given, the guard hooks' ``.claude/work/blocks.jsonl`` records since then.

    Returns a list of :func:`describe` dicts plus ``source`` (``system``, ``result``, ``tool_result``, ``hook``,
    ``hook_response`` or ``blocks``); guard blocks also carry ``reason: "guard_refused"`` and the guard's rule id. One per denied call
    (deduplicated by tool_use id, else by tool and input)."""
    wtp = Path(wt) if wt else None
    uses: dict[str, dict[str, Any]] = {}
    found: list[tuple[str, str, Any, str]] = []   # (key, tool, input, source)
    guards: list[dict[str, Any]] = []
    hooks: list[tuple[str, Any, str]] = []        # (tool, input, why) from hook_response events
    hooked: set[str] = set()                      # tool_use ids a PreToolUse hook blocked (the guard item stands for them)

    def add(tool: Any, inp: Any, use_id: Any, source: str) -> None:
        if use_id and use_id in uses and (not tool or not inp):
            tool, inp = tool or uses[use_id]["name"], inp or uses[use_id]["input"]
        if not tool:
            return
        key = str(use_id) if use_id else f"{tool}:{json.dumps(inp, sort_keys=True, default=str)}"
        found.append((key, str(tool), inp, source))

    events: list[dict[str, Any]] = []
    if log_path:
        try:
            with open(log_path, errors="replace") as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(ev, dict):
                        events.append(ev)
        except OSError:
            pass
    for ev in events:
        typ = ev.get("type")
        msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        if typ == "assistant":
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                    uses[b["id"]] = {"name": b.get("name"), "input": b.get("input")}
        elif typ == "system" and ev.get("subtype") == "hook_response":
            # A hook's own report (stream-json with hook events). A PreToolUse block is exit 2 with
            # "Blocked:" on stderr; a hook that merely failed, or any other exit, is not a denial.
            name = str(ev.get("hook_name") or "")
            event = str(ev.get("hook_event") or name.partition(":")[0])
            stderr = str(ev.get("stderr") or "")
            b = re.search(r"\bBlocked:\s*(.+)", stderr, re.S)
            if event == "PreToolUse" and ev.get("exit_code") == 2 and b:
                why = b.group(1).strip()
                if ev.get("tool_use_id"):
                    hooked.add(str(ev["tool_use_id"]))
                use = uses.get(ev.get("tool_use_id")) or {}
                tool = use.get("name") or name.partition(":")[2] or "?"
                hooks.append((tool, use.get("input"), why))
        elif typ == "system" and ev.get("subtype") == "permission_denied":
            d = ev.get("denial") if isinstance(ev.get("denial"), dict) else ev
            add(d.get("tool_name") or d.get("tool"), d.get("tool_input") or d.get("input"), d.get("tool_use_id"), "system")
        elif typ == "user":
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error")):
                    continue
                text = b.get("content")
                text = text if isinstance(text, str) else " ".join(
                    x.get("text", "") for x in text or [] if isinstance(x, dict))
                use = uses.get(b.get("tool_use_id")) or {}
                m = _DENIED_TEXT.search(text or "")
                if m:
                    add(use.get("name") or m.group(1), use.get("input"), b.get("tool_use_id"), "tool_result")
                    continue
                h = _HOOK_BLOCK.search(text or "")
                if h:
                    why = h.group(2).strip()
                    if b.get("tool_use_id"):
                        hooked.add(str(b["tool_use_id"]))
                    guards.append(_guard_item(use.get("name") or h.group(1) or "?", use.get("input"), why,
                                              f"guard: {why[:120]}", "hook", form=_form_why(why)))
        elif typ == "result":
            for d in ev.get("permission_denials") or []:
                if isinstance(d, dict):
                    add(d.get("tool_name"), d.get("tool_input"), d.get("tool_use_id"), "result")
    for d in (result or {}).get("permission_denials") or []:
        if isinstance(d, dict):
            add(d.get("tool_name"), d.get("tool_input"), d.get("tool_use_id"), "result")
    out: dict[str, dict[str, Any]] = {}
    for key, tool, inp, source in found:
        # Claude Code lists a call a PreToolUse hook blocked in permission_denials too: the guard item is that denial
        if key in out or key in hooked:
            continue
        out[key] = {**describe(tool, inp, wtp), "source": source}
    # A hook_response block the tool result also shows is the same denial: keep the tool-result one (it names
    # the call's input); a hook_response with no matching tool result still counts.
    shown = {(g["tool"], g["rule"]) for g in guards}
    for tool, inp, why in hooks:
        item = _guard_item(tool, inp, why, f"guard: {why[:120]}", "hook_response", form=_form_why(why))
        if (item["tool"], item["rule"]) not in shown:
            shown.add((item["tool"], item["rule"]))
            guards.append(item)
    seen = {(g["tool"], g["target"]) for g in guards}
    for rec in _blocks_since(wtp, since):
        item = _guard_item(str(rec.get("tool") or "?"), {"command": str(rec.get("detail") or "")}, str(rec.get("rule") or "block"),
                           f"guard {rec.get('hook') or ''}:{rec.get('rule') or ''}".replace(": ", ":").strip(), "blocks",
                           form=rec.get("hook") == "guard_bash" and rec.get("rule") == "commit-message")
        item["declare"] = (f"the pack's {rec.get('hook') or 'guard'} refused it on purpose (rule {rec.get('rule')!r}, "
                           "see .claude/work/blocks.jsonl); change the step so it is not needed, or a human does it")
        # the block log keeps a truncated command: the same refusal the stream already showed (whose text says
        # whether it was one of form) is one denial, not two
        same = next((g for g in guards if g["tool"] == item["tool"] and item["target"] and
                     (g["target"].startswith(item["target"]) or item["target"].startswith(g["target"]))), None)
        if same is not None:
            continue
        if (item["tool"], item["target"]) not in seen:
            seen.add((item["tool"], item["target"]))
            guards.append(item)
    for i, g in enumerate(guards):
        out[f"guard:{i}"] = g
    return list(out.values())


def denial_text(step: str, items: list[dict[str, Any]]) -> str:
    """One paragraph for the P1 decision and the retry prompt."""
    parts = [f"{d['tool']} `{d['target'][:200]}` ("
             + (f"refused by the pack's guard, reason {GUARD_REFUSED}, {d['rule'][:240]}; " if d.get("reason") == GUARD_REFUSED
                else f"rule that would have allowed it: `{d['rule'][:240]}`; ") + f"{d['declare']})"
             for d in items[:8]]
    more = f" and {len(items) - 8} more" if len(items) > 8 else ""
    return (f"The {step} session was denied {len(items)} tool call(s) under --permission-mode {PERMISSION_MODE} "
            f"--permission-prompts {PERMISSION_PROMPTS}: " + "; ".join(parts) + more + ".")


def redone_text(step: str, items: list[dict[str, Any]]) -> str:
    """One line for denials of form (:func:`is_form`): recorded on the run, never an escalation by themselves."""
    parts = [f"{d['tool']} `{' '.join(str(d['target']).split())[:120]}` ("
             + ("the commit guard's message rules" if d.get("reason") == GUARD_REFUSED else "refused for its shell shape")
             + ")" for d in items[:5]]
    more = f" and {len(items) - 5} more" if len(items) > 5 else ""
    return (f"{len(items)} call(s) in the {step} session were refused for their form, not for a missing capability: "
            + "; ".join(parts) + more + ". Use one plain command per Bash call, the Write tool for files, and a commit "
            "subject under 72 characters.")


# --------------------------------------------------------------------------- assumptions

_KEYS = {"default_applied": "default_applied", "default": "default_applied", "default applied": "default_applied",
         "where": "where", "why": "why", "what was ambiguous": "why", "decision": "decision"}
_PAIR = re.compile(r"^\s*\**`?([A-Za-z_ ]+?)`?\**\s*:\s*(.*)$")


def assumptions_file(wt: str | Path, slug: str) -> Path:
    return Path(wt) / ".claude" / "work" / slug / "assumptions.md"


def _pairs(text: str) -> dict[str, str]:
    item: dict[str, str] = {}
    for chunk in text.split(" | ") if " | " in text else [text]:
        m = _PAIR.match(chunk)
        if m and m.group(1).strip().lower() in _KEYS:
            item[_KEYS[m.group(1).strip().lower()]] = m.group(2).strip()
    return item


def parse_assumptions(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """``(items, problems)`` from assumptions.md: bullets with ``key: value`` lines (or one line with
    `` | `` separators), or a markdown table whose header names default_applied, where and why."""
    items: list[dict[str, str]] = []
    problems: list[str] = []
    lines = text.splitlines()
    header: list[str] | None = None
    cur: dict[str, str] | None = None
    last_key: str | None = None

    def close() -> None:
        nonlocal cur
        if cur:
            items.append(cur)
        cur = None

    for ln in lines:
        s = ln.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip().strip("`") for c in s.strip("|").split("|")]
            if header is None:
                names = [_KEYS.get(c.lower()) for c in cells]
                if "default_applied" in names:
                    header = [n or "" for n in names]
                continue
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            close()
            items.append({k: v for k, v in zip(header, cells) if k})
            continue
        header = None
        m = re.match(r"^[-*+]\s+(.*)$", s)
        if m and len(ln) - len(ln.lstrip()) < 2:          # a top-level bullet starts an item
            close()
            cur = _pairs(m.group(1))
            if not cur:
                cur = {"default_applied": m.group(1).strip()}
            last_key = next(reversed(cur))
            continue
        if cur is not None and s:                          # a key line, or a continuation of the last value
            pairs = _pairs(re.sub(r"^[-*+]\s+", "", s))
            if pairs:
                cur.update(pairs)
                last_key = next(reversed(pairs))
            elif last_key:
                cur[last_key] = f"{cur[last_key]} {s}".strip()
    close()
    good: list[dict[str, str]] = []
    for i, it in enumerate(items, 1):
        lacking = [k for k in ASSUMPTION_KEYS if not (it.get(k) or "").strip()]
        if lacking:
            problems.append(f"assumption {i} ({(it.get('default_applied') or '?')[:80]}) is missing {', '.join(lacking)}")
            continue
        good.append({k: v.strip() for k, v in it.items() if v and v.strip()})
    return good, problems


def latest_assumptions(conn: psycopg.Connection, slug: str) -> dict[str, Any] | None:
    rows = evidence.list_for(conn, slug, ASSUMPTIONS_KIND)
    return max(rows, key=lambda r: (r["at"], r["id"])) if rows else None


def ingest_assumptions(conn: psycopg.Connection, slug: str, wt: str | Path, *, run_id: Any, step: str,
                       started_at: Any = None) -> dict[str, Any]:
    """Read the worktree's assumptions.md (plus any ``assumptions`` rows the session recorded itself since
    ``started_at``), and record ``assumptions`` evidence when the set differs from the newest row.

    Returns ``{"assumptions": [...], "problems": [...], "evidence_id": id | None, "recorded": bool}``."""
    path = assumptions_file(wt, slug)
    items: list[dict[str, str]] = []
    problems: list[str] = []
    try:
        if path.is_file() and not path.is_symlink():
            items, problems = parse_assumptions(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        problems.append(f"assumptions.md could not be read: {exc}")
    prev = latest_assumptions(conn, slug)
    # The file is the record the skills mandate and the orchestrator parses it itself. A row the session recorded on
    # top of it (a convenience for reviewers) that lacks keys is noted, not held against the step, while the file is
    # there and well-formed; with no usable file those rows are the only record and their gaps count. The console's
    # third contract lost its implementation's conformance to a row recorded under other key names.
    file_ok = bool(items) and not problems
    notes: list[str] = []
    if started_at is not None:
        import datetime as dt

        t0 = started_at if isinstance(started_at, dt.datetime) else dt.datetime.fromisoformat(str(started_at))
        for row in evidence.list_for(conn, slug, ASSUMPTIONS_KIND):
            at = row["at"] if isinstance(row["at"], dt.datetime) else dt.datetime.fromisoformat(str(row["at"]))
            if at >= t0 and (row["payload"] or {}).get("ingested_by") != "orchestrator":
                for a in evidence.assumption_items(row["payload"]):
                    lacking = [k for k in ASSUMPTION_KEYS if not str(a.get(k) or "").strip()]
                    if lacking:
                        (notes if file_ok else problems).append(
                            f"assumption recorded by the session (evidence #{row['id']}, "
                            f"{str(a.get('default_applied') or '?')[:80]}) is missing {', '.join(lacking)}"
                            + ("; the file's items stand" if file_ok else ""))
                    else:
                        items.append(a)
    seen: set[tuple[str, ...]] = set()
    uniq = []
    for a in items:
        key = tuple(str(a.get(k, "")).strip() for k in ASSUMPTION_KEYS)
        if key not in seen:
            seen.add(key)
            uniq.append(a)
    previous = [a for a in ((prev or {}).get("payload") or {}).get("assumptions") or []]
    if not uniq or uniq == previous:
        return {"assumptions": uniq or previous, "problems": problems, "notes": notes,
                "evidence_id": prev["id"] if prev else None, "recorded": False}
    row = evidence.add(conn, slug, ASSUMPTIONS_KIND, {
        "assumptions": uniq, "step": step, "run_id": run_id, "file": str(path.relative_to(wt)) if path.is_file() else None,
        "ingested_by": "orchestrator"})
    return {"assumptions": uniq, "problems": problems, "notes": notes, "evidence_id": row["id"], "recorded": True}


_WEAK_OPTIONS = {"yes", "no", "none", "true", "false", "both", "either", "other", "n/a", "ok", "skip", "retry"}
_STOP = frozenset("the and for with per use using used value option into from that this when then than only also not are was "
                  "has have per each any all can may will".split())
#: A word right before an option that makes the mention a contrast ("rather than memory"), not an application of it.
_CONTRAST = re.compile(r"(?:instead\s+of|rather\s+than|not|never|no|over|versus|vs\.?|unlike|than|without|avoid(?:ed|ing)?|"
                       r"reject(?:ed|ing)?|except)\s+(?:\w+\s+){0,2}$", re.I)


def _values(option: Any) -> list[str]:
    if isinstance(option, dict):
        return [str(option[k]) for k in ("id", "label") if option.get(k)]
    return [str(option)] if option is not None else []


def _mention_at(text: str, phrase: str) -> int | None:
    """Index of ``phrase`` as whole words in ``text`` (a hyphen before or after is a word edge: ``in-memory``)."""
    m = re.search(r"(?<!\w)" + re.escape(phrase.lower()) + r"(?!\w)", text.lower())
    return m.start() if m else None


def _mentions(text: str, phrase: str) -> bool:
    return _mention_at(text, phrase) is not None


def _applies_choice(applied: str, choice: str) -> bool:
    """The assumption applies a decision's choice: it names the choice, or the choice's first clause (``fixed window`` for
    ``fixed window, 100 per minute``)."""
    first = re.split(r"\s*[,;(]\s*", choice.strip(), maxsplit=1)[0]
    return _mentions(applied, choice) or (len(first) >= 3 and _mentions(applied, first))


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in _STOP}


def _option_hit(applied: str, option: str, choice: str) -> str | None:
    """The phrase of ``option`` the assumption applies (not merely contrasts with), else None. A long option is
    matched by its distinctive words (those the chosen option lacks), so ``a sliding approach`` hits the option
    ``sliding window`` when the choice is ``fixed window``."""
    phrases = [option.strip()]
    distinct = _words(option) - _words(choice)
    if len(_words(option)) >= 2:
        phrases += sorted(w for w in distinct if len(w) >= 4)
    for ph in phrases:
        at = _mention_at(applied, ph)
        if at is None:
            continue
        if _CONTRAST.search(applied[max(0, at - 40):at]):
            continue
        return ph
    return None


def resolved_decisions(conn: psycopg.Connection, slug: str) -> list[dict[str, Any]]:
    return conn.execute(
        """SELECT d.id, d.question, d.options, d.choice, d.severity FROM decision d JOIN node n ON n.id = d.node_id
           WHERE n.slug = %s AND d.resolved_at IS NOT NULL ORDER BY d.id""", (slug,)).fetchall()


def contract_decisions(wt: str | Path | None, slug: str) -> list[dict[str, Any]]:
    """The contract.md ``## Decisions`` table rows as decisions (``id`` like ``D1``, ``options`` the Options cell split,
    ``choice`` the Value); rows with no Value are skipped (acceptance.py --ready refuses those before a run)."""
    if wt is None:
        return []
    path = Path(wt) / ".claude" / "work" / slug / "contract.md"
    try:
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() and not path.is_symlink() else ""
    except OSError:
        return []
    m = re.search(r"^##\s+Decisions\s*$(.*?)(?=^##\s|\Z)", re.sub(r"<!--.*?-->", "", text, flags=re.S), re.M | re.S)
    if not m:
        return []

    def split(line: str) -> list[str]:
        body = line.strip().strip("|") if not line.strip().endswith("\\|") else line.strip().lstrip("|")
        return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", body)]

    rows = [ln for ln in m.group(1).splitlines() if ln.strip().startswith("|")]
    if len(rows) < 3:
        return []
    head = [c.lower() for c in split(rows[0])]
    if "value" not in head:
        return []
    out = []
    for r in rows[2:]:
        c = split(r)
        if len(c) != len(head):
            continue
        row = dict(zip(head, c))
        value = re.sub(r"[*`]", "", row.get("value", "")).strip()
        if not value or value.startswith("<"):
            continue
        opts = [o.strip() for o in re.split(r"\s*(?:·|;|\||/|,|\bor\b)\s*", row.get("options", "")) if o.strip()]
        out.append({"id": (row.get("#") or "").strip().upper() or f"row{len(out) + 1}", "question": row.get("question", ""),
                    "options": opts, "choice": value, "severity": None})
    return out


def contradictions(decided: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[str]:
    """Assumptions that contradict a resolved decision (rules in the module docstring)."""
    out: list[str] = []
    for i, a in enumerate(items, 1):
        applied = str(a.get("default_applied") or "")
        every = " ".join(str(a.get(k) or "") for k in ("default_applied", "where", "why", "decision"))
        refs = {int(x) for x in re.findall(r"#(\d+)", every)} | {f"D{x}" for x in re.findall(r"(?<![\w#])[Dd](\d+)\b", every)}
        for d in decided:
            choice = str(d.get("choice") or "").strip()
            ident = d["id"]
            tag = f"decision #{ident}" if isinstance(ident, int) else f"decision {ident}"
            label = f"{tag} ({str(d.get('question') or '')[:120]}) chose {choice!r}"
            if (ident in refs or str(ident).upper() in refs) and choice and not _applies_choice(applied, choice):
                out.append(f"assumption {i} ({applied[:120]}) names {tag} but does not apply its choice: {label}")
                continue
            if d.get("severity"):
                continue   # an orchestrator escalation (retry/skip, a free-text answer): matched only by #id
            others = [v for o in d.get("options") or [] for v in _values(o)
                      if v.strip().lower() != choice.lower() and len(v.strip()) >= 3 and v.strip().lower() not in _WEAK_OPTIONS]
            hit = next((h for h in (_option_hit(applied, v, choice) for v in others) if h), None)
            if hit:
                out.append(f"assumption {i} ({applied[:120]}) applies {hit!r}, contradicting {label}")
    return out


def assumption_issues(conn: psycopg.Connection, node: dict[str, Any], found: dict[str, Any],
                      wt: str | Path | None = None) -> list[str]:
    """Why the assumptions fail conformance (empty when they pass).

    Every assumption carries a ``decision`` field: ``none``, or the id of the decision it relies on (``#12`` for the
    ledger, ``D1`` for the contract.md table row); a missing field fails, and a cited decision's choice must be
    applied. Decisions come from the ledger and from the contract.md Decisions table (``wt`` given)."""
    items = found.get("assumptions") or []
    issues = list(found.get("problems") or [])
    if items and not transitions.defaults_ok(node.get("attrs")):
        issues.append(f"{len(items)} assumption(s) recorded but the contract has no ## Defaults when ambiguous block "
                      "to apply them from")
    if items:
        for i, a in enumerate(items, 1):
            ref = str(a.get("decision") or "").strip()
            if not ref:
                issues.append(f"assumption {i} ({str(a.get('default_applied') or '?')[:80]}) has no `decision:` field; write "
                              "`decision: #<id>` (or `D<n>`) of the decision it relies on, or `decision: none`")
        decided = resolved_decisions(conn, node["slug"]) + contract_decisions(wt, node["slug"])
        issues += contradictions(decided, items)
    return issues


