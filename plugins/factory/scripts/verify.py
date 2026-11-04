"""The gate behind /factory:verify and the commit guard.

Run order: tests -> e2e (when .claude/sdlc.json has an `e2e` block) -> ratchet -> frontend (a Foldkit app) -> secret
scan -> check_agent_config (when the script exists) -> freeze_tests --check (REC-07) -> acceptance (REC-09, when the
contract has lines) -> stamp.
Every step runs even after a failure so one run shows everything; exit 1 names the failed steps.
Green -> writes .claude/work/last_verify.json: the tree fingerprint plus db_backend, tool_versions,
acceptance, frozen_ok, capability_findings (count of accesses outside the contract's declared capabilities; a reviewer-visible
finding, never a failure) and checks {name: exit} (REC-136), and for a Foldkit frontend (config "frontend")
frontend {framework, programs, stories: {tags, covered}, scenes: {files, with_exist}, api: {calls, matched},
oxlint: {findings}}, and for an e2e suite e2e {command, url, server: reused|started|none, recorded_at, passed, failed,
skipped}. The e2e step reads the run `scripts/e2e.py run` recorded under .claude/work/e2e/ (no browser launches inside
the sandbox, so the suite runs through that top-level command, which the human excludes from the sandbox; it builds and
serves the app itself, reusing this checkout's own server) and is red when there is no run, when the tree changed since
it (its fingerprint), or when it was narrowed to some spec files. Its JSON report goes to freeze_tests --check, which
fails when a frozen Playwright spec did not run or was skipped. The frontend step fails on fe-story or fe-scene keys that are new or grown versus the
ratchet baseline (when fe_story / fe_scene are in "checkers"), so a brownfield app's recorded debt does not
block it. The Bash guard refuses `git commit` for a tree whose fingerprint differs. Red -> removes any old
stamp and exits 1.

`--skip-tests` runs every other step for a quick look but can NEVER stamp: a stamp would let the
commit guard pass a tree whose tests fail. It exits 1 with "VERIFY NOT STAMPED". Only added lines
of the diff (and untracked files) are secret-scanned, and a hit prints file and line, never the secret.

Usage: verify.py [--root DIR] [--skip-tests]
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import e2e as e2e_lib  # noqa: E402
import sdlc as cl  # noqa: E402
import frontend_foldkit as fk  # noqa: E402
import test_reasons as tr  # noqa: E402

SECRET_RE = re.compile("|".join(p for p, label in cl.SECRET_PATTERNS if label != "hard-coded credential"))


def step(name: str, ok: bool, detail: str = "") -> bool:
    print(f"\n== {name}\n{detail.rstrip()}\n-- {'ok' if ok else 'FAILED: ' + name}")
    return ok


def script(name: str) -> str:
    return os.path.join(cl.plugin_root(), "scripts", name)


def tool_of(cmd: str) -> str | None:
    """First tool named by a configured command: `python3 -m ruff ...` -> ruff, `go test` -> go."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    while parts and "=" in parts[0] and not parts[0].startswith("-"):
        parts = parts[1:]
    if not parts:
        return None
    if len(parts) > 2 and parts[0].startswith("python") and parts[1] == "-m":
        return parts[2]
    return os.path.basename(parts[0])


def version_of(tool: str) -> str:
    cmds = [[tool, "--version"]]
    if tool != "go":
        cmds.append(["python3", "-m", tool, "--version"])
    else:
        cmds = [["go", "version"]]
    for cmd in cmds:
        try:
            proc = cl.run(cmd)
        except OSError:
            continue
        text = (proc.stdout or proc.stderr).strip().splitlines()
        if proc.returncode == 0 and text:
            return text[0]
    return "missing"


def tool_versions(cfg: dict) -> dict:
    tools = ["python3"]
    for key in ("test", "lint_report", "typecheck"):
        t = tool_of((cfg.get("commands") or {}).get(key) or "")
        if t and t not in tools:
            tools.append(t)
    out = {}
    for t in tools:
        out["python" if t == "python3" else t] = version_of(t)
    return out


