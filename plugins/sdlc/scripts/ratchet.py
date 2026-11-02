#!/usr/bin/env python3
"""Quality ratchet: no NEW violations compared with .claude/baseline.json.

Why: tooling that fails on the whole tree on day one gets disabled on day two. The ratchet
records today's debt once (`--update`, humans only) and then fails only on violations that are
not in the baseline. `--all` lists the known debt for deliberate burn-down.

Sources of violations, all keyed without line numbers so edits above a violation are not "new":
  checkers (scripts/checkers/<stack>/<name>.py, from config "checkers"): key|...|...
  lint (config commands.lint_report parsed by lint_format):           lint|file|rule
  types (config commands.typecheck parsed by typecheck_format):       types|file|code

Baseline v2 (REC-116): {"version": 2, "head", "stack", "counts": {key: n}}. A key not in the
baseline is new; a key whose current count exceeds its baseline count is "more of the same" (+n).
Lower counts pass. v1 files ({"keys": [...]}) load as counts of 1. Stale keys (in the baseline,
no longer firing) only warn. `--prune` lowers counts to current values and drops stale keys;
`--update` rewrites everything. Both are humans-only (the guards block them) and print what changed.
`--baseline PATH` overrides the baseline file (CI points it at the base branch's copy).
`tool_versions` in the config (REC-24) warns when the installed ruff/mypy differs from the pin.

Trust: a PR must not be able to edit the checks that grade it. With `--baseline` (or CI=true) and a
base ref (`--base-ref REF`, default env BASE_REF as origin/$BASE_REF) the CONFIG is read from the base
ref (`git show origin/$BASE_REF:.claude/sdlc.json`), not from the PR tree; `--config PATH` pins
one explicitly. If the base has no config the PR tree's is used, with a note. Under `--baseline` or
CI=true a missing config is exit 2, never 0. A tool that was not run (missing, or --fast) is never
stale and `--prune`/`--update` keep its old counts; a checker that crashes fails the run with exit 2;
with CI=true or `--require-tools` a missing lint/typecheck tool is exit 2 too.

Usage: ratchet.py [--root DIR] [--baseline PATH] [--config PATH] [--base-ref REF] [--files f ...] [--all] [--update] [--prune] [--fast] [--require-tools]
Exit: 0 clean, 1 new or grown violations, 2 tooling error, checker crash, or not initialised under --baseline/CI.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
import atexit
import contextlib
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import sdlc as cl  # noqa: E402

Finding = tuple[str, str]


def parse_report(fmt: str, out: str, err: str, root: str, prefix: str) -> tuple[list[Finding], str | None]:
    findings: list[Finding] = []
    text = out if out.strip() else err
    try:
        if fmt == "ruff-json":
            for item in json.loads(out or "[]"):
                rel = cl.rel_to(root, item["filename"])
                row = (item.get("location") or {}).get("row", 0)
                findings.append((f"{prefix}|{rel}|{item['code']}", f"{rel}:{row}: {item['code']} {item['message']}"))
        elif fmt == "eslint-json":
            for entry in json.loads(out or "[]"):
                rel = cl.rel_to(root, entry["filePath"])
                for m in entry.get("messages", []):
                    rule = m.get("ruleId") or ("fatal" if m.get("fatal") else "unknown")
                    findings.append((f"{prefix}|{rel}|{rule}", f"{rel}:{m.get('line', 0)}: {rule} {m.get('message', '')}"))
        elif fmt == "golangci-json":
            for issue in (json.loads(out or "{}") or {}).get("Issues") or []:
                pos = issue.get("Pos") or {}
                rel = pos.get("Filename", "?")
                findings.append((f"{prefix}|{rel}|{issue.get('FromLinter', 'lint')}", f"{rel}:{pos.get('Line', 0)}: {issue.get('FromLinter', 'lint')} {issue.get('Text', '')}"))
        elif fmt in ("go-vet-text", "go-build-text", "tsc-text", "mypy-text"):
            for line in text.splitlines():
                m = None
                if fmt == "tsc-text":
                    m = re.match(r"^(.+?)\((\d+),\d+\): error (TS\d+): (.*)$", line)
                    if m:
                        rel, row, code, msg = m.group(1), m.group(2), m.group(3), m.group(4)
                elif fmt == "mypy-text":
                    m = re.match(r"^(.+?):(\d+)(?::\d+)?: error: (.*?)\s+\[([\w-]+)\]$", line)
                    if m:
                        rel, row, msg, code = m.group(1), m.group(2), m.group(3), m.group(4)
                else:
                    m = re.match(r"^(?:vet: )?(\.?/?[^:\s]+\.go):(\d+)(?::\d+)?: (.*)$", line)
                    if m:
                        rel, row, msg = m.group(1).lstrip("./"), m.group(2), m.group(3)
                        code = "vet" if fmt == "go-vet-text" else "build"
                if m:
                    findings.append((f"{prefix}|{rel}|{code}", f"{rel}:{row}: {code} {msg}"))
        elif fmt == "cargo-json":
            for line in out.splitlines():
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("reason") != "compiler-message":
                    continue
                msg = obj.get("message") or {}
                if msg.get("level") not in ("warning", "error"):
                    continue
                spans = [s for s in msg.get("spans") or [] if s.get("is_primary")] or (msg.get("spans") or [])
                if not spans:
                    continue
                rel = spans[0].get("file_name", "?")
                code = (msg.get("code") or {}).get("code") or msg.get("level")
                findings.append((f"{prefix}|{rel}|{code}", f"{rel}:{spans[0].get('line_start', 0)}: {code} {msg.get('message', '')}"))
        elif fmt:
            return [], f"unknown report format {fmt}"
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return [], f"{fmt} output not parsed: {exc}"
    return findings, None


CHECKER_PREFIX = {"route_auth": "route-auth", "module_state": "state", "test_quality": "tests",
                  "stubs": "stubs", "dead_code": "dead", "api_contract": "contract"}


def checker_failed(proc) -> bool:
    """A checker prints findings on stdout and exits 0 or 1. A traceback, exit > 1, or exit 1 with an
    empty stdout is a crash, which must never read as "no findings"."""
    if proc.returncode == 0:
        return False
    return proc.returncode > 1 or not (proc.stdout or "").strip() or "Traceback" in (proc.stderr or "")


def collect(cfg: dict, root: str, fast: bool) -> tuple[list[Finding], list[str], list[str], dict[str, str]]:
    """Return (findings, notes, errors, skipped). `skipped` maps a key prefix to why it did not run
    ("missing" or "fast"); its baseline keys are neither stale nor pruned."""
    findings: list[Finding] = []
    notes: list[str] = []
    errors: list[str] = []
    skipped: dict[str, str] = {}
    stack = cfg.get("stack")
    for name in cfg.get("checkers") or []:
        script = os.path.join(cl.plugin_root(), "scripts", "checkers", stack, f"{name}.py")
        if not os.path.exists(script):
            notes.append(f"checker {stack}/{name} not found; skipped")
            skipped[CHECKER_PREFIX.get(name, name)] = "missing"
            continue
        proc = cl.run([sys.executable, "-I", script, "--root", root, "--config", cfg["_path"]], cwd=root)
        if checker_failed(proc):
            errors.append(f"checker {name} crashed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[-300:]}")
            continue
        for line in proc.stdout.splitlines():
            if "\t" in line:
                key, msg = line.split("\t", 1)
                findings.append((key, msg))

    commands = cfg.get("commands") or {}
    for key, fmt_key, prefix in (("lint_report", "lint_format", "lint"), ("typecheck", "typecheck_format", "types")):
        if fast and key == "typecheck":
            skipped[prefix] = "fast"
            continue
        command, fmt = commands.get(key) or "", cfg.get(fmt_key) or ""
        if not command or not fmt:
            continue
        command = command.replace("{app_dirs}", " ".join(d.rstrip("/*") or "." for d in cfg["paths"].get("app", [])))
        proc = cl.run(command, cwd=root, shell=True)
        err = proc.stderr or ""
        if proc.returncode == 127 or ("not found" in err[:300] or "no such command" in err[:300]) and not proc.stdout.strip():
            notes.append(f"{key}: tool not available ({command.split()[0]} — {err.strip().splitlines()[0][:80] if err.strip() else 'exit 127'}); not ratcheted")
            skipped[prefix] = "missing"
            continue
        if proc.returncode not in (0, 1, 2, 3) and not proc.stdout.strip():
            notes.append(f"{key}: command failed (exit {proc.returncode}): {err.strip()[-160:]}")
            skipped[prefix] = "missing"
            continue
        parsed, err = parse_report(fmt, proc.stdout, proc.stderr, root, prefix)
        if err:
            notes.append(f"{key}: {err}")
            skipped[prefix] = "missing"
        findings.extend(parsed)
    return findings, notes, errors, skipped


def is_skipped(key: str, skipped: dict[str, str]) -> bool:
    return key.split("|", 1)[0] in skipped


@contextlib.contextmanager
def config_overlay(root: str, text: str | None):
    """Checkers call cl.load_config(root) themselves, so while they run the trusted config text is
    placed at .claude/sdlc.json and the tree's own file (or its absence) is restored afterwards."""
    if text is None:
        yield
        return
    path = os.path.join(root, cl.CONFIG_REL)
    old = None
    if os.path.exists(path):
        with open(path, "rb") as fh:
            old = fh.read()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        yield
    finally:
        if old is None:
            os.remove(path)
        else:
            with open(path, "wb") as fh:
                fh.write(old)


