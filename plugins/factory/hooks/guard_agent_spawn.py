#!/usr/bin/env python3
"""PreToolUse hook for the ``Agent`` tool: record the model choice against the routing default and hold the
ceilings (docs/DESIGN.md section 4, mechanism 2; section 18.2).

Stdlib only; run it as ``python3 -I hooks/guard_agent_spawn.py`` with the hook JSON on stdin.
Field names and the output contract are in the Claude Code hooks documentation:
``tool_input`` has ``prompt``, ``description``, ``subagent_type``, ``model``; a deny is
exit 2 with the reason on stderr.

Stage resolution, first hit wins:
  1. tag ``[sdlc stage=<stage> node=<slug>]`` in ``tool_input.description`` ONLY. The prompt is
     never read for a tag: it can carry ticket text, and a ticket must not pick the model tier
  2. env ``SDLC_STAGE`` (node: ``SDLC_NODE``)
  3. ``subagent_type`` map (with or without a plugin prefix): test-author -> tests,
     reviewer -> review.lens, security-reviewer -> review.lens, planner -> contract.design,
     triage -> triage, auditor -> audit, maturity-assessor -> assess,
     security-assessor -> assess, drafter -> draft, implementer -> implement. A known agent
     names its own stage, so this outranks the contract's stage below
  4. ``<project>/.claude/work/current.json`` key ``stage`` (node: ``node``)
  5. unknown -> ``implement`` (executor tier)

Outcomes (``decide``): the hook NEVER rewrites the model. A spawn with no model keeps Claude Code's own
resolution (agent frontmatter, then the session model); none is injected. The routing policy's alias for the
stage is the DEFAULT; a requested model on another tier is an OVERRIDE, recorded (``default``, ``override``) and
allowed. Exit 2 only for a ceiling: a model outside ``availableModels`` (read from the managed, user and project
settings the hook can see; none set means no ceiling), the Fable budget per epic, and input or errors it cannot
handle (fail closed). The Fable budget applies when the model that will run is Fable: the requested one, else
the agent definition's frontmatter ``model``. The slot is RESERVED in PreToolUse, atomically: with
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
    "security-reviewer": "review.lens",
    "planner": "contract.design",
    "triage": "triage",
    "auditor": "audit",
    "maturity-assessor": "assess",
    "security-assessor": "assess",
    "drafter": "draft",
    "implementer": "implement",
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
    """``(stage, node)``: tag in the description (never the prompt), then env, then the subagent_type map
    (with or without a plugin prefix), then ``current.json``. ``(None, None)`` when nothing identifies the stage."""
    m = TAG_RE.search(str(tool_input.get("description") or ""))
    if m:
        return m.group("stage"), m.group("node") or os.environ.get("SDLC_NODE") or None
    env_stage = os.environ.get("SDLC_STAGE")
    if env_stage:
        return env_stage, os.environ.get("SDLC_NODE") or None
    cur = _read_json(work / "current.json") if work is not None else None
    node = (cur.get("node") if isinstance(cur, dict) else None) or os.environ.get("SDLC_NODE") or None
    # A known agent names its own stage; current.json's "stage" is the contract's ledger stage
    # ("contract.design" until something moves it), not the stage of a test author or a reviewer.
    agent = str(tool_input.get("subagent_type") or "").rsplit(":", 1)[-1]  # "factory:test-author" -> "test-author"
    if agent in SUBAGENT_STAGES:
        return SUBAGENT_STAGES[agent], node
    if isinstance(cur, dict) and cur.get("stage"):
        return str(cur["stage"]), node
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


def reserve_ledger(hook_input: dict[str, Any], stage: str, node: str | None, model: str, model_tier: str,
                   meta: dict[str, Any] | None = None) -> tuple[str, Any, str]:
    """Reserve a planner slot in the ledger: ``run start --tier planner`` runs the budget check and
    the insert under the ledger's advisory lock. Returns ``("ok", run_id, "")``, ``("refused", None,
    reason)`` (budget spent) or ``("unavailable", None, why)`` (use the counter file)."""
    epic = os.environ.get("SDLC_EPIC")
    if not epic:
        return "unavailable", None, "SDLC_EPIC not set"
    tool_input = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
    args = ["run", "start", "--stage", stage, "--model", model, "--tier", model_tier, "--node", node or epic]
    if hook_input.get("tool_use_id"):
        args += ["--tool-use-id", str(hook_input["tool_use_id"])]
    if tool_input.get("subagent_type"):
        args += ["--agent-type", str(tool_input["subagent_type"])]
    if meta:
        args += ["--meta", json.dumps(meta)]
    code, data, err = run_cli(args)
    if code == 0 and isinstance(data, dict) and "id" in data:
        return "ok", data["id"], ""
    text = err or (json.dumps(data) if data else "")
    if code == 2 and "budget" in text.lower():  # PermissionError is a refusal, not an outage
        return "refused", None, text
    return "unavailable", None, text or f"exit {code}"


# --------------------------------------------------------------------------- ceilings: availableModels


class SettingsCorrupt(Exception):
    """A settings file holds an ``availableModels`` that is not a list of strings."""


def settings_files(project: Path) -> list[Path]:
    """The settings files the hook can see: managed (macOS, Linux, plus ``managed-settings.d``), the user's and
    the project's."""
    files: list[Path] = []
    for base in (Path("/Library/Application Support/ClaudeCode"), Path("/etc/claude-code")):
        files.append(base / "managed-settings.json")
        drop_in = base / "managed-settings.d"
        if drop_in.is_dir():
            files.extend(sorted(drop_in.glob("*.json")))
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    files += [config / "settings.json", project / ".claude" / "settings.json", project / ".claude" / "settings.local.json"]
    return files


