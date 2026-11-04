#!/usr/bin/env python3
"""Maturity assessment: score a repository on seven dimensions (plan, design, build, test, deploy,
maintain, ai-practice) from evidence in its files and its git history, and write
docs/maturity-assessment.md.

Why this exists: a hand-written AI maturity assessment of a brownfield repository is a
table of evidence lines and a stage; reading 93 commits, 26 merges and a tree by hand does not
scale to the next repository, and a number nobody can reproduce is not a baseline. This collects the
same kind of evidence deterministically (nothing is executed from the project; `--run-tools` is the
one opt-in that runs ruff and mypy), scores it against templates/maturity_rubric.json (data, so a
team can edit the thresholds), and names the move that raises each dimension one level
(the playbook's stages are the five levels of the overall score).

Evidence collected: commits, authors and their share, merge commits and self-merges (the merge's
author is the PR's author, where the subject names a PR), commit-message quality (the logic of
check_commit_message.py, imported, not copied), deletions against additions, CI files and what they
run, lint/type/format config and whether CI enforces it, lock file and pinning, tests and
assertions per test and the weak ones (the test_quality checker), docs against code (the
api_contract and check_claude_md_paths outputs), auth coverage (route_auth), module state, secret
patterns (never printed), and AI practice signals (CLAUDE.md, .claude/, AGENTS.md, Co-Authored-By
trailers and how many authors carry them). Anything it cannot see is listed under "what we did not
measure", never guessed.

Usage: python3 -I scripts/maturity.py [--root DIR] [--rev REV] [--rubric PATH] [--out PATH]
           [--no-write] [--json] [--epic N] [--run-tools] [--max-commits N] [--min-level N]
           [--stack NAME] [--dump-rubric]
Exit: 0 assessed, 1 overall level below --min-level, 2 misuse, a bad rubric or a crash.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import importlib.util
import io
import json
import operator
import os
import re
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction

sys.dont_write_bytecode = True  # importing the pack's checkers must not leave __pycache__ in the pack

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PLUGIN, "lib"))
sys.path.insert(0, HERE)
import sdlc as cl  # noqa: E402
import check_commit_message as ccm  # noqa: E402

DEFAULT_RUBRIC = os.path.join(PLUGIN, "templates", "maturity_rubric.json")
OUT_REL = "docs/maturity-assessment.md"
DIM_ORDER = ["plan", "design", "build", "test", "deploy", "maintain", "ai-practice"]
OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq, "!=": operator.ne}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "target", "dist", "build", "__pycache__", ".mypy_cache",
             ".ruff_cache", ".pytest_cache", "vendor", ".tox", ".next", ".idea", ".gradle"}
CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".java", ".rb", ".cs", ".kt", ".swift")
SKIP_SECRET_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".ttf", ".lock",
                    ".svg", ".min.js", ".map", ".pyc", ".sqlite", ".db", ".mp4", ".mov")
MAX_FILE = 1_000_000
DERIVED = {"auth.routes", "auth.unprotected_share", "test.weak_share", "ai.claude_md_missing_paths"}  # said once, under their parent metric
PERCENT_KEYS = ("diff.del_add_ratio", "test.ratio")
PR_RES = [re.compile(r"(?i)\b(?:pull request|merge request|pr)\s*#?(\d+)"), re.compile(r"\(#(\d+)\)"),
          re.compile(r"(?i)merge request[^\n]*?!(\d+)")]
AI_TRAILER_RE = re.compile(
    r"(?im)^\s*Co-Authored-By:.*\b(?:Claude|Copilot|Cursor|Codex|Gemini|Aider|Devin|ChatGPT|OpenAI)\b"
    r"|Generated with \[?Claude Code|^\s*Assisted-by:"
)


# ---------------------------------------------------------------------------------------------
# pack-specific: the plugin reads .claude/sdlc.json and the stack files; a standalone copy hard-codes its paths
# ---------------------------------------------------------------------------------------------

def detect_stack(root: str) -> str:
    """A manifest names the stack; with none, the commonest source extension does."""
    has = lambda *names: any(os.path.exists(os.path.join(root, n)) for n in names)  # noqa: E731
    if has("pyproject.toml", "requirements.txt", "setup.py", "Pipfile"):
        return "python"
    if has("package.json"):
        return "node"
    if has("go.mod"):
        return "go"
    if has("Cargo.toml"):
        return "rust"
    counts: dict[str, int] = {}
    seen = 0
    for _dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fns:
            seen += 1
            ext = os.path.splitext(f)[1]
            if ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"):
                counts[ext] = counts.get(ext, 0) + 1
        if seen > 5000:
            break
    stacks = {"python": counts.get(".py", 0), "node": sum(counts.get(e, 0) for e in (".ts", ".tsx", ".js", ".jsx")),
              "go": counts.get(".go", 0), "rust": counts.get(".rs", 0)}
    best = max(stacks, key=stacks.get)
    return best if stacks[best] else "generic"


class Pack:
    """Config for one assessment: the stack, its path globs, how a checker is run, how secrets are found."""

    def __init__(self, root: str, stack_arg: str | None = None):
        self.root = root
        self.notes: list[str] = []
        self.tmp: str | None = None
        cfg = cl.load_config(root)
        self.configured = bool(cfg.get("_configured")) and not cfg.get("_config_error")
        if cfg.get("_config_error"):
            self.notes.append(f".claude/sdlc.json is unusable ({cfg['_config_error']}); assessed with the stack defaults")
        stack = stack_arg or (cfg.get("stack") if self.configured and cfg.get("stack") != "generic" else None) or detect_stack(root)
        if not self.configured or stack != cfg.get("stack"):
            base = cl.deep_merge(cl.DEFAULTS, cl.stack_defaults(stack) if stack in cl.STACKS else {})
            cfg = cl.deep_merge(base, {"stack": stack})
            if stack == "python":
                cfg["paths"]["app"] = self.guess_python_app(root, cfg["paths"]["app"])
            cfg["_path"] = self.write_temp_config(cfg)
        self.cfg = cfg
        self.stack = stack
        self.extensions = cfg.get("extensions") or list(CODE_EXTS)
        self.ticket_re = re.compile(cfg.get("ticket_id_pattern") or r"[A-Z]{2,}-\d+|#\d+")

    @staticmethod
    def guess_python_app(root: str, current: list[str]) -> list[str]:
        if any(os.path.isdir(os.path.join(root, d)) for d in ("app", "src")):
            return current
        pkgs = [d for d in sorted(os.listdir(root)) if os.path.isfile(os.path.join(root, d, "__init__.py"))
                and d not in ("tests", "test", "docs", "scripts")]
        return [f"{p}/**" for p in pkgs] or current

    def write_temp_config(self, cfg: dict) -> str:
        self.tmp = tempfile.mkdtemp(prefix="maturity-")
        path = os.path.join(self.tmp, "sdlc.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({k: v for k, v in cfg.items() if not k.startswith("_")}, fh)
        return path

    def cleanup(self) -> None:
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def is_test(self, rel: str) -> bool:
        return cl.is_test_path(self.cfg, rel)

    def checker(self, name: str) -> tuple[list[tuple[str, str]] | None, str]:
        """(findings, note). findings is None when the checker is absent, crashed, or had nothing to look at.

        The checkers read their paths from .claude/sdlc.json under --root, and some ignore --config. A repo
        that has not been set up has no such file and must not get one from an assessment, so the checker module
        is loaded here and sdlc.load_config is answered with the effective config for this root.
        """
        script = os.path.join(HERE, "checkers", self.stack, f"{name}.py")
        if not os.path.exists(script):
            return None, f"no {name} checker for the {self.stack} stack"
        buf_o, buf_e = io.StringIO(), io.StringIO()
        saved = cl.load_config
        root = os.path.abspath(self.root)
        cl.load_config = lambda r, _cfg=self.cfg: dict(_cfg) if os.path.abspath(r) == root else saved(r)
        try:
            spec = importlib.util.spec_from_file_location(f"_maturity_{self.stack}_{name}", script)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            with contextlib.redirect_stdout(buf_o), contextlib.redirect_stderr(buf_e):
                rc = mod.main(["--root", root, "--config", self.cfg["_path"]])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
        except Exception as exc:  # noqa: BLE001 - a crashing checker must not read as "clean"
            return None, f"{name} crashed ({type(exc).__name__})"
        finally:
            cl.load_config = saved
        return classify(name, rc, buf_o.getvalue(), buf_e.getvalue())

    def checker_names(self) -> list[str]:
        """Every checker the pack has for this stack, whether or not the project's config lists it: an assessment is read-only evidence, not the ratchet."""
        d = os.path.join(HERE, "checkers", self.stack)
        return sorted(f[:-3] for f in os.listdir(d) if f.endswith(".py")) if os.path.isdir(d) else []

    def claude_md_paths(self) -> tuple[list[str] | None, str]:
        return run_claude_md_paths(os.path.join(HERE, "check_claude_md_paths.py"), self.root)

    def secret(self, text: str, rel: str) -> str | None:
        hit = cl.find_secrets_scan(text, self.cfg, rel)
        return hit[0] if hit else None

    def lint_command(self) -> list[str] | None:
        return ruff_command(self.stack)


# ---------------------------------------------------------------------------------------------
# shared plumbing
# ---------------------------------------------------------------------------------------------