def resolve_config(root: str, config_arg: str | None, base_ref: str | None, trusted: bool) -> tuple[dict | None, str | None, str | None]:
    """Load the config, from the base ref when `trusted` (CI / --baseline).
    Returns (cfg, note, overlay_text): overlay_text is the trusted config when it differs from the tree's."""
    if config_arg:
        path = os.path.abspath(config_arg)
        if not os.path.isfile(path):
            return None, f"--config {path} does not exist", None
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".claude"))
            with open(path, "rb") as src, open(os.path.join(tmp, cl.CONFIG_REL), "wb") as dst:
                dst.write(src.read())
            cfg = cl.load_config(tmp)
        cfg["_root"], cfg["_path"] = root, path
        with open(path, encoding="utf-8") as fh:
            return cfg, None, fh.read()
    if trusted:
        ref = base_ref or (f"origin/{os.environ['BASE_REF']}" if os.environ.get("BASE_REF") else None)
        if ref:
            proc = cl.run(["git", "-C", root, "show", f"{ref}:{cl.CONFIG_REL}"])
            if proc.returncode == 0 and proc.stdout.strip():
                tmp = tempfile.mkdtemp(prefix="sdlc-base-config-")
                atexit.register(shutil.rmtree, tmp, True)
                os.makedirs(os.path.join(tmp, ".claude"))
                with open(os.path.join(tmp, cl.CONFIG_REL), "w", encoding="utf-8") as fh:
                    fh.write(proc.stdout)
                cfg = cl.load_config(tmp)
                cfg["_root"] = root
                local = os.path.join(root, cl.CONFIG_REL)
                note = f"config taken from {ref}, not from the PR tree"
                same = False
                if os.path.isfile(local):
                    with open(local, encoding="utf-8") as fh:
                        same = fh.read() == proc.stdout
                    if not same:
                        note += " (the PR changes .claude/sdlc.json; that needs a human review)"
                return cfg, note, None if same else proc.stdout
    cfg = cl.load_config(root)
    return cfg, None, None


