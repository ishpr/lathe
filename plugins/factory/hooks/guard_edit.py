#!/usr/bin/env python3
"""PreToolUse guard for Edit / Write / MultiEdit. Exit 2 blocks; stderr says why.

1. nothing that looks like a secret is written anywhere;
2. no edits on a protected branch (main/master by default);
3. protected files are edited by humans only (config, baseline, stamp, .env and .env.*, the guards,
   .claude/hooks|rules|agents|skills, .git/config and .git/hooks, CLAUDE.md, .mcp.json, and whatever
   the project lists under paths.protected). The check sees through symlinks (realpath) and
   letter case, so a link or `.CLAUDE/BASELINE.json` does not slip past it;
4. with an active contract (.claude/work/current.json):
   - frozen tests (current.json frozen_tests, written by freeze_tests.py after the /tests commit)
     are not edited, and their entries in current.json are not dropped or changed (REC-07);
   - edits stay inside allowed_paths;
   - while tests are frozen, pytest and coverage configuration is frozen with them: conftest.py
     anywhere, pytest.ini, tox.ini, setup.cfg, .coveragerc, and the [tool.pytest*] /
     [tool.coverage*] tables of pyproject.toml (other pyproject edits pass);
   - a path matching tier_high_paths needs the contract at tier high (REC-54), and an edit of
     current.json itself (once it exists; /ticket creates it) may add keys and frozen tests, narrow
     allowed_paths and turn require_tests on, but not widen allowed_paths, change tier, slug or
     frozen_at, or drop require_tests (REC-54: the tier rule is mechanical only if the model cannot
     rewrite the tier); a proposed current.json that is not a JSON object is denied;
   - an unreadable current.json blocks edits outside .claude/work until it is fixed;
5. a broken .claude/sdlc.json blocks every edit until a human repairs it (silently ignoring it
   would drop the protections it defines).

Fail closed (REC-41): unreadable stdin, a missing tool_input or a crash blocks. Every deny appends
one line to .claude/work/blocks.jsonl (REC-46).
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import sdlc as cl  # noqa: E402

HOOK = "guard_edit"
ALWAYS_ALLOWED = [".claude/work/**"]
# Gitignored staging area for files scripts/workfile.py later moves under .claude/work/: exempt from the
# scope rule only (protected-branch, secrets and frozen-test rules still apply).
STAGE_ALLOWED = [".sdlc-stage/**"]
_CTX: dict = {"root": None, "tool": "Edit", "detail": ""}


def _deny(rule: str, message: str) -> None:
    cl.block(_CTX["root"], HOOK, _CTX["tool"], rule, _CTX["detail"], message)


def main() -> int:
    try:
        return check()
    except SystemExit:
        raise
    except Exception as exc:  # fail closed: a broken guard must not become an open door
        _deny("crash", f"the edit guard crashed ({exc.__class__.__name__}: {exc}). Fix the hook or disable the plugin; nothing is written until then.")
        return 2


def new_text(root: str, path: str, tool: str, ti: dict) -> str | None:
    """The file's content after this edit, or None when it cannot be worked out."""
    if tool == "Write":
        return ti.get("content") or ""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    edits = ti.get("edits") if tool == "MultiEdit" else [ti]
    for e in edits or []:
        old, new = e.get("old_string") or "", e.get("new_string") or ""
        if old not in text:
            return None
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def check_frozen_entries(root: str, path: str, tool: str, ti: dict, frozen: dict[str, str]) -> None:
    """current.json may gain frozen tests, never lose or change one (only freeze_tests.py --unfreeze does)."""
    text = new_text(root, path, tool, ti)
    try:
        after = cl.load_frozen_tests(root, json.loads(text)) if text is not None else None
    except (ValueError, AttributeError):
        after = None
    if after is None or any(after.get(k) != v for k, v in frozen.items()):
        _deny(
            "frozen-test",
            f"this edit drops or changes frozen_tests in current.json ({', '.join(sorted(frozen))}). Frozen tests are "
            "released only by a human with freeze_tests.py --unfreeze; keep those entries as they are.",
        )


TIER_RANK = {"trivial": 0, "low": 1, "medium": 2, "high": 3}
CONTRACT_REL_POSIX = cl.CONTRACT_REL.replace(os.sep, "/")


