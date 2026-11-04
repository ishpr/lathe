#!/usr/bin/env python3
"""The measurement baseline (DESIGN section 18.6): run the eval set, judge each run on its outcome, take the ledger's
metrics, and write ``docs/baselines/<date>-<sha>.json``; ``compare`` says, per stage, whether outcomes held.

Every change that removes a process rule from a skill runs this before and after and compares the two files.

Usage:
  baseline.py prepare
      Build the eval Python (.demo/eval-venv: the sample app's dependencies, pytest, ruff, mypy) with uv when it is
      missing, and check the claude CLI is on the PATH.
  baseline.py run [--stage S ...] [--case GLOB ...] [--runs N] [--model M] [--max-cost-usd X] [--out FILE]
                  [--no-metrics] [--keep] [--dry-run]
      Run `claude plugin eval` on this plugin's suite (tests/evals: the epic cases and the stage cases), with the
      scaffolds, Bash/Write/Edit granted, the kept workspaces and no baseline arm. Each stage case (one with an
      outcome.json) is then judged by tests/evals/_fixtures/check.py on the workspace it left; any other case by
      its own graders' score. Writes the baseline file (default docs/baselines/<UTC date>-<git sha>.json).
      --dry-run prints the eval command and exits. Real runs cost money: --max-cost-usd caps the eval run.
  baseline.py score RESULT.json [--out FILE] [--no-metrics]
      Build a baseline from a finished eval run's --json file, run with --keep-temp (the workspaces are found from
      each run's tracePath).
  baseline.py compare OLD.json NEW.json [--json]
      Per stage: outcomes held, improved or regressed (the thresholds below), and cost and turns similar or not.
      Exit 0 when no stage regressed and cost stayed similar, 1 otherwise, 2 misuse.

Run it from a normal terminal, never from inside a sandboxed Claude Code session: each eval run is a nested claude
session that must write under ~/.claude, and under the session sandbox none of them starts. `run` and `score` write
no baseline (exit 1) when a case started fewer runs than were asked for, and `compare` refuses (exit 2) a baseline
with a stage that has no completed run.

The database for the metrics is DATABASE_URL (the sdlc-graph default otherwise); the eval sessions themselves get
an unreachable DATABASE_URL, so no eval run writes to a ledger. Exit: 0 ok, 1 a failed comparison or eval error,
2 misuse or a missing prerequisite.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PLUGIN = Path(__file__).resolve().parents[1]
EVALS = PLUGIN / "tests" / "evals"
FIXTURES = EVALS / "_fixtures"
EVAL_VENV = PLUGIN / ".demo" / "eval-venv"
BASELINES = PLUGIN / "docs" / "baselines"
SAMPLE = PLUGIN.parents[1] / "examples" / "sample-app"
SCHEMA = 1

#: compare: a stage regressed when its pass rate (share of runs whose outcome checks all passed) fell by more than this.
PASS_RATE_TOLERANCE = 0.20
#: compare: ... or when its mean outcome score (share of a run's checks that passed) fell by more than this.
SCORE_TOLERANCE = 0.05
#: compare: a stage improved when either rose by more than the same tolerance (and neither fell beyond it).
#: compare: a case that passed every old run and no new run is a regression whatever the stage's rates say.
#: compare: cost (and turns) per run are "similar" when the new mean is at most this multiple of the old one.
COST_RATIO_LIMIT = 1.25
TURNS_RATIO_LIMIT = 1.25
#: The tools a stage case needs beyond read-only ones (the eval CLI gates them behind --allow-tools).
GRANTED_TOOLS = ["Bash", "Write", "Edit"]
#: The eval sessions' ledger: an address nothing listens on, so hooks that record runs find no database.
NO_LEDGER_URL = "postgresql://eval:eval@127.0.0.1:9/no_ledger"
#: Ledger metrics the comparison reports (dotted path, which way is better).
METRICS_COMPARED = {
    "first_pass_merge_rate": "higher", "rework_cycles": "lower", "cost_per_merged_contract": "lower",
    "turns_per_merged_contract": "lower", "guard_trips.total": "lower", "denials.rate": "lower",
    "model_overrides.rate": "info",
}


def die(msg: str, code: int = 2) -> None:
    print(f"baseline: {msg}", file=sys.stderr)
    sys.exit(code)


# --------------------------------------------------------------------------- cases


def stage_cases() -> dict[str, dict[str, Any]]:
    """``{case name: {"stage", "dir", "outcome": outcome.json or None}}`` for every case under tests/evals."""
    out: dict[str, dict[str, Any]] = {}
    for case_yaml in sorted(EVALS.glob("**/case.yaml")):
        d = case_yaml.parent
        if "_fixtures" in d.parts or "results" in d.parts:
            continue
        rel = d.relative_to(EVALS).parts
        oc = d / "outcome.json"
        outcome = json.loads(oc.read_text()) if oc.is_file() else None
        name = _yaml_name(case_yaml) or d.name
        out[name] = {"stage": (outcome or {}).get("stage") or rel[0], "dir": str(d.relative_to(PLUGIN)), "outcome": outcome}
    return out


def _yaml_name(path: Path) -> str | None:
    for line in path.read_text().splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return None


# --------------------------------------------------------------------------- the eval result


def eval_runs(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """``{case name: [run, ...]}`` from the ``--json`` document: ``cases[].arms.with[]`` holds one entry per run with
    ``score``, ``passed``, ``turns``, ``costUsd``, ``judgeCostUsd``, ``durationSeconds``, ``error``, ``tracePath`` and
    ``graders`` (Claude Code 2.1.318)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for case in result.get("cases") or []:
        rows = []
        for r in ((case.get("arms") or {}).get("with") or []):
            if not isinstance(r, dict):
                continue
            cost = r.get("costUsd")
            rows.append({
                "native_score": r.get("score"), "native_passed": r.get("passed"),
                "cost_usd": None if cost is None else round(float(cost) + float(r.get("judgeCostUsd") or 0), 6),
                "turns": r.get("turns"), "duration_s": r.get("durationSeconds"), "error": r.get("error"),
                "trace": r.get("tracePath") or None,
                "graders": [{k: g.get(k) for k in ("name", "score", "passed") if k in g}
                            for g in r.get("graders") or [] if isinstance(g, dict)],
            })
        out[str(case.get("name"))] = rows
    return out