def scan_secrets(root: str) -> list[str]:
    """`file:line: <label>` for each secret-looking ADDED line (deleted lines are not scanned: removing a
    leaked secret must not turn verify red). The matched text is never printed."""
    hits: list[str] = []
    cur = "?"
    lineno = 0
    for raw in cl.git(root, "diff", "HEAD", "-U0", "--no-color").splitlines():
        if raw.startswith("+++ "):
            cur = raw[6:] if raw.startswith("+++ b/") else raw[4:]
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) - 1 if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            lineno += 1
            if SECRET_RE.search(raw[1:]):
                hits.append(f"{cur}:{lineno}: secret-looking value (redacted)")
    for rel in [p for p in cl.git(root, "ls-files", "--others", "--exclude-standard").splitlines() if p]:
        try:
            with open(os.path.join(root, rel), "r", encoding="utf-8", errors="ignore") as fh:
                for n, text in enumerate(fh, 1):
                    if SECRET_RE.search(text):
                        hits.append(f"{rel}:{n}: secret-looking value (redacted)")
        except OSError:
            pass
    return hits


def e2e_gate(root: str, block: dict) -> tuple[dict, int, str, str | None]:
    """(stamp block, exit, detail, JSON report path) for the end-to-end suite: the run `scripts/e2e.py run` recorded under
    .claude/work/e2e/ (the suite cannot run inside the sandbox, where no browser launches), refused when the tree moved
    since (fingerprint) or the run was narrowed to some spec files (verify needs the whole suite)."""
    empty = {"command": block["command"], "url": block["url"], "server": "none", "passed": 0, "failed": 0, "skipped": 0}
    run, report = e2e_lib.load_run(root)
    why = e2e_lib.staleness(root, run)
    if why:
        return empty, 1, why, None
    if run.get("files"):
        return empty, 1, (f"the recorded e2e run covered only {', '.join(run['files'])}; verify needs the whole suite: run "
                          f"`{e2e_lib.RUN_COMMAND.format(plugin=cl.plugin_root())}` with no file, then rerun"), None
    try:
        with open(e2e_lib.run_paths(root)["output"], encoding="utf-8", errors="replace") as fh:
            output = fh.read()
    except OSError:
        output = ""
    parsed = tr.parse_playwright(output, report, root)
    n = e2e_lib.counts(parsed)
    code = int(run.get("exit") or 0)
    lines = [f"({run.get('command', '?')}; recorded {run.get('finished_at', '?')}, server {run.get('server', '?')} at {run.get('url') or '(Playwright webServer)'})"]
    lines += [f"  {row['reason']:<16} {row['test']}" for row in parsed["results"]]
    lines += [f"  run error: {err['detail']}" for err in parsed.get("errors") or []]
    if code != 0 and not parsed["results"] and not parsed.get("errors"):
        lines.append("\n".join(output.strip().splitlines()[-6:]))
    lines.append(f"e2e: {n['passed']} passed, {n['failed']} failed, {n['skipped']} skipped")
    if code == 0 and (n["failed"] or parsed.get("errors")):
        code = 1
    if report is None:
        lines.append("no json report in the recorded run: the frozen-test check cannot see which specs ran")
    stamp = {"command": str(run.get("command") or block["command"]), "url": str(run.get("url") or block["url"]),
             "server": str(run.get("server") or "none"), "recorded_at": str(run.get("finished_at") or ""), **n}
    report_path = e2e_lib.run_paths(root)["report"] if report is not None else None
    return stamp, code, "\n".join(lines), report_path