def check_contract_edit(root: str, path: str, tool: str, ti: dict, contract: dict) -> None:
    """Once current.json exists (/ticket creates it; creating it is not checked here), an edit by the
    session may add keys (reviewer, acceptance, ...) and frozen tests, narrow allowed_paths and turn
    require_tests on. It may not widen allowed_paths, change the tier in either direction (a lower
    tier drops review, a higher one opens the tier-high paths, REC-54), change the slug or frozen_at,
    or turn require_tests off; check_frozen_entries keeps the frozen_tests entries. A proposed text
    that is not a JSON object is denied: the guard cannot tell what it would loosen."""
    text = new_text(root, path, tool, ti)
    try:
        after = json.loads(text) if text is not None else None
    except ValueError:
        after = None
    if not isinstance(after, dict):
        _deny(
            "contract-invalid",
            f"this edit would leave {CONTRACT_REL_POSIX} unreadable (not a JSON object, or the old text was not found), "
            "so the guard cannot check what it changes. Write the whole file as valid JSON, keeping tier, slug, "
            "allowed_paths, require_tests and frozen_tests as they are.",
        )

    def norm(value) -> str:
        return str(value if value is not None else "").strip().lower()

    problems = []
    if norm(after.get("tier")) != norm(contract.get("tier")):
        problems.append(f"tier {contract.get('tier')!r} -> {after.get('tier')!r}")
    if "slug" in contract and after.get("slug") != contract.get("slug"):
        problems.append(f"slug {contract.get('slug')!r} -> {after.get('slug')!r}")
    if contract.get("frozen_at") and after.get("frozen_at") != contract.get("frozen_at"):
        problems.append(f"frozen_at {contract.get('frozen_at')!r} -> {after.get('frozen_at')!r}")
    before_paths = contract.get("allowed_paths") or []
    after_paths = after.get("allowed_paths") or []
    if isinstance(before_paths, list) and before_paths:
        if not isinstance(after_paths, list) or not after_paths or not all(
            isinstance(p, str) and (p in before_paths or any(cl.match_one(p, b) for b in before_paths if isinstance(b, str)))
            for p in after_paths
        ):
            problems.append(f"allowed_paths {before_paths} -> {after_paths}")
    if contract.get("require_tests", True) and not after.get("require_tests", True):
        problems.append("require_tests true -> false")
    if problems:
        _deny(
            "contract-lift",
            "this edit loosens the contract (" + "; ".join(problems) + "). From a session current.json may only gain keys "
            "and frozen tests, narrow allowed_paths or require tests; tier, slug and frozen_at stay as /ticket and /tests "
            "wrote them. Only a human changes those: say what the ticket needs and why, and stop.",
        )


# Files that change what pytest collects, how it runs, or what coverage counts. Once tests are
# frozen they are frozen with them: a conftest.py fixture or an addopts `-k`/`--deselect` would
# make a frozen test pass without the code changing.
TEST_CONFIG_NAMES = ("conftest.py", "pytest.ini", "pytest.toml", ".pytest.toml", "tox.ini", "setup.cfg", ".coveragerc")
_TOOL_TABLE = re.compile(r"""^\s*\[\[?\s*["']?tool["']?\s*\.\s*["']?(?:pytest|coverage)""", re.I)
_TOOL_DOTTED_KEY = re.compile(r"""^\s*["']?tool["']?\s*\.\s*["']?(?:pytest|coverage)""", re.I)


def _toml_test_tables(text: str):
    """(tool.pytest, tool.coverage) of a pyproject.toml, or None when it does not parse."""
    try:
        tool = tomllib.loads(text or "").get("tool") or {}
    except (tomllib.TOMLDecodeError, AttributeError, TypeError, ValueError):
        return None
    if not isinstance(tool, dict):
        return (None, None)
    return (tool.get("pytest"), tool.get("coverage"))


def _text_test_tables(text: str) -> list[str]:
    """The raw lines of the [tool.pytest*] / [tool.coverage*] tables and tool.pytest... dotted keys."""
    out, keep = [], False
    for line in (text or "").splitlines():
        if line.lstrip().startswith("["):
            keep = bool(_TOOL_TABLE.match(line))
        if keep or _TOOL_DOTTED_KEY.match(line):
            out.append(line.strip())
    return out


def touches_test_config(root: str, path: str, rel: str, tool: str, ti: dict) -> bool:
    """True when this edit changes pytest or coverage configuration: any write to conftest.py,
    pytest.ini, tox.ini, setup.cfg or .coveragerc, and a pyproject.toml edit that changes its
    [tool.pytest*] or [tool.coverage*] tables (compared parsed, or as text when a side does not parse)."""
    base = rel.rsplit("/", 1)[-1].lower()
    if base in TEST_CONFIG_NAMES:
        return True
    if base != "pyproject.toml":
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            before = fh.read()
    except OSError:
        before = ""
    after = new_text(root, path, tool, ti)
    if after is None:
        return True  # the result cannot be worked out: fail closed
    b, a = _toml_test_tables(before), _toml_test_tables(after)
    if b is not None and a is not None:
        return b != a
    return _text_test_tables(before) != _text_test_tables(after)


def git_dir_paths(root: str) -> list[str]:
    """Real paths of the repository's own config and hooks when .git is a file (a worktree), where
    the `.git/config` rel path does not name them."""
    if not os.path.isfile(os.path.join(root, ".git")):
        return []
    out = []
    for flag in ("--git-common-dir", "--absolute-git-dir"):
        d = cl.git(root, "rev-parse", flag).strip()
        if d:
            d = cl.real(d if os.path.isabs(d) else os.path.join(root, d))
            out += [os.path.join(d, "config"), os.path.join(d, "hooks")]
    return out