def load_baseline(path: str) -> tuple[dict[str, int], int, bool]:
    """Return (counts, version, exists). v1 {"keys": [...]} loads as counts of 1."""
    if not os.path.exists(path):
        return {}, 2, False
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data.get("counts"), dict):
        return {str(k): int(v) for k, v in data["counts"].items()}, int(data.get("version", 2)), True
    return {k: 1 for k in data.get("keys", [])}, 1, True


def write_baseline(path: str, counts: dict[str, int], head: str, stack: str | None) -> None:
    payload = {"version": 2, "head": head, "stack": stack, "counts": dict(sorted(counts.items()))}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
        fh.write("\n")


def describe_changes(old: dict[str, int], new: dict[str, int]) -> list[str]:
    lines = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        if a is None:
            lines.append(f"  + {k}  (new, {b})")
        elif b is None:
            lines.append(f"  - {k}  (dropped, was {a})")
        else:
            lines.append(f"  {'^' if b > a else 'v'} {k}  ({a} -> {b})")
    return lines


def tool_version_notes(cfg: dict, root: str) -> list[str]:
    notes = []
    for tool, pin in sorted((cfg.get("tool_versions") or {}).items()):
        if not pin:
            continue
        got = None
        for cmd in ([sys.executable, "-m", tool, "--version"], [tool, "--version"]):
            try:
                proc = cl.run(cmd, cwd=root)
            except OSError:
                continue
            m = re.search(r"\d+(?:\.\d+)+", proc.stdout or "")
            if proc.returncode == 0 and m:
                got = m.group(0)
                break
        if got is None:
            notes.append(f"tool_versions: {tool} is pinned to {pin} but is not installed; ratchet keys may differ from CI")
        elif got != str(pin):
            notes.append(f"tool_versions: {tool} installed {got}, pinned {pin}; ratchet keys may differ from CI")
    return notes