def classify(name: str, rc: int, stdout: str, stderr: str) -> tuple[list[tuple[str, str]] | None, str]:
    """A checker prints 'key<TAB>message' lines and exits 0 or 1. Anything else, or a clean run that had nothing to look at, is not a result."""
    if rc > 1 or "Traceback" in stderr or (rc == 1 and not stdout.strip()):
        return None, f"{name} crashed (exit {rc})"
    if any(m in stderr for m in ("no files to scan", "nothing to compare", "no entry module")) and not stdout.strip():
        return None, f"{name} found nothing to look at (no matching source files or routes)"
    out = []
    for line in stdout.splitlines():
        if "\t" in line:
            key, msg = line.split("\t", 1)
            out.append((key, msg))
    return out, ""


def run_claude_md_paths(script: str, root: str) -> tuple[list[str] | None, str]:
    if not os.path.exists(script):
        return None, "check_claude_md_paths.py not found"
    proc = subprocess.run([sys.executable, "-I", script, "--root", root], capture_output=True, text=True, cwd=root, timeout=120)
    if proc.returncode > 1:
        return None, f"check_claude_md_paths crashed (exit {proc.returncode})"
    if "no CLAUDE.md or .claude/rules" in (proc.stdout + proc.stderr):
        return None, "no CLAUDE.md or .claude/rules to check"
    return [ln for ln in proc.stdout.splitlines() if "does not exist" in ln], ""


def ruff_command(stack: str) -> list[str] | None:
    if stack != "python":
        return None
    return [sys.executable, "-m", "ruff", "check", "--no-cache", "--output-format", "json", "--exit-zero", "."]


def git_env() -> dict[str, str]:
    return cl.safe_git_env()