def workspace_of(trace: str | None) -> Path | None:
    """The workspace a kept run left: ``<run>/out/trace.jsonl`` -> ``<run>/home/cwd``. With --keep-temp the eval CLI
    seals what the plugin wrote (``<run>/sealed/home/cwd`` under a mode-000 ``sealed`` and a read-only run
    directory); those two are opened (0700) so check.py can read the workspace. Only a directory build.sh made (it
    holds ``.eval/fixture.json``) is returned."""
    if not trace:
        return None
    run = Path(trace).parent.parent
    for ws in (run / "home" / "cwd", run / "sealed" / "home" / "cwd"):
        try:
            if ws.parent.parent.name == "sealed" and (run / "sealed").exists():
                run.chmod(0o700)
                (run / "sealed").chmod(0o700)
            if (ws / ".eval" / "fixture.json").is_file():
                return ws
        except OSError:
            continue
    return None


def check_workspace(stage: str, ws: str, expect: str | None) -> dict[str, Any]:
    cmd = [sys.executable, "-I", str(FIXTURES / "check.py"), "--stage", stage, "--json", ws]
    if expect:
        cmd[5:5] = ["--expect", expect]
    env = {**os.environ, "PATH": f"{EVAL_VENV / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1800)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"passed": False, "score": 0.0, "checks": [], "error": (r.stderr or r.stdout).strip()[-500:]}


def _mean(xs: list[Any]) -> float | None:
    xs = [float(x) for x in xs if isinstance(x, (int, float))]
    return round(statistics.fmean(xs), 4) if xs else None


def started(run: dict[str, Any]) -> bool:
    """Whether the eval CLI started this run's session: it has a trace. A run whose scaffold failed, or whose
    session could not start (an EPERM writing under ~/.claude inside a sandbox), has none."""
    return bool(run.get("trace"))


def completeness(result: dict[str, Any], runs_override: int | None = None) -> dict[str, Any]:
    """``{"ok", "requested", "started", "cases": {case: {"requested", "started"}}, "errors": [...]}``: every case
    must have started as many runs as were asked for (``--runs``, else the case's own ``runsPerCase``); the errors
    are the CLI's own per-run start errors, first ones first."""
    per: dict[str, dict[str, int]] = {}
    errors: list[str] = []
    for case in result.get("cases") or []:
        runs = [r for r in ((case.get("arms") or {}).get("with") or []) if isinstance(r, dict)]
        want = int(runs_override or case.get("runsPerCase") or len(runs) or 1)
        got = sum(1 for r in runs if r.get("tracePath"))
        per[str(case.get("name"))] = {"requested": want, "started": got}
        errors += [f"{case.get('name')}: {r.get('error')}" for r in runs if not r.get("tracePath") and r.get("error")]
    requested = sum(v["requested"] for v in per.values())
    start = sum(v["started"] for v in per.values())
    return {"ok": bool(per) and all(v["started"] >= v["requested"] for v in per.values()),
            "requested": requested, "started": start, "cases": per, "errors": errors}


def refuse_incomplete(check: dict[str, Any], where: Path | str | None) -> int:
    """Print why no baseline is written (exit 1)."""
    print(f"baseline: NOT WRITTEN: {check['started']} of {check['requested']} run(s) started", file=sys.stderr)
    for name, v in check["cases"].items():
        if v["started"] < v["requested"]:
            print(f"  {name}: {v['started']} of {v['requested']} started", file=sys.stderr)
    for e in check["errors"][:10]:
        print(f"  start error: {e[:300]}", file=sys.stderr)
    if not check["cases"]:
        print("  the eval run returned no cases", file=sys.stderr)
    if where:
        print(f"  the eval CLI's own output (result.json, report.html, its log above) is in {where}", file=sys.stderr)
    print("  evals must run from a normal terminal: inside a sandboxed Claude Code session the nested sessions cannot "
          "write under ~/.claude and never start", file=sys.stderr)
    return 1


def build(result: dict[str, Any], *, metrics: dict[str, Any] | None, meta: dict[str, Any]) -> dict[str, Any]:
    known = stage_cases()
    runs_by_case = eval_runs(result)
    cases = []
    for name, runs in runs_by_case.items():
        info = known.get(name, {"stage": "unknown", "dir": None, "outcome": None})
        oc = info["outcome"]
        rows = []
        for r in runs:
            row = dict(r)
            if oc:
                ws = workspace_of(r.get("trace"))
                verdict = check_workspace(oc["stage"], str(ws), oc.get("expect")) if ws else \
                    {"passed": False, "score": 0.0, "checks": [], "error": r.get("error") or "no workspace kept for this run"}
                row.update(outcome="checks", passed=bool(verdict.get("passed")), score=verdict.get("score"),
                           checks=verdict.get("checks"), workspace=str(ws) if ws else None,
                           **({"error": verdict["error"]} if verdict.get("error") else {}))
            else:
                ns = r.get("native_score")
                row.update(outcome="graders", passed=ns is not None and ns >= 1.0, score=ns)
            rows.append(row)
        cases.append({"case": name, "stage": info["stage"], "dir": info["dir"], "runs": rows,
                      "pass_rate": _mean([1.0 if x["passed"] else 0.0 for x in rows]),
                      "mean_score": _mean([x.get("score") for x in rows]),
                      "mean_cost_usd": _mean([x.get("cost_usd") for x in rows]),
                      "mean_turns": _mean([x.get("turns") for x in rows])})
    stages: dict[str, dict[str, Any]] = {}
    for c in cases:
        s = stages.setdefault(c["stage"], {"cases": 0, "runs": 0, "completed": 0, "_p": [], "_s": [], "_c": [], "_t": []})
        s["cases"] += 1
        for r in c["runs"]:
            s["runs"] += 1
            s["completed"] += started(r)
            s["_p"].append(1.0 if r["passed"] else 0.0)
            s["_s"].append(r.get("score"))
            s["_c"].append(r.get("cost_usd"))
            s["_t"].append(r.get("turns"))
    for s in stages.values():
        s.update(pass_rate=_mean(s.pop("_p")), mean_score=_mean(s.pop("_s")), mean_cost_usd=_mean(s.pop("_c")),
                 mean_turns=_mean(s.pop("_t")))
    costs = [r.get("cost_usd") for c in cases for r in c["runs"] if isinstance(r.get("cost_usd"), (int, float))]
    return {"schema": SCHEMA, **meta, "cases": cases, "stages": dict(sorted(stages.items())),
            "totals": {"runs": sum(len(c["runs"]) for c in cases), "cost_usd": round(sum(costs), 4) if costs else None},
            "metrics": metrics}


# --------------------------------------------------------------------------- metrics and provenance


_SNAPSHOT = """
import json, sys
from sdlc_graph import db
from sdlc_graph.ledger import metrics
with db.connect() as conn:
    print(json.dumps(metrics.snapshot(conn, repo=sys.argv[1] or None, since=sys.argv[2] or None), default=str))
"""


def ledger_metrics(repo: str | None = None, since: str | None = None) -> dict[str, Any]:
    """``metrics.snapshot`` of the ledger at DATABASE_URL, through the plugin's own environment (.venv, which has
    psycopg). DATABASE_URL must be set: the baseline never falls back to the sdlc-graph default database."""
    url = os.environ.get("DATABASE_URL")
    if not url or url == NO_LEDGER_URL:
        return {"ok": False, "error": "DATABASE_URL is not set: the metrics need an explicit ledger"}
    py = PLUGIN / ".venv" / "bin" / "python"
    if not py.exists():
        return {"ok": False, "error": f"no {py} (uv sync in the plugin) to read the ledger with"}
    r = subprocess.run([str(py), "-c", _SNAPSHOT, repo or "", since or ""], capture_output=True, text=True,
                       cwd=str(PLUGIN), timeout=300)
    if r.returncode:
        return {"ok": False, "error": (r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[-1][:300]}
    return {"ok": True, "repo": repo, "since": since, "snapshot": json.loads(r.stdout)}


def provenance(args: argparse.Namespace | None = None) -> dict[str, Any]:
    git = lambda *a: subprocess.run(["git", "-C", str(PLUGIN), *a], capture_output=True, text=True).stdout.strip()  # noqa: E731
    cli = shutil.which("claude")
    version = subprocess.run([cli, "--version"], capture_output=True, text=True).stdout.strip() if cli else None
    return {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "git": {"sha": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
                    "dirty": bool(git("status", "--porcelain", "--", "."))},
            "claude_version": version,
            "model": getattr(args, "model", None), "runs_per_case": getattr(args, "runs", None),
            "plugin_version": json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text()).get("version")}


def default_out(meta: dict[str, Any]) -> Path:
    date = meta["created_at"][:10]
    return BASELINES / f"{date}-{meta['git']['sha'][:12] or 'nogit'}.json"


def write(doc: dict[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, default=str) + "\n")
    print(f"baseline written: {out}")
    for st, s in doc["stages"].items():
        print(f"  {st:10} runs {s['runs']:>2}  pass {s['pass_rate']}  score {s['mean_score']}  "
              f"cost/run ${s['mean_cost_usd']}  turns/run {s['mean_turns']}")
    print(f"  total cost ${doc['totals']['cost_usd']}")


# --------------------------------------------------------------------------- commands


def cmd_prepare(_a: argparse.Namespace) -> int:
    if not shutil.which("claude"):
        die("the claude CLI is not on the PATH")
    py = EVAL_VENV / "bin" / "python"
    ok = py.exists() and subprocess.run([str(py), "-c", "import fastapi, sqlalchemy, httpx, pytest, ruff, mypy"],
                                        capture_output=True).returncode == 0
    if not ok:
        if not shutil.which("uv"):
            die("uv is needed to build .demo/eval-venv")
        deps = ["fastapi==0.115.6", "uvicorn==0.34.0", "sqlalchemy==2.0.36", "httpx==0.28.1", "pytest==8.3.4", "ruff", "mypy"]
        # The interpreter goes inside the plugin tree: the eval sandbox cannot execute one under ~/.local/share/uv.
        env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(EVAL_VENV.parent / "uv-python")}
        subprocess.run(["uv", "venv", "-q", "--managed-python", "--python", "3.11", str(EVAL_VENV)], check=True, env=env)
        subprocess.run(["uv", "pip", "install", "-q", "--python", str(py), *deps], check=True)
    print(f"eval python ready: {py}")
    return 0