def main(argv: list[str]) -> int:
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    root = baseline_arg = config_arg = base_ref = None
    files: list[str] = []
    show_all = update = prune = fast = require_tools = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--root", "--baseline", "--config", "--base-ref"):
            if i + 1 >= len(argv):
                print(f"ratchet: {a} needs a value", file=sys.stderr)
                return 2
            if a == "--root":
                root = argv[i + 1]
            elif a == "--config":
                config_arg = argv[i + 1]
            elif a == "--base-ref":
                base_ref = argv[i + 1]
            else:
                baseline_arg = argv[i + 1]
            i += 2
            continue
        if a == "--files":
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                files.append(argv[i].replace(os.sep, "/"))
                i += 1
            continue
        if a == "--all":
            show_all = True
        elif a == "--update":
            update = True
        elif a == "--prune":
            prune = True
        elif a == "--fast":
            fast = True
        elif a == "--require-tools":
            require_tools = True
        else:
            print(f"ratchet: unknown argument {a}", file=sys.stderr)
            return 2
        i += 1
    if update and prune:
        print("ratchet: use --update or --prune, not both", file=sys.stderr)
        return 2
    root = cl.project_root(root)
    in_ci = os.environ.get("CI", "").lower() in ("1", "true")
    trusted = bool(baseline_arg) or in_ci
    cfg, cfg_note, overlay = resolve_config(root, config_arg, base_ref, trusted)
    if cfg is None:
        print(f"ratchet: {cfg_note}", file=sys.stderr)
        return 2
    if cfg_note:
        print(f"note: {cfg_note}", file=sys.stderr)
    if not cfg["_configured"]:
        if trusted:
            print("ratchet: no .claude/sdlc.json (base ref or tree) under --baseline/CI; refusing to pass an unconfigured repo", file=sys.stderr)
            return 2
        print("ratchet: this repo is not initialised (no .claude/sdlc.json); run /sdlc:init first")
        return 0

    with config_overlay(root, overlay):
        findings, notes, errors, skipped = collect(cfg, root, fast)
    notes.extend(tool_version_notes(cfg, root))
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    if errors:
        for err in errors:
            print(f"ratchet: ERROR {err}", file=sys.stderr)
        print("ratchet: a checker failed, so the result cannot be trusted; fix the checker or the file it choked on", file=sys.stderr)
        return 2
    missing = sorted(p for p, why in skipped.items() if why == "missing")
    if missing and (require_tools or in_ci):
        print(f"ratchet: required tool(s) missing ({', '.join(missing)}); install them (CI must not skip checks)", file=sys.stderr)
        return 2

    baseline_path = os.path.abspath(baseline_arg) if baseline_arg else os.path.join(root, cfg.get("ratchet_baseline") or ".claude/baseline.json")
    try:
        known, version, exists = load_baseline(baseline_path)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"ratchet: baseline {baseline_path} unreadable: {exc}", file=sys.stderr)
        return 2
    current = collections.Counter(k for k, _ in findings)
    head = cl.git(root, "rev-parse", "--short", "HEAD").strip()

    if update or prune:
        if prune:
            if not exists:
                print("ratchet --prune: no baseline to prune; a human runs --update first")
                return 2
            if fast or files:
                print("ratchet --prune: needs a full run (not --fast or --files), or it would drop keys that were not checked")
                return 2
            target = {k: min(n, current.get(k, 0)) for k, n in known.items() if current.get(k, 0) > 0}
        else:
            target = dict(current)
        # a tool that did not run says nothing about its keys: keep the old counts
        target.update({k: n for k, n in known.items() if is_skipped(k, skipped)})
        write_baseline(baseline_path, target, head, cfg.get("stack"))
        changes = describe_changes(known if exists else {}, target)
        verb = "pruned" if prune else "written"
        print(f"baseline {verb}: {len(target)} key(s), {sum(target.values())} violation(s) at {head} ({cfg.get('stack')}); was {len(known)} key(s), {sum(known.values())} violation(s)")
        for line in changes[:60]:
            print(line)
        if len(changes) > 60:
            print(f"  +{len(changes) - 60} more")
        if not changes:
            print("  no changes")
        return 0

    stale = []
    if not files:
        stale = sorted(k for k in known if k not in current and not is_skipped(k, skipped))
    if files:
        wanted = {os.path.normpath(f) for f in files}
        findings = [(k, m) for k, m in findings if os.path.normpath(m.split(":", 1)[0]) in wanted]
        current = collections.Counter(k for k, _ in findings)

    by_key: dict[str, list[str]] = collections.defaultdict(list)
    for k, m in findings:
        by_key[k].append(m)
    new_keys = [k for k in by_key if k not in known]
    grown = [(k, known[k], current[k]) for k in by_key if k in known and current[k] > known[k]]

    size = f"{len(known)} key(s), {sum(known.values())} violation(s)"
    if version == 1:
        print("baseline is v1; run --update (humans) to store counts")
    if show_all:
        for k, m in findings:
            tag = "NEW    " if k in new_keys else ("MORE   " if any(g[0] == k for g in grown) else "known  ")
            print(tag + m)
        print(f"ratchet: {len(findings)} violations, {len(new_keys)} new key(s), {len(grown)} grown vs baseline ({size})")
    else:
        for k in new_keys:
            for m in by_key[k]:
                print(m)
        for k, a, b in sorted(grown):
            print(f"{k}: +{b - a} more than baseline ({a} -> {b})")
            for m in by_key[k][:5]:
                print(f"  {m}")
        n_more = sum(b - a for _, a, b in grown)
        print(f"ratchet: {len(new_keys)} new key(s), {len(grown)} grown key(s) (+{n_more} more) vs baseline ({size}); --all lists the known debt")
    if stale:
        print(f"stale: {len(stale)} key(s) no longer fire (run --prune)")
        for k in stale[:10]:
            print(f"  {k}")
        if len(stale) > 10:
            print(f"  +{len(stale) - 10} more")
    return 1 if (new_keys or grown) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