def check() -> int:
    data = cl.read_hook_input()
    ti = data.get("tool_input") if isinstance(data, dict) else None
    tool = str((data or {}).get("tool_name") or "Edit") if isinstance(data, dict) else "Edit"
    _CTX["tool"] = tool
    if not isinstance(ti, dict):
        _deny("input", "hook input unreadable; refusing")
    path = ti.get("file_path") or ""
    if not path:
        return 0
    root = cl.project_root(data.get("cwd"))
    _CTX["root"], _CTX["detail"] = root, path
    cfg = cl.load_config(root)
    if cfg.get("_config_error"):
        _deny("config-invalid", f".claude/sdlc.json is invalid: {cfg['_config_error']}. A human repairs it (the file is protected); until then nothing is edited.")
    if not os.path.isabs(path):
        path = os.path.join(root, path)
    rel = cl.rel_to(root, path)
    inside = not rel.startswith("..")

    if tool == "Write":
        contents = [ti.get("content") or ""]
    elif tool == "MultiEdit":
        contents = [e.get("new_string") or "" for e in ti.get("edits") or []]
    else:
        contents = [ti.get("new_string") or ""]
    for text in contents:
        hit = cl.find_secrets(text, cfg, rel)
        if hit:
            _deny("secret", f"this write contains what looks like a {hit[0]} ({hit[1]}…). Secrets come from the environment, never from a file in the repo.")

    if not inside:
        top = cl.git_toplevel(root)
        if rel.startswith("..") and (cl.real(path).lower() + "/").startswith(top.lower().rstrip("/") + "/"):
            _deny("outside-root", f"{path} is inside the repository ({top}) but outside the project directory {root}; the protections are written relative to the project directory. Edit it from a session started at {top}, or ask a human.")
        return 0
    _CTX["detail"] = rel

    protected_extra = list(cfg["paths"].get("protected") or [])
    work = cl.matches(rel, ALWAYS_ALLOWED, fold=True)
    stage = cl.matches(rel, STAGE_ALLOWED, fold=True)
    real_path = cl.real(path)
    in_git_dir = any(real_path == g or real_path.startswith(g + "/") for g in git_dir_paths(root))
    if cl.is_protected(rel, protected_extra) or in_git_dir:
        _deny("protected-file", f"{rel} is maintained by humans (listed under paths.protected or always protected: config, baseline, stamp, secrets, the guards and agent configuration). Say what you need changed and why, and stop.")

    branch = cl.git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch in cfg["paths"].get("protected_branches", []) and not work:
        _deny("protected-branch", f"you are on {branch}. Create a branch first (git switch -c <type>/<slug>); nothing is edited on {branch}.")

    contract_path = os.path.join(root, cl.CONTRACT_REL)
    if not os.path.exists(contract_path):
        return 0
    contract = cl.load_contract(root)
    if not isinstance(contract, dict):
        if not work:  # .claude/work stays writable so the contract can be repaired
            _deny("contract-unreadable", f"{cl.CONTRACT_REL} exists but is not readable JSON, so scope cannot be checked. Fix it or re-run /factory:ticket.")
        return 0

    frozen = cl.load_frozen_tests(root, contract)
    frozen_lower = {k.lower(): k for k in frozen}
    if rel.lower() in frozen_lower:
        script = os.path.join(cl.plugin_root(), "scripts", "freeze_tests.py")
        _deny("frozen-test", f"{rel} is frozen by /tests; if the test is wrong, stop and ask a human to run `python3 -I {script} --unfreeze {frozen_lower[rel.lower()]}`")
    if frozen and touches_test_config(root, path, rel, tool, ti):
        _deny("test-config", f"{rel}: test configuration is frozen with the tests; ask a human to unfreeze")
    if rel.lower() == CONTRACT_REL_POSIX.lower():
        if frozen:
            check_frozen_entries(root, path, tool, ti, frozen)
        check_contract_edit(root, path, tool, ti, contract)

    if work or stage:
        return 0
    allowed = contract.get("allowed_paths") or []
    if allowed and not cl.matches(rel, allowed):
        _deny(
            "scope",
            f"{rel} is outside the contract's allowed_paths {allowed}. If the ticket needs it, ask a human to widen the "
            f"contract (.claude/work/{contract.get('slug')}/contract.md and current.json) and say why.",
        )
    tier = str(contract.get("tier") or "").lower()
    if tier != "high" and cl.matches(rel, cl.tier_high_paths(cfg), fold=True):
        _deny("tier", f"{rel}: this path forces tier high; re-run /ticket so the contract records it (current.json tier is {contract.get('tier')!r}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