def eval_command(a: argparse.Namespace, out: Path, case: str | None = None) -> list[str]:
    cmd = ["claude", "plugin", "eval", str(PLUGIN), "--scaffold", "--trust-plugin", "--keep-temp", "--ablation", "none",
           "--no-publish", "--threshold", "0", "--concurrency", "1", "--allow-tools", *GRANTED_TOOLS,
           "--json", str(out / "result.json"), "--output-dir", str(out)]
    if a.runs:
        cmd += ["--runs", str(a.runs)]
    if a.model:
        cmd += ["--model", a.model]
    if a.max_cost_usd:
        cmd += ["--max-cost-usd", str(a.max_cost_usd)]
    if case:
        cmd += ["--case", case]
    if a.stage:
        cmd += ["--tag", *a.stage]
    return cmd


def git_dir() -> str | None:
    """The directory of a git that works inside the sandbox. On macOS /usr/bin/git is an xcrun shim that writes a
    cache under the user's temp directory, which the eval sandbox refuses, so the real binary's directory is used."""
    git = shutil.which("git")
    if git == "/usr/bin/git" and shutil.which("xcrun"):
        found = subprocess.run(["xcrun", "--find", "git"], capture_output=True, text=True).stdout.strip()
        git = found or git
    return str(Path(git).parent) if git else None