def available_models(project: Path) -> list[list[str]]:
    """One list per settings file that sets ``availableModels`` (a spawn must satisfy every list). Empty when none
    is set: no ceiling. Raises :class:`SettingsCorrupt` for a value that is not a list of strings."""
    lists: list[list[str]] = []
    for path in settings_files(project):
        data = _read_json(path)
        if not isinstance(data, dict) or "availableModels" not in data:
            continue
        value = data["availableModels"]
        if not isinstance(value, list) or not all(isinstance(m, str) and m.strip() for m in value):
            raise SettingsCorrupt(f"{path}: availableModels must be a list of model names")
        lists.append([m.strip() for m in value])
    return lists


def model_allowed(model: str, allowed: list[str], policy: dict[str, Any]) -> bool:
    """``model`` equals an entry, or shares a tier with one (an alias against a full ID)."""
    low = model.lower()
    tier = tier_of_model(model, policy)
    for entry in allowed:
        if entry.lower() == low:
            return True
        if tier is not None and tier_of_model(entry, policy) == tier:
            return True
    return False


def agent_default_model(tool_input: dict[str, Any]) -> str | None:
    """The ``model`` in the frontmatter of the plugin's agent file for ``subagent_type``, if any."""
    agent = str(tool_input.get("subagent_type") or "").rsplit(":", 1)[-1]
    if not re.fullmatch(r"[\w-]+", agent):
        return None
    try:
        text = (ROOT / "agents" / f"{agent}.md").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    mm = re.search(r"^model:\s*(\S+)\s*$", m.group(1), re.M) if m else None
    return mm.group(1).strip("\"'") if mm else None


# --------------------------------------------------------------------------- decision


def decide(hook_input: dict[str, Any], *, policy: dict[str, Any], fable_remaining: int | None = None,
           available: list[list[str]] | None = None) -> dict[str, Any]:
    """Pure decision. The model is never rewritten.

    Returns ``{"action": "allow"|"block", "stage", "node", "requested_model", "default", "override", "tier",
    "reason", "exit_code"}``. ``default`` is the routing policy's alias for the stage; ``override`` is True when
    a requested model sits on another tier. ``tier`` is the tier of the model that will run when known (the
    requested one, else the agent definition's), else None. ``fable_remaining`` is the budget left when the
    caller already knows it (``None`` = not checked); ``available`` is the ``availableModels`` lists.
    """
    raw_input = hook_input.get("tool_input")
    if raw_input is not None and not isinstance(raw_input, dict):
        raise ValueError(f"tool_input must be an object, got {type(raw_input).__name__}")
    tool_input = dict(raw_input or {})
    stage, node = parse_tag(tool_input, work_dir(hook_input))
    stage = stage or DEFAULT_STAGE
    requested = tool_input.get("model") or None
    if requested is not None and not isinstance(requested, str):
        raise ValueError("tool_input.model must be a string")

    stage_tier = policy["stages"].get(stage)
    if stage_tier is None:
        stage_tier = "executor"
        stage = DEFAULT_STAGE
    default = tier_alias(policy, stage_tier)
    effective = requested or agent_default_model(tool_input)
    tier = tier_of_model(effective, policy)
    override = bool(requested) and tier != stage_tier
    base = {"stage": stage, "node": node, "requested_model": requested, "default": default, "override": override,
            "tier": tier, "effective_model": effective}

    for allowed in available or []:
        if effective and not model_allowed(effective, allowed, policy):
            reason = (f"model '{effective}' is outside availableModels ({', '.join(allowed)}); "
                      "pick a listed model or ask for the list to change.")
            return {**base, "action": "block", "reason": reason, "exit_code": 2}

    if tier == "planner" and fable_remaining is not None and fable_remaining <= 0:
        reason = (f"Fable budget exhausted for stage '{stage}': no planner spawns left. "
                  "Resolve a 'fable_budget_increase' decision or use the orchestrator tier.")
        return {**base, "action": "block", "reason": reason, "exit_code": 2}

    return {**base, "action": "allow", "reason": None, "exit_code": 0}


def note_spawn(work: Path, hook_input: dict[str, Any], result: dict[str, Any], run_id: Any = None) -> None:
    tid = hook_input.get("tool_use_id")
    if not tid:
        return
    path = work / "spawns.json"
    data = _read_json(path)
    if not isinstance(data, dict):
        data = {}
    data[tid] = {k: result.get(k) for k in ("stage", "node", "requested_model", "default", "override", "tier")}
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
    try:
        available = available_models(project_dir(hook_input))
    except SettingsCorrupt as exc:
        print(f"sdlc guard: availableModels unusable, blocking spawn: {exc}", file=sys.stderr)
        return 2
    probe = decide(hook_input, policy=policy, available=available)
    if probe["action"] == "block":
        print(probe["reason"], file=sys.stderr)
        return 2
    run_id = None
    if probe["tier"] == "planner":
        # Reserve before the spawn runs: counting afterwards leaves a window for parallel spawns.
        meta = {"default": probe["default"], "override": probe["override"]}
        model = probe["effective_model"] or probe["default"]
        status, run_id, why = reserve_ledger(hook_input, probe["stage"], probe["node"], model, "planner", meta)
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
    note_spawn(work, hook_input, probe, run_id)
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
