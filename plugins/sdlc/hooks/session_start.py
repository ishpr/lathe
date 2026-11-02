#!/usr/bin/env python3
"""SessionStart (startup, resume, clear, compact): orientation printed into the session's context.

Never blocks (SessionStart cannot). It does shout: every tool the stack's configured commands need
(python3 -m ruff, mypy, pytest; npx eslint/tsc; go; cargo clippy; docker ...) that is not
installed gets a `MISSING TOOL: <name> — install: <cmd>` line, because a missing linter makes
the post-edit hook and verify skip checks quietly (REC-41).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import sdlc as cl  # noqa: E402


INSTALL_HINTS = {
    "python3": "install Python 3.11+ (brew install python@3.11, or apt-get install python3)",
    "python": "install Python 3.11+ (brew install python@3.11, or apt-get install python3)",
    "node": "install Node.js 20+ (brew install node, or https://nodejs.org)",
    "npm": "install Node.js 20+ (brew install node, or https://nodejs.org)",
    "npx": "install Node.js 20+ (brew install node, or https://nodejs.org)",
    "go": "install Go (brew install go, or https://go.dev/dl)",
    "gofmt": "install Go (brew install go, or https://go.dev/dl)",
    "cargo": "install Rust with rustup (https://rustup.rs)",
    "rustfmt": "rustup component add rustfmt",
    "cargo-clippy": "rustup component add clippy",
    "cargo-fmt": "rustup component add rustfmt",
    "docker": "install Docker Desktop or Docker Engine with the compose plugin (https://docs.docker.com/get-docker/)",
    "make": "xcode-select --install (macOS) or apt-get install make",
}
NPM_PACKAGES = {"tsc": "typescript"}


def needed_tools(command: str) -> list[tuple[str, str]]:
    """(kind, name) pairs a configured command needs: exe, pymod (python -m X), npmbin (npx X), cargo."""
    out: list[tuple[str, str]] = []
    for segment in re.split(r"&&|\|\||;|\|", command):
        try:
            toks = shlex.split(segment)
        except ValueError:
            toks = segment.split()
        while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
            toks = toks[1:]
        if not toks or "{" in toks[0]:
            continue
        exe = os.path.basename(toks[0])
        out.append(("exe", exe))
        if re.fullmatch(r"python[0-9.]*", exe) and "-m" in toks[:-1]:
            out.append(("pymod", toks[toks.index("-m") + 1]))
        if exe == "npx":
            rest = [t for t in toks[1:] if not t.startswith("-")]
            if rest:
                out.append(("npmbin", rest[0]))
        if exe == "cargo" and len(toks) > 1 and toks[1] in ("clippy", "fmt"):
            out.append(("exe", "cargo-" + toks[1]))
    return out


def missing_tools(cfg: dict, root: str) -> list[str]:
    needs: list[tuple[str, str]] = []
    for command in (cfg.get("commands") or {}).values():
        if isinstance(command, str) and command.strip():
            needs += [n for n in needed_tools(command) if n not in needs]
    lines: list[str] = []
    seen: set[str] = set()
    pymods: dict[str, list[str]] = {}
    for kind, name in needs:
        if name in seen:
            continue
        if kind == "exe" and not shutil.which(name):
            seen.add(name)
            lines.append(f"MISSING TOOL: {name} — install: {INSTALL_HINTS.get(name, f'install {name} and put it on PATH')}")
        elif kind == "npmbin" and shutil.which("npx") and not (os.path.exists(os.path.join(root, "node_modules", ".bin", name)) or shutil.which(name)):
            seen.add(name)
            lines.append(f"MISSING TOOL: {name} — install: npm install --save-dev {NPM_PACKAGES.get(name, name)}")
        elif kind == "pymod":
            pymods.setdefault("python3", []).append(name)
    py = shutil.which("python3")
    mods = sorted(set(pymods.get("python3", [])))
    if py and mods:
        probe = "import importlib.util, sys; print(' '.join(m for m in sys.argv[1:] if importlib.util.find_spec(m) is None))"
        try:
            gone = subprocess.run([py, "-I", "-c", probe, *mods], capture_output=True, text=True, timeout=8, cwd=root).stdout.split()
        except (OSError, subprocess.TimeoutExpired):
            gone = []
        for mod in gone:
            if mod not in seen:
                seen.add(mod)
                lines.append(f"MISSING TOOL: {mod} — install: python3 -m pip install {mod}")
    return lines


def main() -> int:
    try:
        return orient()
    except Exception as exc:  # SessionStart never blocks; say what broke instead of a traceback
        if not shutil.which("git"):
            print("MISSING TOOL: git — install: xcode-select --install (macOS) or apt-get install git")
        print(f"session-start: orientation failed ({exc.__class__.__name__}: {exc}).")
        return 0


def orient() -> int:
    data = cl.read_hook_input()
    notes = []
    if data is None:
        notes.append("session-start: hook input unreadable; orienting from CLAUDE_PROJECT_DIR.")
        data = {}
    root = cl.project_root(data.get("cwd"))
    cfg = cl.load_config(root)
    branch = cl.git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() or "?"
    head = cl.git(root, "rev-parse", "--short", "HEAD").strip() or "?"
    dirty = cl.changed_paths(root)

    lines = [f"SDLC: {os.path.basename(root)} on {branch} at {head}, {len(dirty)} uncommitted path(s)."]
    lines += notes
    try:
        lines += missing_tools(cfg, root)
    except Exception as exc:  # orientation must still print
        lines.append(f"session-start: tool check failed ({exc.__class__.__name__}: {exc}).")
    if cfg.get("_config_error"):
        lines.append(
            f"CONFIG INVALID: .claude/sdlc.json {cfg['_config_error']}. Every edit and shell command is blocked until a human "
            "repairs it (the file is protected); a broken config would otherwise switch off the protections it defines."
        )
    if not cfg["_configured"]:
        lines.append(
            "This repo is not initialised for the working agreement: run /sdlc:init "
            "(detects the stack, writes .claude/sdlc.json, rules, CI and today's debt baseline). "
            "Until then the guards run generically and nothing is linted or ratcheted."
        )
    else:
        lines.append(f"Stack: {cfg['stack']}; app paths {cfg['paths']['app']}; tests {cfg['paths']['tests']}.")
    if branch in cfg["paths"].get("protected_branches", []):
        lines.append(f"You are on {branch}: nothing is edited here. `git switch -c <type>/<slug>` first (the edit guard blocks edits on protected branches).")

    contract = cl.load_contract(root)
    if contract:
        lines.append(
            f"Active contract: {contract.get('slug')} (tier {contract.get('tier') or 'not recorded'}) — allowed_paths "
            f"{contract.get('allowed_paths')}; edits outside are blocked. Read .claude/work/{contract.get('slug')}/contract.md before touching code."
        )
        frozen = cl.load_frozen_tests(root, contract)
        if frozen:
            lines.append(f"Frozen tests (edits blocked; a human unfreezes): {', '.join(sorted(frozen))}.")
    else:
        lines.append("No active contract. Start with /sdlc:ticket <ticket>; /sdlc:onboard explains the repo; /sdlc:audit lists the debt.")

    stamp = cl.load_stamp(root)
    if stamp:
        fp = subprocess.run(
            [sys.executable, "-I", os.path.join(cl.plugin_root(), "scripts", "worktree_fingerprint.py"), root],
            capture_output=True, text=True, env=cl.safe_git_env(),
        ).stdout.strip()
        lines.append("Verify stamp: current (commit allowed)." if fp == stamp.get("fingerprint") else "Verify stamp: stale — run /sdlc:verify before committing.")

    lines.append(
        "Flow: ticket → tests → implement → verify → ship (human) → deploy (human). Hooks enforce: branch, scope, "
        "protected files, secrets, the stack's checkers and lint as a ratchet, verify-before-commit, message-vs-diff, "
        "no merges from here."
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