def eval_bin() -> Path:
    """``.demo/eval-bin``, first on the eval sessions' PATH: ``gh`` (the stand-in from tests/evals/_fixtures/bin,
    copied, since the eval sandbox hides the suite's own directory from the session) and, when the git on the PATH
    is the macOS xcrun shim, ``git``, a two-line script that runs the real binary (:func:`git_dir`); a link would
    bring nothing, and that directory's other tools (Xcode's holds a python3) stay off the PATH."""
    bin_dir = EVAL_VENV.parent / "eval-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    gh = bin_dir / "gh"
    shutil.copyfile(FIXTURES / "bin" / "gh", gh)
    gh.chmod(0o755)
    real, git = git_dir(), bin_dir / "git"
    if real and real != "/usr/bin":
        git.write_text(f'#!/bin/sh\nexec "{Path(real) / "git"}" "$@"\n')
        git.chmod(0o755)
    elif git.exists() or git.is_symlink():
        git.unlink()
    return bin_dir


def eval_env() -> dict[str, str]:
    """The eval sessions inherit this environment: :func:`eval_bin` and the eval Python first on the PATH, and a
    ledger URL nothing answers."""
    parts = [str(eval_bin()), str(EVAL_VENV / "bin")]
    path = os.pathsep.join([*parts, os.environ.get("PATH", "")])
    return {**os.environ, "PATH": path, "FACTORY_PLUGIN_ROOT": str(PLUGIN), "SAMPLE_APP": str(SAMPLE),
            "DATABASE_URL": NO_LEDGER_URL}