def frontend_gate(root: str, cfg: dict) -> tuple[dict | None, int, str]:
    """(stamp block, exit, detail) for a Foldkit frontend; (None, 0, "") when there is none."""
    block = fk.summary(root, cfg)
    if block is None:
        return None, 0, ""
    findings = block.pop("_findings")
    gate = tuple(fk.CHECKERS[n] + "|" for n in ("fe_story", "fe_scene") if n in (cfg.get("checkers") or []))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ratchet  # noqa: E402 - the baseline reader the ratchet itself uses

    try:
        known, _, _ = ratchet.load_baseline(os.path.join(root, cfg.get("ratchet_baseline") or ".claude/baseline.json"))
    except (OSError, ValueError, TypeError, AttributeError):
        known = {}
    counts: dict[str, int] = {}
    msgs: dict[str, str] = {}
    for key, msg in findings:
        if gate and key.startswith(gate):
            counts[key] = counts.get(key, 0) + 1
            msgs.setdefault(key, msg)
    new = sorted(k for k, n in counts.items() if n > known.get(k, 0))
    st, sc, api, ox = block["stories"], block["scenes"], block["api"], block["oxlint"]
    lines = [f"stories: {st['covered']}/{st['tags']} Message tags sent by a story test",
             f"scenes: {sc['with_exist']}/{sc['files']} scene files assert toExist",
             f"api: {api['matched']}/{api['calls']} Command calls go through the api table",
             f"oxlint: {ox['findings']} finding(s)" if ox.get("findings") is not None
             else f"oxlint: not run ({str(ox.get('note') or 'frontend.oxlint is off').removeprefix('oxlint: ')})"]
    if not gate:
        lines.append("(fe_story / fe_scene are not in checkers: not gated)")
    lines += [f"NEW {msgs[k]}" for k in new]
    return block, 1 if new else 0, "\n".join(lines)