def git(root: str, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, env=git_env(), timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


# ---------------------------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------------------------

class Metrics:
    def __init__(self) -> None:
        self.m: dict[str, dict] = {}
        self.not_measured: list[tuple[str, str]] = []
        self.extra_nm: list[str] = []

    def put(self, key: str, value, line: str | None = None, note: str | None = None) -> None:
        self.m[key] = {"value": value, "line": line, "note": note}
        if value is None:
            self.not_measured.append((key, note or "not measured"))

    def val(self, key: str):
        return self.m.get(key, {}).get("value")


def fmt(key: str, v) -> str:
    if v is None:
        return "not measured"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if key.endswith("_share") or key in PERCENT_KEYS:
            return f"{round(v * 100)}%"
        return f"{v:.1f}"
    return str(v)


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


class Repo:
    """Tracked (and unignored) files, cached reads, and a few path predicates."""

    def __init__(self, root: str, is_git: bool):
        self.root = root
        out = git(root, "ls-files", "-co", "--exclude-standard", "-z") if is_git else None
        if out is not None:
            files = [f for f in out.split("\0") if f]
        else:
            files = []
            for dp, dns, fns in os.walk(root):
                dns[:] = [d for d in dns if d not in SKIP_DIRS]
                files += [os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/") for f in fns]
        venvs: dict[str, bool] = {}

        def in_venv(rel: str) -> bool:  # a directory with pyvenv.cfg is a virtualenv, whatever it is called
            d = rel.rpartition("/")[0]
            if not d:
                return False
            if d not in venvs:
                venvs[d] = cl.is_venv_dir(os.path.join(root, d)) or in_venv(d)
            return venvs[d]

        self.files = sorted(f for f in set(files) if not (set(f.split("/")[:-1]) & SKIP_DIRS)
                            and not in_venv(f) and os.path.isfile(os.path.join(root, f)))
        self.set = set(self.files)
        self._cache: dict[str, str] = {}

    def read(self, rel: str) -> str:
        if rel not in self._cache:
            try:
                full = os.path.join(self.root, rel)
                if os.path.getsize(full) > MAX_FILE:
                    self._cache[rel] = ""
                else:
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        self._cache[rel] = fh.read()
            except OSError:
                self._cache[rel] = ""
        return self._cache[rel]

    def has(self, *names: str) -> bool:
        return any(n in self.set for n in names)

    def find(self, regex: str, flags: int = re.I) -> list[str]:
        rx = re.compile(regex, flags)
        return [f for f in self.files if rx.search(f)]

    def first(self, *names: str) -> str | None:
        return next((n for n in names if n in self.set), None)


# ---- git history -----------------------------------------------------------------------------

def collect_history(mx: Metrics, root: str, pack: Pack, rev: str, cap: int) -> None:
    keys_na = ["git.commits", "git.merges", "git.authors", "git.top_author_share", "git.span_days", "git.pr_merges",
               "git.self_merge_share", "msg.pass_share", "msg.contradicting", "diff.additions", "diff.deletions",
               "diff.del_add_ratio", "plan.pr_numbers", "plan.ticket_ref_share", "ai.trailer_commits", "ai.trailer_authors",
               "ai.trailer_share", "ai.ever_shared_config", "deploy.tags"]

    def none_all(why: str) -> None:
        for k in keys_na:
            mx.put(k, None, note=why)
        mx.put("git.available", False, "no git history to read")

    head = git(root, "rev-parse", "--verify", f"{rev}^{{commit}}")
    if head is None:
        none_all(f"{rev} is not a commit in this directory (not a git repository, or no commits yet)")
        return
    head = head.strip()
    mx.put("git.available", True)
    mx.put("git.head", head[:7])
    cap = max(1, cap)
    fmt_log = "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%P%x1f%B"
    raw = git(root, "log", rev, f"--max-count={cap}", f"--format={fmt_log}") or ""
    commits, merges = [], []
    for rec in raw.split("\x1e"):
        if not rec.strip():
            continue
        parts = rec.split("\x1f", 5)
        if len(parts) < 6:
            continue
        sha, an, ae, date, parents, msg = parts
        c = {"sha": sha.strip(), "name": an, "email": ae.strip().lower(), "date": date.strip(), "parents": parents.split(), "msg": msg}
        (merges if len(c["parents"]) > 1 else commits).append(c)
    mx.put("git.head_date", (commits or merges or [{"date": ""}])[0]["date"][:10])
    capped = len(commits) + len(merges) >= cap
    scope = f"the last {cap} commits" if capped else "all history"
    mx.put("git.scope", scope)

    # ---- authors and size
    n = len(commits)
    by_author: dict[str, int] = {}
    names: dict[str, str] = {}
    for c in commits:
        by_author[c["email"]] = by_author.get(c["email"], 0) + 1
        names[c["email"]] = c["name"]
    top = max(by_author.values()) if by_author else 0
    top_email = max(by_author, key=by_author.get) if by_author else ""
    mx.put("git.commits", n, f"{plural(n, 'non-merge commit')} by {plural(len(by_author), 'author')} ({scope})")
    mx.put("git.merges", len(merges))
    mx.put("git.authors", len(by_author))
    mx.put("git.top_author_share", (top / n) if n else None, f"{names.get(top_email, '?')} wrote {pct(top / n)} of the commits" if n else None,
           None if n else "no commits")
    dates = [c["date"] for c in commits + merges if c["date"]]
    span = None
    if dates:
        import datetime as _dt
        try:
            lo, hi = min(dates), max(dates)
            span = (_dt.datetime.fromisoformat(hi) - _dt.datetime.fromisoformat(lo)).days
        except ValueError:
            span = None
    mx.put("git.span_days", span)

    # ---- merges and self-merges
    pr_merges = self_merges = 0
    for c in merges[:300]:
        subj = c["msg"].strip().splitlines()[0] if c["msg"].strip() else ""
        if not any(rx.search(subj) for rx in PR_RES):
            continue
        p1, p2 = c["parents"][0], c["parents"][1]
        authors = (git(root, "log", f"{p1}..{p2}", "--no-merges", "--format=%ae") or "").split()
        if not authors:
            tip = (git(root, "log", "-1", "--format=%ae", p2) or "").strip()
            authors = [tip] if tip else []
        if not authors:
            continue
        counts: dict[str, int] = {}
        for a in authors:
            counts[a.lower()] = counts.get(a.lower(), 0) + 1
        best = max(counts.values())
        tip_author = (git(root, "log", "-1", "--format=%ae", p2) or "").strip().lower()
        pr_author = tip_author if counts.get(tip_author) == best else sorted(k for k, v in counts.items() if v == best)[0]
        pr_merges += 1
        if pr_author == c["email"]:
            self_merges += 1
    mx.put("git.pr_merges", pr_merges)
    if pr_merges:
        mx.put("git.self_merge_share", self_merges / pr_merges,
               f"{self_merges} of {pr_merges} pull-request merges are self-merges (the merger is the author); a second reviewer is not visible in git")
    else:
        mx.put("git.self_merge_share", None, note="no merge commit names a pull request (squash or rebase merges hide who merged)")
    mx.m["git.self_merges"] = {"value": self_merges, "line": None, "note": None}

    # ---- message quality, with the pack's own rules
    name_only = git(root, "log", rev, "--no-merges", f"--max-count={cap}", "--name-only", "--format=%x1e%H") or ""
    files_of: dict[str, list[str]] = {}
    for rec in name_only.split("\x1e"):
        lines = [ln for ln in rec.splitlines() if ln.strip()]
        if lines:
            files_of[lines[0].strip()] = lines[1:]
    failing = contradicting = 0
    for c in commits:
        problems = ccm.check(c["msg"], files_of.get(c["sha"], []))
        if problems:
            failing += 1
            if any(k.startswith("commit|claims-") for k, _ in problems):
                contradicting += 1
    if n:
        mx.put("msg.checked", n)
        mx.put("msg.failing", failing)
        mx.put("msg.pass_share", 1 - failing / n,
               f"{failing} of {n} commit messages fail the pack's message check (vague subject, body missing, or claims the diff does not show)")
        mx.put("msg.contradicting", contradicting, f"{contradicting} of {n} commit messages contradict their diffs (name a file or tests the commit does not touch)")
    else:
        for k in ("msg.pass_share", "msg.contradicting"):
            mx.put(k, None, note="no non-merge commits")

    # ---- additions and deletions
    numstat = git(root, "log", rev, "--no-merges", f"--max-count={cap}", "--numstat", "--format=") or ""
    adds = dels = 0
    for ln in numstat.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            adds += int(parts[0])
            dels += int(parts[1])
    mx.put("diff.additions", adds)
    mx.put("diff.deletions", dels)
    if adds:
        mx.put("diff.del_add_ratio", dels / adds, f"+{adds:,}/-{dels:,} lines: deletions are {dels / adds * 100:.1f}% of additions, so little is ever removed or consolidated"
               if dels / adds < 0.1 else f"+{adds:,}/-{dels:,} lines: deletions are {dels / adds * 100:.0f}% of additions")
    else:
        mx.put("diff.del_add_ratio", None, note="no added lines")

    # ---- traceability to the ask
    pr_nums: set[str] = set()
    for c in commits + merges:
        subj = c["msg"].strip().splitlines()[0] if c["msg"].strip() else ""
        for rx in PR_RES:
            for m in rx.finditer(subj):
                pr_nums.add(m.group(1))
    mx.put("plan.pr_numbers", len(pr_nums), f"{plural(len(pr_nums), 'distinct pull request')} named in commit subjects")
    if n:
        cited = sum(1 for c in commits if pack.ticket_re.search(c["msg"]))
        mx.put("plan.ticket_ref_share", cited / n, f"{cited} of {n} commits cite a ticket or PR id")
    else:
        mx.put("plan.ticket_ref_share", None, note="no non-merge commits")

    # ---- AI trailers
    tr = [c for c in commits if AI_TRAILER_RE.search(c["msg"])]
    tr_authors = sorted({c["email"] for c in tr})
    mx.put("ai.trailer_commits", len(tr))
    mx.put("ai.trailer_authors", len(tr_authors))
    mx.put("ai.trailer_share", (len(tr) / n) if n else None)
    if not tr:
        mx.m["ai.trailer_commits"]["line"] = f"0 of {n} non-merge commits carry an AI trailer (trailers can be turned off, so this is a floor)"
    else:
        who = names.get(tr_authors[0], "?") if len(tr_authors) == 1 else f"{len(tr_authors)} of {len(by_author)} authors"
        mx.m["ai.trailer_commits"]["line"] = (f"{len(tr)} of {n} non-merge commits carry an AI trailer, "
                                              + (f"all from {who}" if len(tr_authors) == 1 else f"from {who}") + " (a floor: trailers can be turned off)")

    # ---- ever had agent config or CI in any ref
    ever = git(root, "log", "--all", "--oneline", "-n", "1", "--", "CLAUDE.md", ":(glob)**/CLAUDE.md", "AGENTS.md", ":(glob)**/AGENTS.md", ".claude")
    ever_ci = git(root, "log", "--all", "--oneline", "-n", "1", "--", ".github/workflows", ".gitlab-ci.yml", "Jenkinsfile",
                  "azure-pipelines.yml", ".circleci", "bitbucket-pipelines.yml")
    mx.put("ai.ever_shared_config", None if ever is None else bool(ever.strip()), None, "git log over all refs failed")
    mx.put("ci.ever", None if ever_ci is None else bool(ever_ci.strip()))
    tags = git(root, "tag", "--list")
    mx.put("deploy.tags", None if tags is None else len([t for t in tags.splitlines() if t.strip()]),
           None if tags is None else f"{plural(len([t for t in tags.splitlines() if t.strip()]), 'release tag')}")


# ---- files ------------------------------------------------------------------------------------

CI_FILES = [(r"^\.github/workflows/[^/]+\.ya?ml$", "GitHub Actions"), (r"^\.gitlab-ci\.ya?ml$", "GitLab CI"),
            (r"^Jenkinsfile$", "Jenkins"), (r"^azure-pipelines\.ya?ml$", "Azure Pipelines"),
            (r"^\.circleci/config\.ya?ml$", "CircleCI"), (r"^bitbucket-pipelines\.ya?ml$", "Bitbucket Pipelines"),
            (r"^\.drone\.ya?ml$", "Drone"), (r"^\.travis\.ya?ml$", "Travis")]
CI_PATTERNS = {
    "tests": r"\b(pytest|py\.test|unittest|npm (?:run )?test|yarn test|pnpm (?:run )?test|jest|vitest|mocha|go test|cargo test|nextest|tox|nox|rspec|mvn\b[^\n]*\btest|gradle\w* test|dotnet test|make test|make check)\b",
    "lint": r"\b(ruff(?! format)|flake8|pylint|eslint|golangci-lint|clippy|rubocop|biome (?:check|lint)|make lint|pre-commit run|lint)\b",
    "types": r"\b(mypy|pyright|pytype|tsc|cargo check|cargo build|go vet|go build|make typecheck|type-?check)\b",
    "format": r"\b(ruff format|black|prettier|gofmt|cargo fmt|isort|make format|format --check|fmt --check|pre-commit run)\b",
    "security": r"\b(bandit|pip-audit|safety|npm audit|yarn audit|trivy|grype|snyk|codeql|gitleaks|trufflehog|semgrep|osv-scanner|cargo audit|govulncheck|dependency-review)\b",
    "deploy": r"\b(deploy|kubectl|helm|terraform apply|cdk deploy|sam deploy|serverless|flyctl|vercel|netlify|aws-actions/amazon-ecs|docker push|gcloud run|az webapp|heroku|argocd|release-please|twine upload|npm publish|pypi-publish)\b",
    "contract": r"(openapi|schemathesis|\bpact\b|dredd|api_contract|check_api_contract|swagger|oasdiff|graphql-inspector)",
    "baseline": r"(ratchet|baseline_diff|baseline-diff|baseline\.json)",
}
LINT_CONFIGS = [(r"^\.?ruff\.toml$", "ruff.toml"), (r"^\.flake8$", ".flake8"), (r"^\.pylintrc$", ".pylintrc"),
                (r"^\.eslintrc(\.\w+)?$", ".eslintrc"), (r"^eslint\.config\.\w+$", "eslint.config"), (r"^\.golangci\.ya?ml$", ".golangci.yml"),
                (r"^\.?clippy\.toml$", "clippy.toml"), (r"^biome\.jsonc?$", "biome.json")]
TYPE_CONFIGS = [(r"^\.?mypy\.ini$", "mypy.ini"), (r"^pyrightconfig\.json$", "pyrightconfig.json"), (r"^tsconfig(\.\w+)?\.json$", "tsconfig.json")]
FORMAT_CONFIGS = [(r"^\.prettierrc(\.\w+)?$", ".prettierrc"), (r"^prettier\.config\.\w+$", "prettier.config"), (r"^\.editorconfig$", ".editorconfig"),
                  (r"^\.?rustfmt\.toml$", "rustfmt.toml")]
LOCKFILES = ["poetry.lock", "uv.lock", "Pipfile.lock", "pdm.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
             "go.sum", "Cargo.lock", "Gemfile.lock", "composer.lock"]
MANIFESTS = ["pyproject.toml", "Pipfile", "setup.py", "package.json", "go.mod", "Cargo.toml", "Gemfile", "pom.xml", "build.gradle",
             "build.gradle.kts", "composer.json"]


def top_level(repo: Repo, regex: str) -> str | None:
    rx = re.compile(regex)
    return next((f for f in repo.files if "/" not in f and rx.search(f)), None)


def collect_ci_and_tools(mx: Metrics, repo: Repo, pack: Pack) -> str:
    ci_files, systems = [], []
    for rx, name in CI_FILES:
        hit = repo.find(rx, 0)
        if hit:
            ci_files += hit
            systems.append(name)
    text = "\n".join(repo.read(f) for f in ci_files)
    if re.search(r"\bmake\s+\w", text) and "Makefile" in repo.set:
        text += "\n" + repo.read("Makefile")
    low = text.lower()
    present = bool(ci_files)
    mx.put("ci.present", present, ("CI: " + ", ".join(systems) + f" ({plural(len(ci_files), 'file')})") if present else "No CI config in the tree (.github/workflows, .gitlab-ci.yml, Jenkinsfile, ...)")
    mx.put("ci.systems", ", ".join(systems) if systems else "none")
    if present:
        if "GitHub Actions" in systems:
            on_pr = bool(re.search(r"\bpull_request(_target)?\b", text))
        else:
            on_pr = True  # GitLab, Jenkins, Azure and CircleCI run on every push by default
        mx.put("ci.on_pull_request", on_pr, None if on_pr else "CI exists but no workflow triggers on pull_request")
    else:
        mx.put("ci.on_pull_request", False)
    for key, label in (("tests", "the tests"), ("lint", "lint"), ("types", "a type check"), ("format", "a format check"),
                       ("security", "a security or dependency scan"), ("deploy", "a deploy or release step"),
                       ("contract", "an API contract check"), ("baseline", "a debt baseline check")):
        found = bool(present and re.search(CI_PATTERNS[key], low if key not in ("deploy",) else low))
        none_line = "No CI, so nothing runs the tests independently" if key == "tests" else None
        mx.put(f"ci.runs_{key}", found, (f"CI runs {label}" if found else (f"CI does not run {label}" if present else none_line)))
    # config for the three standards, and whether CI enforces each
    pyproject = repo.read("pyproject.toml")
    setup_cfg = repo.read("setup.cfg") + repo.read("tox.ini")
    lint = next((n for rx, n in LINT_CONFIGS if top_level(repo, rx)), None)
    if not lint and re.search(r"\[tool\.ruff", pyproject):
        lint = "pyproject [tool.ruff]"
    if not lint and re.search(r"\[flake8\]", setup_cfg):
        lint = "setup.cfg [flake8]"
    if not lint and '"eslintConfig"' in repo.read("package.json"):
        lint = "package.json eslintConfig"
    typ = next((n for rx, n in TYPE_CONFIGS if top_level(repo, rx)), None)
    if not typ and re.search(r"\[tool\.(mypy|pyright)", pyproject):
        typ = "pyproject [tool.mypy]"
    if not typ and re.search(r"\[mypy", setup_cfg):
        typ = "setup.cfg [mypy]"
    if not typ and pack.stack in ("go", "rust"):
        typ = "compiler"
    fmt_cfg = next((n for rx, n in FORMAT_CONFIGS if top_level(repo, rx)), None)
    if not fmt_cfg and re.search(r"\[tool\.(black|isort)\]", pyproject):
        fmt_cfg = "pyproject [tool.black]"
    ruff_toml = repo.read("ruff.toml") + repo.read(".ruff.toml")
    if not fmt_cfg and (re.search(r"(?m)^\[format\]", ruff_toml) or re.search(r"\[tool\.ruff\.format\]", pyproject)):
        fmt_cfg = "ruff [format]"
    if not fmt_cfg and pack.stack == "go":
        fmt_cfg = "gofmt"
    mx.put("tool.lint_config", lint)
    mx.put("tool.type_config", typ)
    mx.put("tool.format_config", fmt_cfg)
    cfg_count = sum(1 for x in (lint, typ, fmt_cfg) if x)
    names = ", ".join(f"{k} ({v})" for k, v in (("lint", lint), ("types", typ), ("format", fmt_cfg)) if v) or "none"
    mx.put("tool.config_count", cfg_count, f"{cfg_count} of 3 standards have config in the repo: {names}")
    pre = repo.first(".pre-commit-config.yaml", "lefthook.yml", ".husky/pre-commit")
    mx.put("tool.precommit", bool(pre), None)
    enforced = []
    for label, cfgname, key in (("lint", lint, "ci.runs_lint"), ("types", typ, "ci.runs_types"), ("format", fmt_cfg, "ci.runs_format")):
        if cfgname and mx.val(key):
            enforced.append(label)
    unenforced = [x for x in ("lint", "types", "format") if x not in enforced]
    line = f"CI enforces {len(enforced)} of 3 standards" + (f" ({', '.join(enforced)})" if enforced else "")
    if cfg_count and len(enforced) < cfg_count:
        configured_only = [x for x, c in (("lint", lint), ("types", typ), ("format", fmt_cfg)) if c and x not in enforced]
        line += f"; {', '.join(configured_only)} {'is' if len(configured_only) == 1 else 'are'} configured but nothing in CI runs {'it' if len(configured_only) == 1 else 'them'}"
        if pre:
            line += f" ({pre} is opt-in per developer)"
    mx.put("tool.enforced_count", len(enforced), line)
    _ = unenforced
    return text


def manifest_dirs(pack: Pack) -> list[str]:
    """Directories whose manifests describe the app: the root, plus each configured app path's literal directory
    and the directories above it (paths.app `plugins/factory/scripts/**` finds plugins/factory/pyproject.toml)."""
    dirs = {""}
    for glob in (pack.cfg or {}).get("paths", {}).get("app", []):
        parts = []
        for seg in glob.strip("./").split("/"):
            if any(c in seg for c in "*?["):
                break
            parts.append(seg)
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return sorted(dirs)


def collect_deps(mx: Metrics, repo: Repo, pack: Pack) -> None:
    dirs = manifest_dirs(pack)
    at = lambda d, n: f"{d}/{n}" if d else n  # noqa: E731
    manifests = [at(d, m) for d in dirs for m in MANIFESTS if at(d, m) in repo.set] + repo.find(r"(^|/)requirements[\w.-]*\.txt$")
    manifests = [m for m in manifests if not m.endswith("pyproject.toml") or re.search(r"(?m)^dependencies\s*=", repo.read(m))]
    has_manifest = bool(manifests)
    lock = next((at(d, lf) for d in dirs for lf in LOCKFILES if at(d, lf) in repo.set), None) or next(iter(repo.find(r"(^|/)requirements[\w.-]*\.(lock|in)$")), None)
    mx.put("deps.manifest", has_manifest, ("Dependencies declared in " + ", ".join(sorted(set(manifests))[:4])) if has_manifest else "No dependency manifest found")
    mx.put("deps.lockfile", bool(lock), f"Lock file: {lock}" if lock else None)
    pinned = total = 0
    for m in manifests:
        text = repo.read(m)
        if re.search(r"requirements[\w.-]*\.txt$", m):
            for ln in text.splitlines():
                s = ln.split("#", 1)[0].strip()
                if not s or s.startswith(("-", "git+", "http")):
                    continue
                total += 1
                pinned += 1 if re.search(r"==|@ ", s) else 0
        elif m.endswith("package.json"):
            try:
                data = json.loads(text)
            except ValueError:
                continue
            for sect in ("dependencies", "devDependencies"):
                for ver in (data.get(sect) or {}).values():
                    total += 1
                    pinned += 1 if re.match(r"^\d", str(ver)) else 0
        elif m.endswith("pyproject.toml"):
            block = re.search(r"(?ms)^dependencies\s*=\s*\[(.*?)\]", text)
            for item in re.findall(r'"([^"]+)"', block.group(1) if block else ""):
                total += 1
                pinned += 1 if "==" in item else 0
    if total:
        mx.put("deps.pinned_share", pinned / total, f"{pinned} of {total} declared dependencies are pinned to an exact version" + ("" if lock else " and there is no lock file"))
    else:
        mx.put("deps.pinned_share", None, note="no parsable dependency list")
    auto = repo.first(".github/dependabot.yml", ".github/dependabot.yaml", "renovate.json", ".renovaterc", ".renovaterc.json", ".github/renovate.json5", "renovate.json5")
    mx.put("deps.autoupdate", bool(auto), f"Automated dependency updates: {auto}" if auto else "No Dependabot or Renovate config")


def count_tests(rel: str, src: str) -> tuple[int, int]:
    """(tests, assertions) for one test file."""
    if rel.endswith(".py"):
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError, RecursionError):
            return 0, 0
        tests = asserts = 0

        def visit(fn):
            nonlocal tests, asserts
            tests += 1
            for n in ast.walk(fn):
                if isinstance(n, ast.Assert):
                    asserts += 1
                elif isinstance(n, (ast.With, ast.AsyncWith)):
                    for it in n.items:
                        ce = it.context_expr
                        f = getattr(ce, "func", None)
                        nm = getattr(f, "attr", None) or getattr(f, "id", None)
                        if isinstance(ce, ast.Call) and nm in ("raises", "warns"):
                            asserts += 1
                elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr.startswith("assert"):
                    asserts += 1
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                visit(node)
            elif isinstance(node, ast.ClassDef):
                for it in node.body:
                    if isinstance(it, (ast.FunctionDef, ast.AsyncFunctionDef)) and it.name.startswith("test"):
                        visit(it)
        return tests, asserts
    if rel.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs")):
        return (len(re.findall(r"(?m)^\s*(?:it|test)(?:\.(?:only|skip))?\s*\(", src)),
                len(re.findall(r"\bexpect\s*\(|\bassert(?:\.\w+)?\s*\(", src)))
    if rel.endswith(".go"):
        return (len(re.findall(r"(?m)^func\s+Test\w*\s*\(", src)),
                len(re.findall(r"\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\b|\b(?:assert|require)\.\w+\(", src)))
    if rel.endswith(".rs"):
        return len(re.findall(r"#\[(?:\w+::)?test\]", src)), len(re.findall(r"\bassert(?:_eq|_ne)?!", src))
    return 0, 0


def is_test_file(pack: Pack, rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1]
    if rel.endswith(".py"):
        return (base.startswith("test_") or base.endswith("_test.py")) and base != "conftest.py"
    if rel.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs")):
        return ".test." in base or ".spec." in base or "/__tests__/" in "/" + rel
    if rel.endswith(".go"):
        return base.endswith("_test.go")
    if rel.endswith(".rs"):
        return rel.startswith("tests/") or "/tests/" in rel
    return False


def loc(src: str) -> int:
    return sum(1 for ln in src.splitlines() if ln.strip())


def collect_tests(mx: Metrics, repo: Repo, pack: Pack, ci_text: str, chk: dict) -> None:
    tfiles = [f for f in repo.files if is_test_file(pack, f)]
    tests = asserts = 0
    for f in tfiles:
        t, a = count_tests(f, repo.read(f))
        tests += t
        asserts += a
    if any(f.endswith(".rs") for f in repo.files):  # inline #[test] modules in src
        for f in repo.files:
            if f.endswith(".rs") and f not in tfiles:
                t, a = count_tests(f, repo.read(f))
                tests += t
                asserts += a
    mx.put("test.files", len(tfiles))
    mx.put("test.count", tests, f"{plural(tests, 'test')} in {plural(len(tfiles), 'file')}" if tests else "No automated tests found")
    mx.put("test.asserts", asserts)
    if tests:
        mx.put("test.density", asserts / tests, f"{asserts / tests:.1f} assertions per test ({asserts} across {tests})")
    else:
        mx.put("test.density", None, note="no tests")
    findings, note = chk.get("test_quality", (None, "test_quality checker not run"))
    if findings is not None and tests:
        weak = {(k.split("|")[1], k.split("|")[2]) for k, _ in findings if k.count("|") >= 3 and k.rsplit("|", 1)[1] in ("no-assert", "existence-assert", "constant-assert")}
        noneg = {k.split("|")[1] for k, _ in findings if k.endswith("|no-negative-case")}
        stack_tests = [f for f in tfiles if any(f.endswith(e) for e in pack.extensions)] or tfiles
        mx.put("test.weak", len(weak), f"{plural(len(weak), 'test')} assert nothing real (no assert, existence-only or constant); {len(noneg)} of {len(stack_tests)} test files have no negative case")
        mx.put("test.weak_share", len(weak) / tests, None)
        mx.put("test.files_no_negative_share", (len(noneg) / len(stack_tests)) if stack_tests else None, None, None if stack_tests else "no test files")
    else:
        why = note or "no tests to check"
        for k in ("test.weak", "test.weak_share", "test.files_no_negative_share"):
            mx.put(k, None, note=why)
    src_loc = test_loc = 0
    for f in repo.files:
        if f.endswith(CODE_EXTS):
            if is_test_file(pack, f) or pack.is_test(f):
                test_loc += loc(repo.read(f))
            else:
                src_loc += loc(repo.read(f))
    if src_loc:
        mx.put("test.ratio", test_loc / src_loc, f"Test code is {test_loc / src_loc * 100:.0f}% of source code by lines ({test_loc:,} against {src_loc:,})")
    else:
        mx.put("test.ratio", None, note="no source files")
    cfg_text = ci_text + "\n" + "\n".join(repo.read(f) for f in ("pyproject.toml", "setup.cfg", ".coveragerc", "package.json", "Makefile", "codecov.yml", ".codecov.yml", "jest.config.js", "jest.config.ts", "tox.ini") if f in repo.set)
    gate = bool(re.search(r"--cov-fail-under|fail_under|coverageThreshold|--fail-under|fail-under|coverage_threshold|target:\s*\d+%", cfg_text))
    mx.put("test.coverage_gate", gate, "A coverage floor is configured" if gate else "No coverage floor configured")
    mx.put("test.in_ci", bool(mx.val("ci.runs_tests")), None)
    dep_text = "\n".join(repo.read(m) for m in repo.files if re.search(r"(^|/)(requirements[\w.-]*\.txt|pyproject\.toml|package\.json|Cargo\.toml|go\.mod)$", m))
    adv = bool(re.search(r"mutmut|cosmic-ray|stryker|hypothesis|fast-check|proptest|quickcheck|schemathesis|go-fuzz", dep_text, re.I)) \
        or any(re.search(r"(^|/)tests/QUARANTINE\.md$|freeze_tests", f) for f in repo.files)
    mx.put("test.advanced", adv)


def collect_docs_and_build(mx: Metrics, repo: Repo, pack: Pack, chk: dict, ci_text: str) -> None:
    readme = top_level(repo, r"^README(\.\w+)?$")
    rlines = sum(1 for ln in repo.read(readme).splitlines() if ln.strip()) if readme else 0
    mx.put("docs.readme_lines", rlines, f"README is {rlines} lines" if readme else "No README")
    arch = repo.first("ARCHITECTURE.md", "DESIGN.md", "docs/ARCHITECTURE.md", "docs/architecture.md", "docs/design.md") or next(iter(repo.find(r"^docs/(architecture|design|overview)[^/]*\.md$")), None)
    mx.put("docs.architecture", bool(arch), f"Architecture note: {arch}" if arch else "No architecture or design note")
    adrs = repo.find(r"(^|/)(adr|adrs|decisions)/[^/]+\.md$")
    adrs = [a for a in adrs if not a.lower().endswith("readme.md")]
    mx.put("docs.adr_count", len(adrs), f"{plural(len(adrs), 'decision record')} (ADR)" if adrs else "No decision records (ADRs)")
    mx.put("docs.contributing", bool(repo.has("CONTRIBUTING.md", ".github/CONTRIBUTING.md")))
    mx.put("docs.changelog", bool(top_level(repo, r"^CHANGELOG(\.\w+)?$")))
    runbook = repo.find(r"(^|/)(runbook|runbooks|rollback|oncall|on-call)[^/]*(\.md)?$|(^|/)docs/[^/]*(runbook|rollback)[^/]*\.md$")
    mx.put("docs.runbook", bool(runbook), f"Runbook or rollback doc: {runbook[0]}" if runbook else "No runbook or rollback doc")
    inc = repo.find(r"(postmortem|post-mortem|incident)[^/]*\.md$|(^|/)(postmortems|incidents)/")
    mx.put("maintain.incident_record", bool(inc), f"Incident record: {inc[0]}" if inc else "No incident or postmortem records")
    findings, note = chk.get("api_contract", (None, "api_contract checker not run"))
    if findings is not None:
        n = sum(1 for k, _ in findings if k.startswith("contract|"))
        mx.put("docs.contract_lies", n, f"{plural(n, 'endpoint')} the UI or README names {'does' if n == 1 else 'do'} not exist on the server" if n else "Every endpoint the UI or README names exists on the server")
    else:
        mx.put("docs.contract_lies", None, note=note)
    cm, note = pack.claude_md_paths()
    if cm is not None:
        mx.put("docs.claude_md_missing_paths", len(cm), f"{plural(len(cm), 'path')} named in CLAUDE.md or .claude/rules do not exist" if cm else "Every path CLAUDE.md names exists")
        mx.put("ai.claude_md_missing_paths", len(cm))
    else:
        mx.put("docs.claude_md_missing_paths", None, note=note)
        mx.put("ai.claude_md_missing_paths", None, note=note)
    fitness = bool(repo.find(r"(^|/)\.importlinter$|(^|/)\.dependency-cruiser\.\w+$|test_architecture")) or bool(re.search(r"importlinter|import-linter|depcruise|archunit", repo.read("pyproject.toml") + repo.read("package.json") + repo.read("setup.cfg"), re.I))
    mx.put("design.fitness", fitness)
    # build gates
    co = repo.first("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
    mx.put("build.codeowners", bool(co), f"CODEOWNERS present ({co})" if co else "No CODEOWNERS file")
    prt = repo.first(".github/pull_request_template.md", "pull_request_template.md", "docs/pull_request_template.md") or next(iter(repo.find(r"^\.github/PULL_REQUEST_TEMPLATE")), None)
    mx.put("build.pr_template", bool(prt))
    issue_t = bool(repo.find(r"^\.github/ISSUE_TEMPLATE/"))
    mx.put("plan.templates", bool(prt) or issue_t, "PR or issue template present" if (prt or issue_t) else "No PR or issue template")
    intent = repo.find(r"(^|/)(epics?|specs?|rfcs?|intents?|plans?|prds?)/[^/]+\.md$|(^|/)(intent|spec|plan|prd|contract)\.md$")
    mx.put("plan.intent_files", len(intent), f"{plural(len(intent), 'written intent, spec or epic file')} committed" if intent else "No written intent, spec or epic files in the repo")
    mx.put("build.review_md", bool(repo.has("REVIEW.md")))
    mx.put("build.baseline", bool(repo.has(".claude/baseline.json")))
    metrics_file = repo.find(r"(^|/)(metrics|cost)[^/]*\.(json|md|csv)$|metrics_baseline")
    mx.put("plan.metrics_file", bool(metrics_file), f"Metrics or cost baseline: {metrics_file[0]}" if metrics_file else "No metrics or cost baseline committed")
    # auth, state, debt
    routes = 0
    for f in repo.files:
        if f.endswith(".py") and not pack.is_test(f) and pack.cfg and any(cl.match_one(f, g) for g in pack.cfg["paths"].get("app", [])):
            routes += len(re.findall(r"@\w+\.(?:get|post|put|patch|delete)\(", repo.read(f)))
    findings, note = chk.get("route_auth", (None, "route_auth checker not run"))
    if findings is not None and routes:
        un = sum(1 for k, _ in findings if k.startswith("route-auth|") and not k.endswith("|syntax"))
        mx.put("auth.routes", routes)
        mx.put("auth.unprotected", un, f"{un} of about {routes} routes have no auth dependency and no allowlist entry" if un else f"All {routes} or so routes carry an auth dependency or an allowlist entry")
        mx.put("auth.unprotected_share", un / routes)
    else:
        for k in ("auth.routes", "auth.unprotected", "auth.unprotected_share"):
            mx.put(k, None, note=note if findings is None else "no routes found")
    findings, note = chk.get("module_state", (None, "module_state checker not run"))
    if findings is not None:
        n = sum(1 for k, _ in findings if k.startswith("state|") and not k.endswith("|syntax"))
        mx.put("state.module_level", n, f"{plural(n, 'module-level mutable object')} on request paths (state that does not survive a second process)" if n else "No module-level mutable state on request paths")
    else:
        mx.put("state.module_level", None, note=note)
    findings, note = chk.get("dead_code", (None, "dead_code checker not run"))
    mx.put("debt.dead", None if findings is None else sum(1 for k, _ in findings if k.startswith("dead|")),
           None if findings is None else f"{sum(1 for k, _ in findings if k.startswith('dead|'))} unmounted routers or unreachable modules", note if findings is None else None)
    findings, note = chk.get("stubs", (None, "stubs checker not run"))
    mx.put("debt.stubs", None if findings is None else sum(1 for k, _ in findings if k.startswith("stubs|")),
           None if findings is None else f"{sum(1 for k, _ in findings if k.startswith('stubs|'))} TODOs without a ticket id, stub bodies or env reads outside config", note if findings is None else None)
    # deploy
    cont = repo.find(r"(^|/)(Dockerfile[\w.-]*|docker-compose[\w.-]*\.ya?ml|compose[\w.-]*\.ya?ml)$|^\.devcontainer/")
    mx.put("deploy.container", bool(cont), f"Container setup: {cont[0]}" if cont else "No Dockerfile, compose file or devcontainer")
    mx.put("deploy.makefile", bool(repo.has("Makefile", "justfile", "Taskfile.yml")))
    mx.put("deploy.iac", bool(repo.find(r"\.tf$|(^|/)(cdk\.json|Pulumi\.ya?ml|serverless\.ya?ml|skaffold\.ya?ml)$|^(helm|k8s|kubernetes|charts)/")))
    docs_text = "\n".join(repo.read(f) for f in repo.files if f.lower().endswith(".md") and (f.startswith("docs/") or "/" not in f))
    roll = bool(repo.find(r"rollback")) or bool(re.search(r"\brollback\b", docs_text + ci_text + repo.read("Makefile"), re.I))
    mx.put("deploy.rollback", roll, "A rollback step is written down or scripted" if roll else "No rollback step written down or scripted")
    smoke = bool(repo.find(r"(smoke|canary)")) or bool(re.search(r"smoke|canary|health-?check", ci_text, re.I))
    mx.put("deploy.smoke", smoke)
    rel_wf = bool(repo.find(r"^\.github/workflows/[^/]*(release|deploy|publish|cd)[^/]*\.ya?ml$")) or bool(re.search(r"(?m)^\s*(release|tags):", ci_text))
    mx.put("deploy.release_workflow", rel_wf)


def scan_secrets(mx: Metrics, repo: Repo, pack: Pack) -> None:
    hits: list[tuple[str, str]] = []
    scanned = 0
    for f in repo.files:
        if f.lower().endswith(SKIP_SECRET_EXTS) or scanned >= 5000:
            continue
        scanned += 1
        label = pack.secret(repo.read(f), f)
        if label:
            hits.append((f, label))
    env_tracked = [f for f in repo.files if f.rsplit("/", 1)[-1].startswith(".env") and not f.lower().endswith(cl.ENV_TEMPLATE_SUFFIXES)]
    mx.put("secrets.hits", len(hits), (f"{plural(len(hits), 'secret-like string')} in tracked files ("
                                       + "; ".join(f"{lbl} in {fl}" for fl, lbl in hits[:3]) + ("; ..." if len(hits) > 3 else "") + ")")
           if hits else f"No secret-like strings in {scanned} tracked files (values are never printed)")
    mx.put("secrets.env_tracked", len(env_tracked), f"{plural(len(env_tracked), 'tracked .env file')}: {', '.join(env_tracked[:3])}" if env_tracked else None)


def collect_ai(mx: Metrics, repo: Repo) -> None:
    claude_md = repo.find(r"(^|/)CLAUDE\.md$")
    dotclaude = [f for f in repo.files if f.startswith(".claude/") and not f.startswith(".claude/work/") and f != ".claude/settings.local.json"]
    agents_md = repo.find(r"(^|/)AGENTS\.md$")
    other = repo.find(r"^\.cursor/rules/|^\.cursorrules$|^\.github/copilot-instructions\.md$|^\.windsurfrules$|^GEMINI\.md$|^\.aider|^\.continue/")
    shared = bool(claude_md or dotclaude or agents_md)
    mx.put("ai.claude_md", bool(claude_md))
    mx.put("ai.dotclaude", bool(dotclaude))
    mx.put("ai.agents_md", bool(agents_md))
    mx.put("ai.other_assistant_files", bool(other), f"Other assistant rules committed: {other[0]}" if other else None)
    parts = [n for n, ok in (("CLAUDE.md", claude_md), (".claude/", dotclaude), ("AGENTS.md", agents_md)) if ok]
    mx.put("ai.shared_config", shared, f"Shared agent config committed: {', '.join(parts)}" if shared else "No CLAUDE.md, AGENTS.md or .claude/ in the tree")
    if mx.val("ai.ever_shared_config") is False and not shared:
        mx.m["ai.shared_config"]["line"] = "No CLAUDE.md, AGENTS.md or .claude/ in the tree or in any ref, ever; each session starts from zero"
    elif mx.val("ai.ever_shared_config") is True and not shared:
        mx.m["ai.shared_config"]["line"] = "No CLAUDE.md, AGENTS.md or .claude/ in the tree now, though one existed in history"
    settings = {}
    if ".claude/settings.json" in repo.set:
        try:
            settings = json.loads(repo.read(".claude/settings.json"))
            if not isinstance(settings, dict):
                settings = {}
        except ValueError:
            settings = {}
    via = None
    if settings.get("hooks"):
        via = ".claude/settings.json"
    elif any(f.startswith(".claude/hooks/") for f in repo.files):
        via = ".claude/hooks/"
    elif re.search(r"\b(?:factory|sdlc)@", json.dumps(settings.get("enabledPlugins", {}))) or ".claude/sdlc.json" in repo.set:
        via = "the factory plugin"
    mx.put("ai.hooks", bool(via), f"Hooks configured via {via}" if via else "No hooks configured in the repo, so rules are advisory (nothing refuses a bad edit)")
    deny = ((settings.get("permissions") or {}).get("deny")) if isinstance(settings.get("permissions"), dict) else None
    mx.put("ai.permissions_deny", bool(deny), None)
    mx.put("ai.rules", len([f for f in repo.files if re.match(r"^\.claude/rules/[^/]+\.md$", f)]))
    mx.put("ai.skills", len([f for f in repo.files if re.match(r"^\.claude/skills/[^/]+/SKILL\.md$", f)]))
    mx.put("ai.agents", len([f for f in repo.files if re.match(r"^\.claude/agents/[^/]+\.md$", f)]))
    cfg_tests = bool(repo.find(r"(^|/)tests/hooks/|(^|/)evals?/|(^|/)test_(hooks|guards?)\.py$"))
    mx.put("ai.config_tests", cfg_tests)
    gi = repo.read(".gitignore")
    overlay = re.search(r"(?im)^.*\b(CLAUDE(?:\.local)?\.md|\.worktrees/?)\b.*$", gi)
    if overlay and not claude_md:
        mx.extra_nm.append(f"Gitignored agent overlays: .gitignore mentions `{overlay.group(1)}`; if per-person CLAUDE.md files exist on disk they were not read, and they are what to harvest first.")
    if mx.val("ai.shared_config"):
        mx.m["ai.rules"]["line"] = f"{mx.val('ai.rules')} rules, {mx.val('ai.skills')} skills, {mx.val('ai.agents')} agents committed under .claude/"


def run_tools(mx: Metrics, root: str, pack: Pack) -> None:
    cmd = pack.lint_command()
    if not cmd:
        mx.put("tool.lint_debt", None, note="--run-tools supports ruff on Python only")
        return
    # The ruff binary on PATH first. The fallback is the interpreter's ruff run isolated (-I) from a scratch
    # directory with the repository as an argument, so a ruff/ package or ruff.py inside the repository is never
    # imported. ruff reads the repository's ruff config (data, not code); that is the one thing --run-tools reads.
    args = list(cmd[3:])
    # The project's own ruff config is passed explicitly (--config / --isolated) so a user-level config on this
    # machine cannot leak in; uvx is the last resort when no ruff is installed.
    conf = [os.path.join(root, a) if a.endswith(".toml") else a for a in cl.ruff_config_args(root)]
    args = [args[0], *conf, *args[1:]]
    candidates = ([(["ruff", *args], root)] if shutil.which("ruff") else [])
    candidates.append(([sys.executable, "-I", "-m", "ruff", *args[:-1], root], None))
    if shutil.which("uvx"):
        candidates.append((["uvx", "ruff", *args[:-1], root], None))
    for c, cwd in candidates:
        try:
            with tempfile.TemporaryDirectory() as scratch:
                p = subprocess.run(c, capture_output=True, text=True, cwd=cwd or scratch, timeout=300)
            if "No module named ruff" in p.stderr or (p.returncode not in (0, 1) and not p.stdout.strip()):
                continue
            n = len(json.loads(p.stdout or "[]"))
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        mx.put("tool.lint_debt", n, f"ruff reports {n} findings with the project's own config")
        return
    mx.put("tool.lint_debt", None, note="ruff is not installed (no `ruff` on PATH, no `python3 -m ruff`, no `uvx`)")


# ---------------------------------------------------------------------------------------------
# rubric and scoring
# ---------------------------------------------------------------------------------------------

def rubric_source(root: str, given: str | None) -> tuple[str | None, str]:
    """(path to read, label for the report). The plugin ships its rubric under templates/."""
    if given:
        shown = os.path.relpath(given, root) if os.path.abspath(given).startswith(root + os.sep) else given
        return given, f"`{shown}`"
    return DEFAULT_RUBRIC, "`templates/maturity_rubric.json` in the plugin"


def rubric_text(path: str | None) -> str:
    with open(path or DEFAULT_RUBRIC, encoding="utf-8") as fh:
        return fh.read()


def load_rubric(path: str | None) -> dict:
    try:
        data = json.loads(rubric_text(path))
    except (OSError, ValueError) as exc:
        raise ValueError(f"rubric {path or 'default'} cannot be read: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("dimensions"), dict) or not data["dimensions"]:
        raise ValueError(f"rubric {path}: needs a 'dimensions' object")

    def check_cond(c, where):
        if not isinstance(c, dict):
            raise ValueError(f"rubric {where}: a condition must be an object")
        for grp in ("any", "all"):
            if grp in c:
                if not isinstance(c[grp], list) or not c[grp]:
                    raise ValueError(f"rubric {where}: '{grp}' must be a non-empty list")
                for s in c[grp]:
                    check_cond(s, where)
                return
        if c.get("op") not in OPS or not isinstance(c.get("metric"), str) or "value" not in c:
            raise ValueError(f"rubric {where}: a condition needs metric, op ({' '.join(OPS)}) and value: {c}")

    for dkey, dim in data["dimensions"].items():
        levels = (dim or {}).get("levels")
        if not isinstance(levels, dict) or sorted(levels) != ["1", "2", "3", "4", "5"]:
            raise ValueError(f"rubric dimension {dkey}: 'levels' must have the keys 1 to 5")
        for n, lv in levels.items():
            if not isinstance(lv.get("definition"), str) or not isinstance(lv.get("requires", []), list):
                raise ValueError(f"rubric {dkey} level {n}: needs a definition and a 'requires' list")
            for c in lv.get("requires", []):
                check_cond(c, f"{dkey} level {n}")
            if n != "1" and not lv.get("move"):
                raise ValueError(f"rubric {dkey} level {n}: needs a 'move' (the step that reaches it)")
    ov = data.get("overall") or {}
    if ov.get("method", "mean_floor") != "mean_floor":
        raise ValueError("rubric overall.method: only mean_floor is supported")
    return data


def eval_cond(c: dict, mx: Metrics) -> tuple[bool, list[str], list[str]]:
    """(met, unmet descriptions, metric keys involved in unmet leaves)."""
    if "any" in c or "all" in c:
        group = c.get("any") or c.get("all")
        results = [eval_cond(s, mx) for s in group]
        if "any" in c:
            if any(r[0] for r in results):
                return True, [], []
            return False, ["one of: " + " | ".join("; ".join(r[1]) for r in results)], [k for r in results for k in r[2]]
        unmet = [u for r in results for u in r[1]]
        keys = [k for r in results for k in r[2]]
        return (not unmet), unmet, keys
    key = c["metric"]
    v = mx.val(key)
    label = c.get("label") or f"{key} {c['op']} {c['value']}"
    now = fmt(key, v)
    if v is None:
        ok = c.get("if_missing") == "pass"
    else:
        try:
            ok = bool(OPS[c["op"]](v, c["value"]))
        except TypeError:
            ok = False
    return ok, ([] if ok else [f"{label} (now: {now})"]), ([] if ok else [key])


def score(rubric: dict, mx: Metrics) -> list[dict]:
    out = []
    keys = [k for k in DIM_ORDER if k in rubric["dimensions"]] + [k for k in rubric["dimensions"] if k not in DIM_ORDER]
    for dkey in keys:
        dim = rubric["dimensions"][dkey]
        lvls = dim["levels"]
        level, blockers, blocker_keys = 1, [], []
        for n in range(2, 6):
            res = [eval_cond(c, mx) for c in lvls[str(n)].get("requires", [])]
            if all(r[0] for r in res):
                level = n
            else:
                blockers = [u for r in res for u in r[1]]
                blocker_keys = [k for r in res for k in r[2]]
                break
        nxt = lvls.get(str(level + 1))
        ev_keys = list(dict.fromkeys(blocker_keys + (dim.get("evidence") or [])))
        lines: list[str] = []

        def add(k: str, lines: list[str] = lines) -> None:
            rec = mx.m.get(k)
            if rec is None:
                return
            text = rec["line"] or (f"{k}: not measured ({rec['note'] or 'no data'})" if rec["value"] is None else None)
            if text and text not in lines:
                lines.append(text)
        for k in dict.fromkeys(blocker_keys):  # what stops the next level, at most two lines of it
            if len(lines) < 2:
                add(k)
        for k in list(dim.get("evidence") or []) + blocker_keys:  # then the rubric's own order, so the report is never only a list of blockers
            if len(lines) < 3:
                add(k)
        extra = []
        for k in ev_keys:
            rec = mx.m.get(k)
            if rec and rec["line"] and rec["line"] not in lines:
                extra.append(rec["line"])
        d = {"key": dkey, "title": dim.get("title", dkey), "level": level, "definition": lvls[str(level)]["definition"],
             "evidence": lines, "more_evidence": extra[:3], "next": None}
        if nxt:
            d["next"] = {"level": level + 1, "definition": nxt["definition"], "move": nxt["move"], "unmet": blockers, "blocker_keys": list(dict.fromkeys(blocker_keys)),
                         "effort": int(nxt.get("effort") or 2), "impact": int(nxt.get("impact") or 2),
                         "epic_outcome": nxt.get("epic_outcome") or nxt["definition"], "epic_metrics": nxt.get("epic_metrics") or []}
        out.append(d)
    return out


def overall(rubric: dict, dims: list[dict]) -> dict:
    ov = rubric.get("overall") or {}
    levels = [d["level"] for d in dims]
    mean = sum(levels) / len(levels)
    lvl = int(mean)  # floor
    cap = min(levels) + int(ov.get("cap_above_lowest", 2))
    capped = lvl > cap
    lvl = min(lvl, cap, 5)
    stages = ov.get("stages") or {}
    return {"level": lvl, "label": stages.get(str(lvl), ""), "mean": round(mean, 2), "capped": capped, "lowest": min(levels)}


def top_moves(dims: list[dict]) -> list[dict]:
    """Rank each dimension's next step by impact over effort. A step that clears the same unmet conditions as one
    already chosen is the same work, so it is folded into that one ("also raises") and the slot goes to the next."""
    cands = []
    for i, d in enumerate(dims):
        if d["next"]:
            n = d["next"]
            cands.append((-Fraction(n["impact"], n["effort"]), -len(n["unmet"]), d["level"], i, d))
    cands.sort(key=lambda t: t[:4])
    moves: list[dict] = []
    for _, _, _, _, d in cands:
        n = d["next"]
        keys = set(n["blocker_keys"])
        twin = next((m for m in moves if keys and keys & m["_keys"]), None)
        if twin:
            twin["also_raises"].append({"title": d["title"], "from": d["level"], "to": n["level"], "metrics": n["epic_metrics"], "evidence": d["evidence"]})
            continue
        if len(moves) >= 3:
            continue
        moves.append({"rank": len(moves) + 1, "dimension": d["key"], "title": d["title"], "from": d["level"], "to": n["level"], "move": n["move"],
                      "effort": n["effort"], "impact": n["impact"], "unmet": n["unmet"], "evidence": d["evidence"] + d["more_evidence"],
                      "also_raises": [], "epic": {"outcome": n["epic_outcome"], "metrics": n["epic_metrics"]}, "_keys": keys})
    for m in moves:
        m.pop("_keys")
    return moves


WORDS = {1: "low", 2: "medium", 3: "high"}
# How a person re-runs this assessment. The plugin has no scripts/ in a target repository: its skill is the command.
RERUN_CMD = "`/factory:maturity`"


def fill(text: str, mx: Metrics) -> str:
    return re.sub(r"\{([a-z][\w.\-]*)\}", lambda m: fmt(m.group(1), mx.val(m.group(1))), text)


_TARGET = re.compile(r"\{([a-z][\w.\-]*)\}\s*->\s*(?:(?P<yes>yes)$|(?P<num>\d+(?:\.\d+)?)(?P<pct>%)?(?P<dir>\s+or\s+(?:more|fewer))?$)")


def metric_met(text: str, mx: Metrics) -> bool:
    """True when a success metric's target already holds at baseline (`x -> 1.5 or more` with x at 2.4), so it is not listed as a goal."""
    m = _TARGET.search(text.strip())
    if not m:
        return False
    v = mx.val(m.group(1))
    if v is None:
        return False
    if m.group("yes"):
        return v is True
    if isinstance(v, bool):
        return False
    target = float(m.group("num")) / (100 if m.group("pct") else 1)
    direction = (m.group("dir") or "").strip()
    if direction.endswith("more"):
        return v >= target
    if direction.endswith("fewer"):
        return v <= target
    return v == target


def epic_prompt(move: dict, dims: list[dict], mx: Metrics, head: str, stack: str) -> str:
    d = next(x for x in dims if x["key"] == move["dimension"])
    lines = ["Write an epic (outcome, hypothesis, the measure that says it worked, then features) for this move on the maturity assessment.", "",
             f"Outcome: {move['epic']['outcome']}", "",
             f"Problem statement (measured from the repository at {head}, {stack} stack):"]
    ev = list(move["evidence"])
    for a in move["also_raises"]:
        ev += [e for e in a["evidence"] if e not in ev]
    lines += [f"- {e}" for e in (ev or ["(no evidence lines)"])[:6]]
    lines += ["", f"Where it stands: {d['title']} is at level {d['level']} of 5 ({d['definition']})",
              f"Target: level {move['to']} ({d['next']['definition']})", "",
              f"The move: {move['move']}"]
    if move["unmet"]:
        lines += ["", "Not yet true (each one is a candidate feature):"] + [f"- {u}" for u in move["unmet"]]
    if move["also_raises"]:
        lines += ["", "The same work also raises: " + "; ".join(f"{a['title']} ({a['from']} to {a['to']})" for a in move["also_raises"]) + "."]
    metrics = list(move["epic"]["metrics"])
    for a in move["also_raises"]:
        metrics += [m for m in a["metrics"] if m.split(":")[0] not in {x.split(":")[0] for x in metrics}]
    open_metrics = [m for m in metrics if not metric_met(m, mx)]  # a target the repository already meets is not a goal
    lines += ["", "Success metrics (baseline -> target):"] + [f"- {fill(m, mx)}" for m in open_metrics]
    if len(open_metrics) < len(metrics):
        lines += [f"({len(metrics) - len(open_metrics)} metric(s) already meet their target at baseline and are left out.)"]
    lines += ["", f"Effort {WORDS[move['effort']]}, impact {WORDS[move['impact']]} (rubric estimate). Re-run {RERUN_CMD} after the work: the {d['title'].lower()} level should read {move['to']}."]
    return "\n".join(lines)


def cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render_md(res: dict) -> str:
    ov = res["overall"]
    out = ["# Maturity assessment", "",
           f"Assessed `{res['head'] or 'working tree'}`" + (f" (commit of {res['head_date']})" if res.get("head_date") else "") + f", {res['stack']} stack, history: {res['scope']}.",
           f"Overall: **level {ov['level']} of 5, {ov['label']}** (mean {ov['mean']:.2f} across {len(res['dimensions'])} dimensions"
           + (f"; capped, because the lowest dimension is {ov['lowest']}" if ov["capped"] else "") + ").", "",
           "| Dimension | Level | Evidence | The move that raises it one level |", "| --- | --- | --- | --- |"]
    for d in res["dimensions"]:
        ev = "<br>".join(cell(e) for e in d["evidence"]) or "no evidence lines"
        if d["next"]:
            n = d["next"]
            mv = f"**To {n['level']}:** {cell(n['move'])}"
            if n["unmet"]:
                mv += "<br>*Not yet true:* " + cell("; ".join(n["unmet"]))
        else:
            mv = "At the top level; hold it by re-running this assessment each quarter."
        out.append(f"| **{d['title']}** | **{d['level']}** of 5<br>*{cell(d['definition'])}* | {ev} | {mv} |")
    out += ["", "## Three moves first", "",
            "Ranked by impact over effort (each 1 low to 3 high, from the rubric); ties go to the step that clears more unmet conditions, then the lower level, then the order of the table. Steps that clear the same conditions are one piece of work and are listed together.", ""]
    for m in res["top_moves"]:
        also = "".join(f" The same work also raises {a['title']} from {a['from']} to {a['to']}." for a in m["also_raises"])
        out.append(f"{m['rank']}. **{m['title']}, {m['from']} to {m['to']}** (effort {WORDS[m['effort']]}, impact {WORDS[m['impact']]}). {m['move']}{also}")
    if not res["top_moves"]:
        out.append("Every dimension is at level 5.")
    out += ["", "## What we did not measure", ""]
    out += [f"- {x}" for x in res["not_measured"]]
    out += ["", f"Scored against {res['rubric']} (edit the thresholds there; `--dump-rubric` prints a copy to start from). Re-run: {RERUN_CMD} (the script's `--json` gives the data, `--epic 1` the epic prompt of the first move).", ""]
    return "\n".join(out)


def assess(root: str, args) -> dict:
    pack = Pack(root, args.stack)
    try:
        rubric_path, rubric_label = rubric_source(root, args.rubric)
        rubric = load_rubric(rubric_path)
        mx = Metrics()
        is_git = git(root, "rev-parse", "--git-dir") is not None
        collect_history(mx, root, pack, args.rev, args.max_commits)
        repo = Repo(root, is_git)
        chk = {n: pack.checker(n) for n in pack.checker_names()}
        ci_text = collect_ci_and_tools(mx, repo, pack)
        collect_deps(mx, repo, pack)
        collect_tests(mx, repo, pack, ci_text, chk)
        collect_docs_and_build(mx, repo, pack, chk, ci_text)
        scan_secrets(mx, repo, pack)
        collect_ai(mx, repo)
        extra_nm: list[str] = []
        if args.run_tools:
            run_tools(mx, root, pack)
        else:
            extra_nm = ["Lint and type error counts: not run (pass --run-tools to run ruff on a Python repo; mypy is never run, it can execute project plugins)."]
        dims = score(rubric, mx)
        ov = overall(rubric, dims)
        moves = top_moves(dims)
        known = set(mx.m)
        referenced = set()

        def walk(c):
            if "metric" in c:
                referenced.add(c["metric"])
            for s in c.get("any", []) + c.get("all", []):
                walk(s)
        for dim in rubric["dimensions"].values():
            for lv in dim["levels"].values():
                for c in lv.get("requires", []):
                    walk(c)
        unknown = sorted(referenced - known)
        needed = referenced | {k for dim in rubric["dimensions"].values() for k in (dim.get("evidence") or [])}
        groups: dict[str, list[str]] = {}
        for key, note in dict.fromkeys(mx.not_measured):
            if key in needed and key not in DERIVED:
                groups.setdefault(note, []).append(key)
        nm = list(rubric.get("not_measured") or []) + extra_nm
        nm += [f"{', '.join(keys)}: {note}" for note, keys in groups.items()]
        nm += mx.extra_nm
        nm += [f"the rubric names a metric this script does not collect: {k}" for k in unknown]
        nm += pack.notes
        if not is_git or mx.val("git.available") is False:
            nm.insert(0, "Git history: none readable here, so commits, merges, authors, message quality and trailers are not measured.")
        res = {"version": 1, "root": root, "head": mx.val("git.head") or "", "head_date": mx.val("git.head_date") or "",
               "scope": mx.val("git.scope") or "no history", "stack": pack.stack, "rubric": rubric_label,
               "overall": ov, "dimensions": dims, "top_moves": moves, "not_measured": nm,
               "metrics": {k: {"value": v["value"], "line": v["line"]} for k, v in sorted(mx.m.items())}}
        res["_mx"] = mx
        return res
    finally:
        pack.cleanup()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="maturity.py", description="Score a repository on seven maturity dimensions from its files and git history; write docs/maturity-assessment.md.",
                                 epilog="Exit: 0 assessed, 1 below --min-level, 2 misuse or crash.")
    ap.add_argument("--root", default=None, help="repository root (default: CLAUDE_PROJECT_DIR, else the git toplevel of the current directory)")
    ap.add_argument("--rev", default="HEAD", help="revision whose history is read (default HEAD); files come from the working tree")
    ap.add_argument("--rubric", default=None, help="rubric JSON (default: templates/maturity_rubric.json in the plugin); edit a copy to fit the team")
    ap.add_argument("--out", default=None, help="where to write the report (default <root>/docs/maturity-assessment.md)")
    ap.add_argument("--no-write", action="store_true", help="print only; do not write the report file")
    ap.add_argument("--json", action="store_true", help="print the assessment as JSON instead of Markdown (the file is still written unless --no-write)")
    ap.add_argument("--epic", type=int, metavar="N", help="print the epic prompt for top move N (1 to 3) and write nothing")
    ap.add_argument("--run-tools", action="store_true", help="also run ruff (Python) and count its findings; off by default so nothing from the project runs")
    ap.add_argument("--max-commits", type=int, default=1000, help="read at most this many recent commits (default 1000)")
    ap.add_argument("--min-level", type=int, default=None, help="exit 1 when the overall level is below this")
    ap.add_argument("--stack", default=None, help="python, node, go or rust (default: from .claude/sdlc.json, else detected)")
    ap.add_argument("--dump-rubric", action="store_true", help="print the default rubric JSON and exit (save it, edit it, pass it with --rubric)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        if args.dump_rubric:
            sys.stdout.write(rubric_text(args.rubric))
            return 0
        root = os.path.abspath(args.root) if args.root else cl.project_root(None)
        if not os.path.isdir(root):
            print(f"maturity: --root {root} is not a directory", file=sys.stderr)
            return 2
        if args.rev.startswith("-"):
            print(f"maturity: {args.rev!r} is not a revision", file=sys.stderr)
            return 2
        res = assess(root, args)
        mx = res.pop("_mx")
        if args.epic is not None:
            if not 1 <= args.epic <= len(res["top_moves"]):
                print(f"maturity: --epic {args.epic}: there are {len(res['top_moves'])} top moves", file=sys.stderr)
                return 2
            print(epic_prompt(res["top_moves"][args.epic - 1], res["dimensions"], mx, res["head"] or "working tree", res["stack"]))
            return 0
        md = render_md(res)
        if not args.no_write:
            out = os.path.abspath(args.out) if args.out else os.path.join(root, OUT_REL)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            old = open(out, encoding="utf-8").read() if os.path.exists(out) else None
            if old != md:
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write(md)
            print(f"maturity: wrote {os.path.relpath(out, root) if out.startswith(root) else out}", file=sys.stderr)
        print(json.dumps(res, indent=2, ensure_ascii=False) if args.json else md)
        if args.min_level is not None and res["overall"]["level"] < args.min_level:
            print(f"maturity: overall level {res['overall']['level']} is below --min-level {args.min_level}", file=sys.stderr)
            return 1
        return 0
    except ValueError as exc:
        print(f"maturity: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - a crash must not read as an assessment
        print(f"maturity: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
