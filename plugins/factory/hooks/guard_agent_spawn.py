#!/usr/bin/env python3
"""PreToolUse hook for the ``Agent`` tool: pin the spawn's model to the routing policy and
enforce the Fable budget (docs/DESIGN.md section 4, mechanism 2).

Stdlib only; run it as ``python3 -I hooks/guard_agent_spawn.py`` with the hook JSON on stdin.
Field names and the output contract are in the Claude Code hooks documentation:
``tool_input`` has ``prompt``, ``description``, ``subagent_type``, ``model``; a rewrite is
``hookSpecificOutput.updatedInput`` (the whole tool input, unchanged fields included); a deny is
exit 2 with the reason on stderr.

Stage resolution, first hit wins:
  1. tag ``[sdlc stage=<stage> node=<slug>]`` in ``tool_input.description`` ONLY. The prompt is
     never read for a tag: it can carry ticket text, and a ticket must not pick the model tier
  2. env ``SDLC_STAGE`` (node: ``SDLC_NODE``)
  3. ``<project>/.claude/work/current.json`` key ``stage`` (node: ``node``)
  4. ``subagent_type`` map: test-author -> tests, reviewer -> review.lens,
     planner -> contract.design, triage -> triage
  5. unknown -> ``implement`` (executor tier)

Outcomes (``decide``): compliant spawn -> pass through (no stdout, exit 0); model missing or on
another tier alias -> rewrite to the policy alias, one stderr line; planner tier (fable) with the
Fable budget used up -> exit 2. The budget slot is RESERVED in PreToolUse, atomically: with
``SDLC_EPIC`` set and the CLI answering, ``sdlc-graph run start --tier planner`` inserts the run
under the ledger's advisory lock (parallel spawns cannot both take the last slot, and the
PostToolUse hook finishes that row instead of starting another); otherwise the counter file
``.claude/work/fable_budget.json`` (``{"used": N, "budget": M}``, ``budget`` optional) is
read-modified-written under an exclusive ``flock`` and replaced atomically. A counter file that
exists but cannot be parsed fails CLOSED (exit 2): a torn or hand-damaged file never resets the
budget. Unreadable stdin, or any unexpected error while handling an Agent spawn -> exit 2, so a hook
config without ``onFailure: block`` cannot open the gate. (A counter file the agent can delete is
still only a convenience; the ledger is the enforcement.)

Allowed spawns are noted in ``.claude/work/spawns.json`` (keyed by ``tool_use_id``) so the
PostToolUse hook can record the model that was originally requested.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

TAG_RE = re.compile(r"\[sdlc\s+stage=(?P<stage>[\w.]+)(?:\s+node=(?P<node>[\w.-]+))?\]")
SUBAGENT_STAGES: dict[str, str] = {
    "test-author": "tests",
    "reviewer": "review.lens",
    "planner": "contract.design",
    "triage": "triage",
}
DEFAULT_STAGE = "implement"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
POLICY_PATH = ROOT / "sdlc_graph" / "ledger" / "routing_policy.json"


# --------------------------------------------------------------------------- helpers shared with record_agent_run


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path or os.environ.get("SDLC_ROUTING_POLICY") or POLICY_PATH)
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def project_dir(hook_input: dict[str, Any]) -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or hook_input.get("cwd") or os.getcwd())


def work_dir(hook_input: dict[str, Any]) -> Path:
    return project_dir(hook_input) / ".claude" / "work"


def cli_command() -> list[str] | None:
    explicit = os.environ.get("SDLC_GRAPH_BIN")
    if explicit:
        return explicit.split()
    venv = ROOT / ".venv" / "bin" / "sdlc-graph"
    if venv.exists():
        return [str(venv)]
    found = shutil.which("sdlc-graph")
    return [found] if found else None


def run_cli(args: list[str], timeout: float = 15.0) -> tuple[int, Any, str]:
    """``(exit_code, parsed_json_or_None, stderr)``. Exit code -1 when the CLI cannot be run."""
    cmd = cli_command()
    if cmd is None:
        return -1, None, "sdlc-graph CLI not found (set SDLC_GRAPH_BIN)"
    try:
        proc = subprocess.run(cmd + args + ["--json"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, None, f"sdlc-graph failed to run: {exc}"
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        data = None
    return proc.returncode, data, proc.stderr.strip()


def tier_alias(policy: dict[str, Any], tier: str) -> str:
    return policy["tiers"][tier]


def tier_of_model(model: str | None, policy: dict[str, Any]) -> str | None:
    """Tier whose alias appears in ``model`` (aliases or full IDs); None when unknown."""
    if not model:
        return None
    low = str(model).lower()
    for tier, alias in policy["tiers"].items():
        if alias.lower() in low:
            return tier
    return None


def _read_json(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- stage and node


def parse_tag(tool_input: dict[str, Any], work: Path | None = None) -> tuple[str | None, str | None]:
    """``(stage, node)``: tag in the description (never the prompt), then env, then ``current.json``,
    then the subagent_type map. ``(None, None)`` when nothing identifies the stage."""
    m = TAG_RE.search(str(tool_input.get("description") or ""))
    if m:
        return m.group("stage"), m.group("node") or os.environ.get("SDLC_NODE") or None
    env_stage = os.environ.get("SDLC_STAGE")
    if env_stage:
        return env_stage, os.environ.get("SDLC_NODE") or None
    if work is not None:
        cur = _read_json(work / "current.json")
        if isinstance(cur, dict) and cur.get("stage"):
            return str(cur["stage"]), cur.get("node") or os.environ.get("SDLC_NODE") or None
    stage = SUBAGENT_STAGES.get(str(tool_input.get("subagent_type") or ""))
    if stage:
        return stage, os.environ.get("SDLC_NODE") or None
    return None, os.environ.get("SDLC_NODE") or None


# --------------------------------------------------------------------------- Fable budget


class CounterCorrupt(Exception):
    """``fable_budget.json`` exists but is not ``{"used": int>=0, "budget"?: int>=0}``."""


def counter_path(work: Path) -> Path:
    return work / "fable_budget.json"


def _counter_state(work: Path, policy: dict[str, Any]) -> tuple[int, int]:
    """``(used, budget)``. A missing file is a fresh counter; an unreadable or ill-typed one raises
    :class:`CounterCorrupt` (never a silent reset to the default budget)."""
    path = counter_path(work)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0, int(policy.get("fable_budget_per_epic", 3))
    except OSError as exc:
        raise CounterCorrupt(f"{path} is unreadable: {exc}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CounterCorrupt(f"{path} is not valid JSON ({exc.msg}); fix or delete it") from None
    if not isinstance(data, dict):
        raise CounterCorrupt(f"{path} must hold a JSON object")
    used, budget = data.get("used", 0), data.get("budget", policy.get("fable_budget_per_epic", 3))
    for name, v in (("used", used), ("budget", budget)):
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise CounterCorrupt(f"{path}: {name} must be a non-negative integer, got {v!r}")
    return used, budget


def counter_remaining(work: Path, policy: dict[str, Any]) -> int:
    used, budget = _counter_state(work, policy)
    return budget - used


def reserve_counter(work: Path, policy: dict[str, Any]) -> bool:
    """Take one planner slot: True and ``used`` bumped on disk, or False when none is left. The
    read-modify-write is serialised by an exclusive ``flock`` on a sidecar lock file and the new
    state is written to a temp file and renamed into place, so a concurrent spawn never reads a
    torn file. Raises :class:`CounterCorrupt` (fail closed) and OSError (also treated as a block)."""
    work.mkdir(parents=True, exist_ok=True)
    with open(work / "fable_budget.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            used, budget = _counter_state(work, policy)
            if budget - used <= 0:
                return False
            tmp = work / f"fable_budget.json.{os.getpid()}.tmp"
            tmp.write_text(json.dumps({"budget": budget, "used": used + 1}, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, counter_path(work))
            return True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def reserve_ledger(hook_input: dict[str, Any], stage: str, node: str | None, alias: str, model_tier: str) -> tuple[str, Any, str]:
    """Reserve a planner slot in the ledger: ``run start --tier planner`` runs the budget check and
    the insert under the ledger's advisory lock. Returns ``("ok", run_id, "")``, ``("refused", None,
    reason)`` (budget spent) or ``("unavailable", None, why)`` (use the counter file)."""
    epic = os.environ.get("SDLC_EPIC")
    if not epic:
        return "unavailable", None, "SDLC_EPIC not set"
    tool_input = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
    args = ["run", "start", "--stage", stage, "--model", alias, "--tier", model_tier, "--node", node or epic]
    if hook_input.get("tool_use_id"):
        args += ["--tool-use-id", str(hook_input["tool_use_id"])]
    if tool_input.get("subagent_type"):
        args += ["--agent-type", str(tool_input["subagent_type"])]
    code, data, err = run_cli(args)
    if code == 0 and isinstance(data, dict) and "id" in data:
        return "ok", data["id"], ""
    text = err or (json.dumps(data) if data else "")
    if code == 2 and "budget" in text.lower():  # PermissionError is a refusal, not an outage
        return "refused", None, text
    return "unavailable", None, text or f"exit {code}"


# --------------------------------------------------------------------------- decision


def decide(hook_input: dict[str, Any], *, policy: dict[str, Any], fable_remaining: int | None = None) -> dict[str, Any]:
    """Pure decision.

    Returns ``{"action": "allow"|"rewrite"|"block", "stage", "node", "requested_model", "model",
    "tier", "reason", "output", "exit_code"}``. ``fable_remaining`` is the budget left when the
    caller already knows it (``None`` = not checked).
    """
    raw_input = hook_input.get("tool_input")
    if raw_input is not None and not isinstance(raw_input, dict):
        raise ValueError(f"tool_input must be an object, got {type(raw_input).__name__}")
    tool_input = dict(raw_input or {})
    stage, node = parse_tag(tool_input, work_dir(hook_input))
    stage = stage or DEFAULT_STAGE
    requested = tool_input.get("model") or None
    base = {"stage": stage, "node": node, "requested_model": requested}

    tier = policy["stages"].get(stage)
    if tier is None:
        tier = "executor"
        stage = DEFAULT_STAGE
        base["stage"] = stage
    alias = tier_alias(policy, tier)
    base.update(tier=tier, model=alias)

    if tier == "planner" and fable_remaining is not None and fable_remaining <= 0:
        reason = (f"Fable budget exhausted for stage '{stage}': no planner spawns left. "
                  "Resolve a 'fable_budget_increase' decision or use the orchestrator tier.")
        return {**base, "action": "block", "reason": reason, "output": None, "exit_code": 2}

    if tier_of_model(requested, policy) == tier:
        return {**base, "action": "allow", "reason": None, "output": None, "exit_code": 0}

    reason = f"sdlc routing: stage '{stage}' -> {tier} tier; model {requested or '(unset)'} rewritten to {alias}"
    updated = dict(tool_input)
    updated["model"] = alias
    output = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": reason,
        "updatedInput": updated,
    }}
    return {**base, "action": "rewrite", "reason": reason, "output": output, "exit_code": 0}


def note_spawn(work: Path, hook_input: dict[str, Any], result: dict[str, Any], run_id: Any = None) -> None:
    tid = hook_input.get("tool_use_id")
    if not tid:
        return
    path = work / "spawns.json"
    data = _read_json(path)
    if not isinstance(data, dict):
        data = {}
    data[tid] = {k: result.get(k) for k in ("stage", "node", "requested_model", "model", "tier")}
    data[tid]["rewritten"] = result["action"] == "rewrite"
    if run_id is not None:
        data[tid]["run_id"] = run_id
    data[tid]["agent_type"] = (hook_input.get("tool_input") or {}).get("subagent_type")
    try:
        work.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"sdlc guard: could not write {path}: {exc}", file=sys.stderr)


def _main(hook_input: dict[str, Any], policy: dict[str, Any]) -> int:
    work = work_dir(hook_input)
    probe = decide(hook_input, policy=policy)
    run_id = None
    if probe["tier"] == "planner":
        # Reserve before the spawn runs: counting afterwards leaves a window for parallel spawns.
        status, run_id, why = reserve_ledger(hook_input, probe["stage"], probe["node"], probe["model"], "planner")
        if status == "refused":
            print(why, file=sys.stderr)
            return 2
        if status == "unavailable":
            if os.environ.get("SDLC_EPIC"):
                print(f"sdlc guard: ledger budget unavailable ({why}); using counter file", file=sys.stderr)
            try:
                got = reserve_counter(work, policy)
            except (CounterCorrupt, OSError) as exc:
                print(f"sdlc guard: Fable budget counter unusable, blocking planner spawn: {exc}", file=sys.stderr)
                return 2
            if not got:
                print(decide(hook_input, policy=policy, fable_remaining=0)["reason"], file=sys.stderr)
                return 2
    result = decide(hook_input, policy=policy, fable_remaining=None)
    note_spawn(work, hook_input, result, run_id)
    if result["action"] == "rewrite":
        print(result["reason"], file=sys.stderr)
        print(json.dumps(result["output"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Read stdin, decide, print ``output``; return 2 (reason on stderr) to block. Any unexpected
    error while handling an Agent spawn is a block too (fail closed)."""
    try:
        hook_input = json.loads(sys.stdin.read())
        if not isinstance(hook_input, dict):
            raise ValueError("hook input is not a JSON object")
        policy = load_policy()
    except (ValueError, OSError) as exc:
        print(f"sdlc guard: cannot read hook input or policy, blocking spawn: {exc}", file=sys.stderr)
        return 2

    if hook_input.get("tool_name", "Agent") != "Agent":
        return 0
    try:
        return _main(hook_input, policy)
    except Exception as exc:  # noqa: BLE001 - fail closed: a crash must not become an allowed spawn
        print(f"sdlc guard: unexpected error, blocking spawn: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