def capability_findings(root: str) -> tuple[int, str]:
    """(count, detail) of the declared-versus-observed capability findings of the active contract
    (.claude/work/<slug>/capability_findings.jsonl, written by hooks/observe_capabilities.py). Informational: the
    reviewer reads them, they never fail the gate."""
    contract = cl.load_contract(root)
    slug = contract.get("slug") if isinstance(contract, dict) else None
    if not isinstance(slug, str) or not slug or "/" in slug or slug.startswith("."):
        return 0, ""
    rows: list[str] = []
    try:
        with open(os.path.join(root, ".claude", "work", slug, "capability_findings.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    rows.append(f"{rec.get('kind')}: {rec.get('target')} ({rec.get('command') or rec.get('tool')})")
    except OSError:
        return 0, ""
    return len(rows), "\n".join(rows[:20] + ([f"... and {len(rows) - 20} more"] if len(rows) > 20 else []))


def main(argv: list[str]) -> int:
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    root = None
    skip_tests = "--skip-tests" in argv
    if "--root" in argv:
        root = argv[argv.index("--root") + 1]
    root = cl.project_root(root)
    cfg = cl.load_config(root)
    stamp_path = os.path.join(root, cl.STAMP_REL)
    if os.path.exists(stamp_path):
        os.remove(stamp_path)
    if not cfg["_configured"]:
        print("verify: this repo is not initialised; run /factory:init first")
        return 2

    checks: dict[str, int] = {}
    failed: list[str] = []
    junit: str | None = None

    def record(name: str, code: int, detail: str) -> None:
        checks[name] = code
        if not step(name, code == 0, detail):
            failed.append(name)

    test_cmd = (cfg.get("commands") or {}).get("test") or ""
    if skip_tests or not test_cmd:
        print("\n== tests\n-- skipped (no test command configured)" if not test_cmd else "\n== tests\n-- skipped (--skip-tests)")
        if not test_cmd:
            checks["tests"] = 1
            failed.append("tests (no test command configured)")
        else:
            checks["tests"] = 1
            failed.append("tests (skipped by --skip-tests; a skipped run cannot stamp)")
    else:
        run_cmd = test_cmd
        if re.search(r"pytest[^;&|]*$", test_cmd) and "--junitxml" not in test_cmd:
            junit = os.path.join(tempfile.mkdtemp(prefix="verify-junit-"), "junit.xml")
            run_cmd = f"{test_cmd} --junitxml={shlex.quote(junit)}"
        proc = cl.run(run_cmd, cwd=root, shell=True)
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
        record("tests", proc.returncode, f"({test_cmd})\n{tail}")

    e2e_block = e2e_lib.settings(cfg)
    e2e_stamp: dict | None = None
    e2e_report: str | None = None
    if e2e_block is not None:
        if skip_tests:
            print("\n== e2e\n-- skipped (--skip-tests)")
            checks["e2e"] = 1
        else:
            e2e_stamp, e2e_code, e2e_detail, e2e_report = e2e_gate(root, e2e_block)
            record("e2e", e2e_code, e2e_detail)

    proc = cl.run([sys.executable, "-I", script("ratchet.py"), "--root", root], cwd=root)
    record("ratchet", proc.returncode, proc.stdout + proc.stderr)

    frontend, fe_code, fe_detail = frontend_gate(root, cfg)
    if frontend is not None:
        record("frontend", fe_code, fe_detail)

    secret_hits = scan_secrets(root)
    checks["secrets"] = 1 if secret_hits else 0
    if not step("secrets in diff", not secret_hits, "\n".join(secret_hits) if secret_hits else "none"):
        failed.append("secrets")

    if os.path.exists(script("check_agent_config.py")):
        proc = cl.run([sys.executable, "-I", script("check_agent_config.py"), "--root", root], cwd=root)
        record("agent_config", proc.returncode, proc.stdout + proc.stderr)

    freeze_cmd = [sys.executable, "-I", script("freeze_tests.py"), "--root", root, "--check"]
    if junit and os.path.exists(junit):
        freeze_cmd += ["--junit", junit]
    if e2e_report and os.path.exists(e2e_report):
        freeze_cmd += ["--e2e-report", e2e_report]
    proc = cl.run(freeze_cmd, cwd=root)
    record("frozen_tests", proc.returncode, proc.stdout + proc.stderr)
    frozen_ok = proc.returncode == 0
    if junit:
        shutil.rmtree(os.path.dirname(junit), ignore_errors=True)

    acceptance = {"passed": 0, "failed": 0}
    proc = cl.run([sys.executable, "-I", script("acceptance.py"), "--root", root], cwd=root)
    m = re.search(r"^ACCEPTANCE passed=(\d+) failed=(\d+)$", proc.stdout, re.M)
    if m and (int(m.group(1)) + int(m.group(2))) > 0:
        acceptance = {"passed": int(m.group(1)), "failed": int(m.group(2))}
        record("acceptance", proc.returncode, proc.stdout + proc.stderr)
    elif proc.returncode not in (0,):
        record("acceptance", proc.returncode, proc.stdout + proc.stderr)
    else:
        print("\n== acceptance\n-- skipped (no acceptance lines in the contract)")

    if failed:
        if skip_tests and test_cmd:
            print("\nVERIFY NOT STAMPED: --skip-tests never stamps (failed: " + ", ".join(failed) + "); run verify without it")
        else:
            print("\nVERIFY RED: failed: " + ", ".join(failed) + "; fix them and rerun")
        return 1
    cap_n, cap_detail = capability_findings(root)
    if cap_n:
        print(f"\n== capability findings\ncapability_findings: {cap_n} access(es) outside the contract's declared capabilities "
              f"(reviewer-visible, not a failure):\n{cap_detail}\n-- noted")
    fp = cl.run([sys.executable, "-I", script("worktree_fingerprint.py"), root]).stdout.strip()
    db_url = os.environ.get("DATABASE_URL", "")
    stamp = {
        "ok": True,
        "fingerprint": fp,
        "head": cl.git(root, "rev-parse", "--short", "HEAD").strip(),
        "stack": cfg.get("stack"),
        "db_backend": db_url.split(":", 1)[0] if ":" in db_url else "sqlite",
        "tool_versions": tool_versions(cfg),
        "acceptance": acceptance,
        "frozen_ok": frozen_ok,
        "checks": checks,
        "capability_findings": cap_n,
    }
    if frontend is not None:
        stamp["frontend"] = frontend
    if e2e_stamp is not None:
        stamp["e2e"] = e2e_stamp
    os.makedirs(os.path.dirname(stamp_path), exist_ok=True)
    with open(stamp_path, "w", encoding="utf-8") as fh:
        json.dump(stamp, fh)
        fh.write("\n")
    print(f"\nVERIFY GREEN fingerprint={fp}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