def _cleanup(doc: dict[str, Any]) -> None:
    """Remove each kept run directory whose workspace check.py read (``<run>`` above ``home/cwd``)."""
    for c in doc["cases"]:
        for r in c["runs"]:
            ws = Path(r["workspace"]) if r.get("workspace") else None
            if ws and (ws / ".eval" / "fixture.json").is_file():
                run = ws.parents[2] if ws.parent.parent.name == "sealed" else ws.parents[1]
                for d in (run, *run.rglob("*")):
                    try:
                        if d.is_dir() and not d.is_symlink():
                            d.chmod(0o700)
                    except OSError:
                        pass
                shutil.rmtree(run, ignore_errors=True)


def cmd_run(a: argparse.Namespace) -> int:
    if not (EVAL_VENV / "bin" / "python").exists():
        die("no eval python: run `baseline.py prepare` first")
    tmp = Path(tempfile.mkdtemp(prefix="factory-baseline-"))
    # one eval run per --case glob (the CLI takes one glob), merged; no --case: one run over the whole suite
    result: dict[str, Any] = {"cases": [], "costUsd": 0.0}
    exits = []
    for i, case in enumerate(a.case or [None]):
        out = tmp / f"eval-{i}"
        out.mkdir()
        cmd = eval_command(a, out, case)
        print("$ " + " ".join(cmd), flush=True)
        if a.dry_run:
            continue
        r = subprocess.run(cmd, env=eval_env(), cwd=str(PLUGIN))
        exits.append(r.returncode)
        if not (out / "result.json").is_file():
            die(f"the eval run wrote no result (exit {r.returncode}); its output dir is {out}", 1)
        part = json.loads((out / "result.json").read_text())
        result["cases"] += part.get("cases") or []
        result["costUsd"] += float(part.get("costUsd") or 0)
        result.setdefault("claudeVersion", part.get("claudeVersion"))
    if a.dry_run:
        return 0
    check = completeness(result, a.runs)
    if not check["ok"]:
        return refuse_incomplete(check, tmp)
    meta = provenance(a)
    meta["eval_exits"] = exits
    meta["eval_cost_usd"] = round(result["costUsd"], 4)
    doc = build(result, metrics=None if a.no_metrics else ledger_metrics(a.repo, a.since), meta=meta)
    write(doc, Path(a.out) if a.out else default_out(meta))
    if a.keep:
        print(f"kept: {tmp} and each run's workspace (the baseline's runs[].workspace)")
    else:
        _cleanup(doc)
    return 0


