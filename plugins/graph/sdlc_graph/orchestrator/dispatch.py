"""Dispatch one step: worktree, ``.claude/work/current.json`` from the ledger, the executor settings
file, the prompt, then the executor process and its run row.

Step-executor protocol (what ``ORCHESTRATOR_EXECUTOR`` or the bundled ``step_executor.sh`` is
called with; every flag takes a value, ``--plugin-dir`` repeats)::

    <executor> --worktree WT --command /sdlc:<step> --prompt-file P --settings S
               --result-file R --log-file L --session-id UUID --model M --max-turns N
               --budget USD --reviewer @h --allowed-tools-file T
               --permission-mode dontAsk --permission-prompts none [--plugin-dir D]...

The executor runs ONE ``claude -p`` session in WT (no worktree of its own, no push, never ``--bare``) with
exactly the permission mode, prompt policy and ``--allowedTools`` it is given (T holds one rule per line,
compiled by :func:`sdlc_graph.orchestrator.autonomy.allowed_tools`: the pack's base list read from its
``run_headless.sh``, the orchestrator's tools, the stack's test/lint/format commands and the contract's
capabilities), streams stream-json to L and writes R last: ``{"exit_code", "duration_seconds",
"last_assistant_message", "subtype", "is_error", "num_turns", "total_cost_usd", "session_id",
"permission_denials"}`` (the result-file shape of the pack's ``run_headless.sh`` plus session id and the
result event's denials). The process runs in its own process group so the watchdog can stop it.
Environment: an allowlist of the daemon's (``config.clean_env``: PATH, HOME, locale, proxy and CA
variables, ``ANTHROPIC_*``, ``CLAUDE_*``, plus ``ORCHESTRATOR_ENV_PASSTHROUGH``; the GitHub token only for
``ship``), never its database URLs or other credentials, plus ``SDLC_DATABASE_URL`` (and ``DATABASE_URL``
when the daemon had one) carrying the session's ledger URL (``cfg.session_database_url``, a restricted
role, else the daemon's own), ``SDLC_SCHEMA``, ``SDLC_NODE``, ``SDLC_STAGE``, ``ORCH_RUN_ID``,
``ORCH_STEP``, ``ORCH_ATTEMPT``, ``ORCH_EXECUTOR_MODE`` (local|runner), ``ORCH_AUTH_SOURCE`` (what the session's
credential is, detected from this environment: see ``orchestrator.auth``).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import psycopg

from sdlc_graph import db
from sdlc_graph.impact import capabilities as caps_mod
from sdlc_graph.ledger import decisions, nodes, workflow

from . import auth, autonomy, contract_blocks, gates, gitops, store
from . import setup as worktree_setup
from .config import SDLC_GRAPH_ROOT, TOOL_PREFIXES, Config, clean_env
from .steps import Step

#: Popen objects of executors this process started (reaped by the watchdog; avoids zombies).
CHILDREN: dict[int, subprocess.Popen] = {}

#: Tools an executor session may not call: the orchestrator transitions after its gates, and only
#: humans resolve decisions.
DENY_TOOLS = [
    *(f"{prefix}{tool}" for tool in ("contract_transition", "decision_resolve") for prefix in TOOL_PREFIXES),
    # the same operations through the CLI
    "Bash(sdlc-graph*)",
    "Bash(python3 -m sdlc_graph*)",
    "Bash(python -m sdlc_graph*)",
]

TRUNC = 1500


def process_identity(pid: int) -> tuple[str, str] | None:
    """``(start time, command line)`` of the process from ``ps``, or None when there is no such process."""
    try:
        proc = subprocess.run(["ps", "-o", "lstart=,command=", "-p", str(pid)], capture_output=True, text=True, timeout=10,
                              env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = proc.stdout.strip()
    if proc.returncode != 0 or not line:
        return None
    # lstart is five words: "Mon Nov  2 10:00:00 2026"
    parts = line.split(None, 5)
    if len(parts) < 5:
        return None
    return " ".join(parts[:5]), parts[5] if len(parts) > 5 else ""



def _cut(text: str | None, n: int = TRUNC) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " [...]"


def frozen_from_ledger(conn: psycopg.Connection, slug: str, wt: str | Path | None = None) -> tuple[dict[str, str], str | None]:
    """``(files, commit)`` from the newest ``tests_frozen`` evidence that still describes the branch (not
    superseded, and on a commit reachable from ``wt``'s HEAD when ``wt`` is given: :func:`gates.live_frozen`),
    or ``({}, None)``."""
    row = gates.live_frozen(conn, slug, wt)
    if row is None:
        return {}, None
    p = row["payload"]
    return dict(p.get("files") or {}), p.get("commit")


def dependency_base(conn: psycopg.Connection, slug: str, default: str) -> str:
    """Stack on the branch of the one unmerged dependency; otherwise the base branch."""
    deps = [n for n in nodes.neighbours(conn, slug, rel="depends_on", direction="out")]
    unmerged = [d["node"]["slug"] for d in deps if d["node"]["kind"] == "contract" and d["node"]["state"] != "merged"]
    return gitops.branch_for(unmerged[0]) if len(unmerged) == 1 else default


def current_json(node: dict[str, Any], *, feature: str | None, step: Step, wf: dict[str, Any], cfg: Config,
                 frozen: dict[str, str], frozen_at: str | None, run_id: int, attempt: int) -> dict[str, Any]:
    caps = node.get("capabilities") or {}
    out: dict[str, Any] = {
        "slug": node["slug"], "ticket": node["title"], "allowed_paths": list(caps.get("paths") or []),
        "tier": node.get("tier") or "low", "require_tests": True, "reviewer": cfg.reviewer,
        "node": node["slug"], "feature": feature, "state": node["state"], "step": step.name,
        "command": step.command, "stage": wf.get("stage"), "model": wf.get("model"),
        "acceptance": node.get("acceptance") or [], "capabilities": caps,
        "orchestrator": {"run_id": run_id, "attempt": attempt, "source": "ledger",
                         "written_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
    }
    if frozen:
        out["frozen_tests"] = frozen
        out["frozen_at"] = frozen_at
    return out


def contract_md(conn: psycopg.Connection, node: dict[str, Any]) -> str:
    """A pack-shaped contract.md rendered from the ledger row (used when the repo has none)."""
    caps = node.get("capabilities") or {}
    lines = [f"# {node['title']}", "", "## Outcome", "", node["title"], "", "## Acceptance criteria", ""]
    for i, row in enumerate(node.get("acceptance") or [], 1):
        test = f"`{row['test']}`: " if row.get("test") else ""
        lines.append(f"{i}. {row.get('id', '')} {test}{row.get('text', '')}".rstrip())
    runnable = [r for r in node.get("acceptance") or [] if r.get("command")]
    lines += ["", "## Acceptance", ""]
    if runnable:
        for r in runnable:
            lines += [f"- $ {r['command']}", f"  => {r.get('expect', 'exit 0')}"]
    else:
        lines.append("(no runnable acceptance commands in the ledger; the acceptance tests above are the checks)")
    lines += ["", "## Decisions", ""]
    decided = conn.execute(
        "SELECT question, choice, decided_by FROM decision d JOIN node n ON n.id = d.node_id "
        "WHERE n.slug = %s AND d.resolved_at IS NOT NULL ORDER BY d.id", (node["slug"],)).fetchall()
    # "None." (or "- question -> choice") is what the sdlc plugin's acceptance.py --ready accepts
    lines += [f"- {d['question']} -> {d['choice']} ({d['decided_by']})" for d in decided] or ["None."]
    lines += ["", "## Allowed paths", ""] + [f"- `{p}`" for p in caps.get("paths") or []]
    lines += ["", *contract_blocks.render(node)]
    lines += ["", "## Risk tier", "", node.get("tier") or "low", "",
              "## Ledger", "", f"Node `{node['slug']}` in the sdlc-graph ledger; this file is rendered from it.", ""]
    return "\n".join(lines)


def settings_for(node: dict[str, Any], step: Step, cfg: Config) -> dict[str, Any]:
    """Compiled capabilities + transition/decision denies + (unless the graph plugin is
    loaded with --plugin-dir) the sdlc-graph Agent-spawn hooks with absolute paths."""
    settings = caps_mod.compile_full(node.get("capabilities") or {})["settings"]
    settings = json.loads(json.dumps(settings))
    if step.egress:
        net = settings.setdefault("sandbox", {}).setdefault("network", {})
        net["allowedDomains"] = sorted(set(net.get("allowedDomains") or []) | set(step.egress))
    perms = settings.setdefault("permissions", {})
    perms["deny"] = sorted(set(perms.get("deny") or []) | set(DENY_TOOLS))
    graph_plugin_loaded = any(Path(d).resolve() == SDLC_GRAPH_ROOT for d in cfg.plugin_dirs if Path(d).exists())
    if not graph_plugin_loaded:
        root = SDLC_GRAPH_ROOT / "hooks"
        hooks = json.loads((root / "hooks.example.json").read_text())["hooks"]
        for event, script in (("PreToolUse", "guard_agent_spawn.py"), ("PostToolUse", "record_agent_run.py")):
            for group in hooks[event]:
                for h in group["hooks"]:
                    h["command"] = f"\"{cfg.python}\" -I \"{root / script}\""
        settings["hooks"] = hooks
    return settings


def prompt_for(conn: psycopg.Connection, node: dict[str, Any], step: Step, cfg: Config, *, attempt: int,
               feedback: list[str], previous: dict[str, Any] | None) -> str:
    caps = node.get("capabilities") or {}
    lines = [
        step.command, "",
        f"Orchestrated step (sdlc-graph orchestrator), attempt {attempt}. Contract `{node['slug']}`: {node['title']}.",
        f"Ledger state: {node['state']}. This session runs exactly the step above and ends. The orchestrator",
        "checks the evidence you record, the files you changed and the frozen tests after you finish, and moves",
        "the ledger state itself: record evidence with evidence_add as the skill says, but do not call",
        "contract_transition or decision_resolve (both are denied in this session).",
        "", "Allowed paths (the drift gate resets the worktree if you change anything else):",
        *[f"- {p}" for p in caps.get("paths") or []],
        "", "Acceptance:",
        *[f"- {r.get('id')}: {r.get('text')}" + (f" (test {r['test']})" if r.get("test") else "") for r in node.get("acceptance") or []],
        "",
        "",
        "Nobody answers questions during this run, and none should be needed: every question was asked when the",
        "contract was written. Where the contract, its decisions and the code leave something open, apply the",
        "contract's Defaults when ambiguous (below) and record each applied default in assumptions.md; write",
        "questions.md only for an escalation the defaults cannot settle, then end the run.",
        f"assumptions.md is .claude/work/{node['slug']}/assumptions.md: create .sdlc-stage/{node['slug']}/assumptions.md with",
        "the Write tool (it makes the directory; no mkdir, no heredoc), then run the pack's scripts/workfile.py",
        f".claude/work/{node['slug']}/assumptions.md (Claude Code refuses Write under .claude/ here). One item per default:",
        "'- default_applied: <what you chose>' then indented 'where: <file or area>', 'why: <what was open>' and",
        "'decision: <the Decisions row it relies on, D1 for row 1, #<id> for a ledger decision, or none>' (the gates",
        "fail an item without a decision: line). An item that names a decision quotes that decision's Value in",
        "default_applied. An assumption must never contradict a decision below; the gates check this.",
        "Defaults when ambiguous:",
        *[f"- {d}" for d in _defaults(node)],
        f"questions.md (.claude/work/{node['slug']}/questions.md, staged the same way) holds only that escalation:",
        f"a question no default settles and no decision anticipated. The reviewer is {cfg.reviewer}.",
        "",
        "How to run commands here: tools outside this session's allow-list are denied, never prompted.",
        "- One plain command per Bash call: no &&, ; or | chain, no > redirection, no heredoc or loop, even of allowed",
        "  commands (such a call is refused for its shape). Read with Read, find with Glob, search with Grep; create files",
        "  with Write (it makes missing directories) and change them with Edit, never with cat, echo, touch, mkdir or sed.",
        "- A commit subject (the first -m) stays under 72 characters; put the detail and the Co-Authored-By trailer in",
        "  a second -m. The pack's commit guard refuses a longer subject.",
        "- A denied capability (a command, path, host or tool no rule allows) ends the step as an escalation: do not",
        "  look for another way to do the same thing. A call refused only for its form (the shape above, or a commit",
        "  message the guard rejects) is redone once in the allowed form.",
    ]
    if step.name != "ship":
        lines.append("Do not push and do not open a PR in this step. Commit where the pack allows it (its commit guard "
                     "allows a tests-only commit; app code is committed by /sdlc:ship after verify); otherwise "
                     "leave the work in the worktree, where the next step finds it.")
    if previous:
        lines += ["", f"Previous run (#{previous['id']}, step {previous.get('step')}, attempt {previous.get('attempt')}, "
                      f"outcome {previous.get('outcome')}):"]
        if previous.get("summary"):
            lines.append("  summary: " + _cut(previous["summary"]))
        if previous.get("last_assistant_message"):
            lines.append("  its last message: " + _cut(previous["last_assistant_message"]))
    if feedback:
        lines += ["", "Why the last attempt did not pass (fix this first):", *[f"- {f}" for f in feedback]]
    answered = store.answered_questions(conn, node["slug"])
    if answered:
        lines += ["", "Questions an earlier session asked, and the human's answers (follow them):",
                  *[f"- {_cut(a['question'], 600)} -> {_cut(a['choice'], 600)} ({a['decided_by']})" for a in answered]]
    decided = [d for d in autonomy.resolved_decisions(conn, node["slug"]) if not d.get("severity")]
    if decided:
        lines += ["", "Decisions already made on this contract (follow them; never assume otherwise):",
                  *[f"- #{d['id']} {_cut(d['question'], 300)} -> {_cut(d['choice'], 300)}" for d in decided]]
    open_d = decisions.open_for(conn, node["slug"])
    if open_d:
        lines += ["", "Open decisions on this contract (a human answers them; do not):",
                  *[f"- #{d['id']} {d['question']}" for d in open_d]]
    return "\n".join(lines) + "\n"


def _defaults(node: dict[str, Any]) -> list[str]:
    d = (node.get("attrs") or {}).get("defaults")
    return [d] if isinstance(d, str) else [str(x) for x in d or []] or [
        "(none recorded: this contract should not have been dispatched; escalate through questions.md)"]


def executor_env(node: dict[str, Any], step: Step, stage: str | None, run_id: int, attempt: int,
                 cfg: Config | None = None, worktree: Path | None = None) -> dict[str, str]:
    """The session's environment: an allowlist, not a copy of the daemon's. The ledger URL it carries is
    ``cfg.session_database_url`` (point it at a role that may only INSERT evidence and SELECT; see
    docs/orchestrator.md) and otherwise the daemon's own URL, which a session could use to bypass the
    ledger's actor rules with raw SQL."""
    env = clean_env(github=bool(step.egress), worktree=worktree)
    url = (cfg.session_database_url if cfg else None) or db.database_url()
    # The ledger URL reaches the session's graph MCP server through DATABASE_URL (its .mcp.json
    # expands ${DATABASE_URL}). The application under test may read DATABASE_URL too, so it is only
    # exported when the daemon itself was given one; otherwise both fall back to their defaults.
    # SDLC_DATABASE_URL always carries it, for tools that read that name.
    if os.environ.get("DATABASE_URL") or (cfg and cfg.session_database_url):
        env["DATABASE_URL"] = url
    env.update({
        "SDLC_DATABASE_URL": url, "SDLC_SCHEMA": db.schema_name(),
        "SDLC_NODE": node["slug"], "SDLC_STAGE": stage or "", "ORCH_RUN_ID": str(run_id),
        "ORCH_STEP": step.name, "ORCH_ATTEMPT": str(attempt),
        "ORCH_EXECUTOR_MODE": cfg.executor_mode if cfg else "local",
    })
    env["ORCH_AUTH_SOURCE"] = auth.detect_auth_source(env)
    return env


#: A git credential helper that answers from the session's own GitHub token (``GH_TOKEN``, else ``GITHUB_TOKEN``).
GIT_TOKEN_HELPER = ('!f() { test "$1" = get || exit 0; echo username=x-access-token; '
                    'echo "password=${GH_TOKEN:-$GITHUB_TOKEN}"; }; f')


def github_env(env: dict[str, str], config_dir: Path) -> dict[str, str]:
    """What a pushing step (ship) needs to use the GitHub token it was given, inside a sandbox that denies reading
    ``~/.config/gh`` (the pack's settings do): ``gh`` reads an empty config directory of the run's own
    (``GH_CONFIG_DIR``; it refuses to start when its config file is unreadable, even with a token) and
    authenticates with the token, and git pushes through :data:`GIT_TOKEN_HELPER` instead of the engineer's
    credential helpers (which read that directory or the keychain). Empty when ``env`` carries no token."""
    if not (env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")):
        return {}
    config_dir.mkdir(parents=True, exist_ok=True)
    return {"GH_CONFIG_DIR": str(config_dir), "GH_NO_UPDATE_NOTIFIER": "1", "GH_PROMPT_DISABLED": "1",
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",   # drop the helpers configured so far
            "GIT_CONFIG_KEY_1": "credential.helper", "GIT_CONFIG_VALUE_1": GIT_TOKEN_HELPER}


GH_TOKEN_TIMEOUT = 30.0


def ship_token(cfg: Config) -> str | None:
    """The GitHub token for the ship step in local executor mode when the daemon's environment has none: the
    engineer's own login, from ``gh auth token`` (a subprocess, no shell), resolved once and kept on ``cfg``.
    The value is never logged. None in runner mode (an explicit token or GitHub identity federation is the
    runner's job), or when ``gh`` is missing, not logged in or fails (ship then fails visibly on its own)."""
    if cfg.executor_mode != "local":
        return None
    if cfg.github_token:
        return cfg.github_token
    gh = shutil.which("gh")
    if not gh:
        print("ship credential: none (no GH_TOKEN or GITHUB_TOKEN, and gh is not on PATH)", file=sys.stderr, flush=True)
        return None
    try:
        proc = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=GH_TOKEN_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    token = (proc.stdout.strip().splitlines() or [""])[0].strip() if proc is not None and proc.returncode == 0 else ""
    if not token:
        print("ship credential: none (gh auth token gave no token; run gh auth login or set GH_TOKEN)", file=sys.stderr, flush=True)
        return None
    cfg.github_token = token
    return token


def dispatch(conn: psycopg.Connection, cfg: Config, slug: str, step: Step, *, feature: str | None, attempt: int,
             feedback: list[str] | None = None, start_commit: str | None = None) -> dict[str, Any]:
    """Start one executor session for ``step`` on ``slug``. Returns the run row.

    ``start_commit`` forces the commit the step starts from (a retry after a reset); otherwise
    the worktree's HEAD.
    """
    node = nodes.get_row(conn, slug)
    node = {**node, "id": str(node["id"])}
    wf = workflow.next_step(conn, slug)
    stage, model = wf.get("stage"), wf.get("model") or "sonnet"
    base = dependency_base(conn, slug, cfg.base or gitops.default_base(cfg.repo))
    wt, created = gitops.ensure_worktree(cfg.repo, slug, base)
    if cfg.guard:
        cfg.guard()
    failed_setup = worktree_setup.ensure(conn, cfg, slug, wt, feature=feature, attempt=attempt)
    if failed_setup is not None:   # setup_failed: the P1 decision is open; the contract waits for a human
        return failed_setup
    commit = start_commit or gitops.head(wt)
    # a question from an earlier run was escalated and answered: any questions.md present after this run is new
    old_q = gates.questions_file(wt, slug)
    if old_q.is_file():
        old_q.replace(old_q.with_name("questions.answered.md"))
    if cfg.guard:
        cfg.guard()
    previous = store.last_run(conn, slug)
    frozen, frozen_at = frozen_from_ledger(conn, slug, wt)
    allowed = autonomy.allowed_tools(node, step.name, cfg)   # FileNotFoundError/ValueError: no pack list, no run

    env = executor_env(node, step, stage, 0, attempt, cfg, worktree=wt)       # run id is patched in below
    ship_credential = None
    if step.egress and not (env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")) and cfg.executor_mode == "local":
        token = ship_token(cfg)
        if token:
            env["GH_TOKEN"] = token
            ship_credential = "gh auth token"
            print(f"ship credential: {ship_credential}", file=sys.stderr, flush=True)
    run = store.start_run(conn, slug, step=step.name, stage=stage, model=model, attempt=attempt,
                          executor=cfg.executor_mode, auth_source=env["ORCH_AUTH_SOURCE"],
                          start_commit=commit, meta={"from_state": node["state"], "to_state": step.to_state,
                                                     "worktree": str(wt), "base": base, "feature": feature,
                                                     "worktree_created": created, "ship_credential": ship_credential, "path_head": env.get("PATH", "").split(os.pathsep)[0], "feedback": feedback or [],
                                                     "dirty_at_start": gitops.dirty_snapshot(wt),
                                                     "ignored_at_start": gitops.ignored_snapshot(wt),
                                                     "start_snapshot": gitops.snapshot(wt)})
    rid = run["id"]
    session_id = str(uuid.uuid4())
    logdir = Path(cfg.log_root) / slug
    logdir.mkdir(parents=True, exist_ok=True)
    stem = logdir / f"{step.name}-{attempt}-run{rid}"
    log_path, result_path = Path(f"{stem}.jsonl"), Path(f"{stem}.result.json")
    prompt_path, settings_path, stderr_path = Path(f"{stem}.prompt.md"), Path(f"{stem}.settings.json"), Path(f"{stem}.stderr")
    tools_path = Path(f"{stem}.allowed_tools.txt")
    if step.egress:
        env.update(github_env(env, Path(f"{stem}.gh")))

    work = wt / ".claude" / "work"
    (work / slug).mkdir(parents=True, exist_ok=True)
    (work / "current.json").write_text(json.dumps(
        current_json(node, feature=feature, step=step, wf=wf, cfg=cfg, frozen=frozen, frozen_at=frozen_at,
                     run_id=rid, attempt=attempt), indent=2) + "\n")
    cmd_md = work / slug / "contract.md"
    if not cmd_md.exists():
        src = cfg.repo / ".claude" / "work" / slug / "contract.md"
        cmd_md.write_text(src.read_text() if src.is_file() else contract_md(conn, node))
    prompt_path.write_text(prompt_for(conn, node, step, cfg, attempt=attempt, feedback=feedback or [], previous=previous))
    settings_path.write_text(json.dumps(settings_for(node, step, cfg), indent=2) + "\n")
    tools_path.write_text("".join(f"{t}\n" for t in allowed["tools"]))

    argv = [*cfg.executor, "--worktree", str(wt), "--command", step.command, "--prompt-file", str(prompt_path),
            "--settings", str(settings_path), "--result-file", str(result_path), "--log-file", str(log_path),
            "--session-id", session_id, "--model", model, "--max-turns", str(cfg.max_turns),
            "--budget", str(cfg.budget_usd), "--reviewer", cfg.reviewer, "--allowed-tools-file", str(tools_path),
            "--permission-mode", autonomy.PERMISSION_MODE, "--permission-prompts", autonomy.PERMISSION_PROMPTS]
    for d in cfg.plugin_dirs:
        argv += ["--plugin-dir", d]
    if "--bare" in argv:   # --bare skips hooks, and the hooks are the guard
        raise ValueError("refusing to start an executor with --bare")
    try:
        with open(stderr_path, "ab") as err:
            env["ORCH_RUN_ID"] = str(rid)
            proc = subprocess.Popen(argv, cwd=wt, env=env,
                                    stdin=subprocess.DEVNULL, stdout=err, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
        store.finish_run(conn, rid, "executor_error", summary=f"{step.name}: could not start the executor: {exc}")
        return store.get_run(conn, rid)
    CHILDREN[proc.pid] = proc
    ident = process_identity(proc.pid)
    store.update_run(conn, rid, pid=proc.pid, session_id=session_id, log_path=str(log_path),
                     meta={"pid_started": ident[0] if ident else None, "result_file": str(result_path), "prompt_file": str(prompt_path),
                           "settings_file": str(settings_path), "stderr_file": str(stderr_path), "argv0": argv[0],
                           "allowed_tools_file": str(tools_path), "allowed_tools_count": len(allowed["tools"]),
                           "allowed_tools_notes": allowed["notes"], "permission_mode": autonomy.PERMISSION_MODE,
                           "permission_prompts": autonomy.PERMISSION_PROMPTS})
    return store.get_run(conn, rid)