def cmd_score(a: argparse.Namespace) -> int:
    result = json.loads(Path(a.result).read_text())
    check = completeness(result)
    if not check["ok"]:
        return refuse_incomplete(check, Path(a.result).parent)
    meta = provenance(a)
    doc = build(result, metrics=None if a.no_metrics else ledger_metrics(a.repo, a.since), meta=meta)
    write(doc, Path(a.out) if a.out else default_out(meta))
    return 0


# --------------------------------------------------------------------------- compare


def _ratio(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old <= 0:
        return None
    return round(new / old, 3)


def _dig(d: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        d = d.get(part) if isinstance(d, dict) else None
    return d


def unusable(doc: dict[str, Any]) -> list[str]:
    """Why a baseline cannot be compared: no stage at all, or a stage with no completed run (a run the eval CLI
    started; an older file without ``completed`` counts its runs)."""
    stages = doc.get("stages") or {}
    if not stages:
        return ["it has no stages"]
    return [f"stage {st!r} has no completed run" for st, s in sorted(stages.items())
            if not int(s.get("completed", s.get("runs") or 0) or 0)]


def compare(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Per stage: ``outcome`` held | improved | regressed | not compared, ``cost`` similar | higher | lower | unknown,
    the deltas, and the cases that were always passing before and never now. ``ok`` is false when any stage
    regressed or got more expensive than :data:`COST_RATIO_LIMIT` (or its turns than :data:`TURNS_RATIO_LIMIT`)."""
    out: dict[str, Any] = {"stages": {}, "lost_cases": [], "metrics": {}}
    old_cases = {c["case"]: c for c in old.get("cases") or []}
    new_cases = {c["case"]: c for c in new.get("cases") or []}
    lost = [n for n, c in new_cases.items() if n in old_cases and old_cases[n]["pass_rate"] == 1.0 and c["pass_rate"] == 0.0]
    out["lost_cases"] = sorted(lost)
    for stage in sorted(set(old.get("stages") or {}) | set(new.get("stages") or {})):
        o, n = (old.get("stages") or {}).get(stage), (new.get("stages") or {}).get(stage)
        if not o or not n:
            out["stages"][stage] = {"outcome": "not compared", "why": "missing from the old baseline" if not o else "missing from the new one"}
            continue
        dp = None if o["pass_rate"] is None or n["pass_rate"] is None else round(n["pass_rate"] - o["pass_rate"], 4)
        ds = None if o["mean_score"] is None or n["mean_score"] is None else round(n["mean_score"] - o["mean_score"], 4)
        stage_lost = [c for c in lost if new_cases[c]["stage"] == stage]
        if stage_lost or (dp is not None and dp < -PASS_RATE_TOLERANCE) or (ds is not None and ds < -SCORE_TOLERANCE):
            verdict = "regressed"
        elif (dp is not None and dp > PASS_RATE_TOLERANCE) or (ds is not None and ds > SCORE_TOLERANCE):
            verdict = "improved"
        else:
            verdict = "held"
        cr, tr = _ratio(n["mean_cost_usd"], o["mean_cost_usd"]), _ratio(n["mean_turns"], o["mean_turns"])
        cost = "unknown" if cr is None else ("higher" if cr > COST_RATIO_LIMIT else ("lower" if cr < 1 / COST_RATIO_LIMIT else "similar"))
        turns = "unknown" if tr is None else ("higher" if tr > TURNS_RATIO_LIMIT else ("lower" if tr < 1 / TURNS_RATIO_LIMIT else "similar"))
        out["stages"][stage] = {"outcome": verdict, "pass_rate": [o["pass_rate"], n["pass_rate"]], "pass_rate_delta": dp,
                                "score": [o["mean_score"], n["mean_score"]], "score_delta": ds, "cost": cost,
                                "cost_ratio": cr, "turns": turns, "turns_ratio": tr, "lost_cases": stage_lost}
    om = ((old.get("metrics") or {}).get("snapshot")) or {}
    nm = ((new.get("metrics") or {}).get("snapshot")) or {}
    for key, better in METRICS_COMPARED.items():
        a, b = _dig(om, key), _dig(nm, key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            delta = round(b - a, 4)
            trend = "same" if delta == 0 else ("info" if better == "info" else
                                               ("better" if (delta > 0) == (better == "higher") else "worse"))
        else:
            delta, trend = None, "unknown"
        out["metrics"][key] = {"old": a, "new": b, "delta": delta, "trend": trend}
    out["ok"] = all(s["outcome"] in ("held", "improved", "not compared") and s.get("cost") != "higher"
                    and s.get("turns") != "higher" for s in out["stages"].values())
    return out


def cmd_compare(a: argparse.Namespace) -> int:
    try:
        old, new = (json.loads(Path(p).read_text()) for p in (a.old, a.new))
    except (OSError, ValueError) as exc:
        die(f"cannot read the baselines: {exc}")
    for name, doc in ((a.old, old), (a.new, new)):
        why = unusable(doc)
        if why:
            die(f"refusing {name}: {'; '.join(why)} (a baseline whose runs never started measures nothing)")
    res = compare(old, new)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        for stage, s in res["stages"].items():
            if s["outcome"] == "not compared":
                print(f"{stage:10} not compared ({s['why']})")
                continue
            print(f"{stage:10} {s['outcome']:9} pass {s['pass_rate'][0]} -> {s['pass_rate'][1]}  "
                  f"score {s['score'][0]} -> {s['score'][1]}  cost {s['cost']} (x{s['cost_ratio']})  "
                  f"turns {s['turns']} (x{s['turns_ratio']})" + (f"  lost: {', '.join(s['lost_cases'])}" if s["lost_cases"] else ""))
        for key, m in res["metrics"].items():
            if m["trend"] != "unknown":
                print(f"metric {key}: {m['old']} -> {m['new']} ({m['trend']})")
        print("OK: outcomes held at similar cost" if res["ok"] else "NOT OK: a stage regressed or got more expensive")
    return 0 if res["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare").set_defaults(fn=cmd_prepare)
    r = sub.add_parser("run")
    r.add_argument("--stage", nargs="+", help="eval tags (stage names: tests implement verify ship; epic cases have none)")
    r.add_argument("--case", nargs="+", help="case name globs (one eval run each)")
    r.add_argument("--runs", type=int, help="runs per case (default: each case's own, 3)")
    r.add_argument("--model")
    r.add_argument("--max-cost-usd", type=float)
    r.add_argument("--out")
    r.add_argument("--no-metrics", action="store_true")
    r.add_argument("--repo", help="metrics: one repository's nodes only")
    r.add_argument("--since", help="metrics: nodes touched since this ISO date (e.g. a week before the run)")
    r.add_argument("--keep", action="store_true", help="keep the eval workspaces")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("score")
    s.add_argument("result")
    s.add_argument("--out")
    s.add_argument("--no-metrics", action="store_true")
    s.add_argument("--repo")
    s.add_argument("--since")
    s.set_defaults(fn=cmd_score)
    c = sub.add_parser("compare")
    c.add_argument("old")
    c.add_argument("new")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_compare)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
