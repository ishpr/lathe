"""Git helpers for the orchestrator: worktrees, the drift diff, resets, and the allowed-path matcher.

The matcher is the same glob dialect as the pack's ``run_headless.sh`` scope check: ``**/`` any
directories, ``**`` anything, ``*`` within one segment, ``?`` one character, a trailing ``/`` means
the whole directory, and a bare path also matches everything below it.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

def _load_scope():
    """The scope matcher and work-state rules live in the pack's ``lib/sdlc.py`` (stdlib only) and are shared with
    ``scripts/verify.py``'s scope step, so the drift gate and verify judge a path the same way."""
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "lib" / "sdlc.py"
    spec = importlib.util.spec_from_file_location("sdlc_graph._pack_lib", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_scope = _load_scope()
#: Local work state the orchestrator and the pack write; never cleaned by a reset (lib/sdlc.py).
WORK_DIR: str = _scope.WORK_DIR
STAGE_DIR: str = _scope.STAGE_DIR
WORK_FILES: frozenset[str] = _scope.WORK_FILES
work_exempt = _scope.work_exempt
_rx = _scope._rx
outside = _scope.outside

#: Tool caches and dependency trees that sessions legitimately create and that are gitignored: not drift.
CACHE_PARTS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis", ".tox", ".nox",
                         "node_modules", ".venv", "venv", ".cache", ".next", ".turbo", ".git",
                         # what a test runner writes (gitignored wherever it exists): Playwright's results, traces and
                         # reports, coverage. The console's lifecycle contract lost a tests attempt to drift because the
                         # gate's own e2e run wrote test-results/. Bundler output (dist/, build/) stays a planted file.
                         "test-results", "playwright-report", "blob-report", "coverage", "htmlcov"})
CACHE_NAMES = frozenset({".coverage", ".eslintcache", ".DS_Store"})
#: Build state the project's own typecheck and build commands write (`tsc --incremental`, Vite): not drift either.
CACHE_SUFFIXES = (".pyc", ".tsbuildinfo")

GIT_ID = ["-c", "user.name=sdlc-graph orchestrator", "-c", "user.email=orchestrator@sdlc-graph.invalid"]


class GitError(RuntimeError):
    pass


def git(cwd: str | Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def head(cwd: str | Path) -> str:
    return git(cwd, "rev-parse", "HEAD").strip()


def reachable(cwd: str | Path, commit: str, ref: str = "HEAD") -> bool:
    """True when ``commit`` names a commit that is ``ref`` or one of its ancestors (False for an unknown sha)."""
    if not isinstance(commit, str) or not commit.strip() or commit.startswith("-"):
        return False
    proc = subprocess.run(["git", "-C", str(cwd), "merge-base", "--is-ancestor", commit.strip(), ref],
                          capture_output=True, text=True)
    return proc.returncode == 0


def current_branch(cwd: str | Path) -> str:
    return git(cwd, "rev-parse", "--abbrev-ref", "HEAD").strip()


def default_base(repo: str | Path) -> str:
    out = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).strip()
    if out:
        return out.split("/", 1)[1] if out.startswith("origin/") else out
    return current_branch(repo)


def branch_for(slug: str) -> str:
    return f"sdlc/{slug}"


def worktree_path(repo: str | Path, slug: str) -> Path:
    """``<repo>-<slug>`` next to the repository (the run_headless.sh convention)."""
    repo = Path(repo).resolve()
    return repo.parent / f"{repo.name}-{slug}"


def valid_branch_name(branch: str) -> bool:
    return subprocess.run(["git", "check-ref-format", "--branch", branch], capture_output=True, text=True).returncode == 0


def ensure_worktree(repo: str | Path, slug: str, base: str) -> tuple[Path, bool]:
    """Create the contract's worktree from ``base`` on branch ``sdlc/<slug>``, or reuse it.

    Returns ``(path, created)``. Raises GitError when the path exists but is not a worktree of
    ``repo`` (never overwrites anything).
    """
    wt = worktree_path(repo, slug)
    branch = branch_for(slug)
    if not valid_branch_name(branch):
        raise GitError(f"{branch!r} is not a valid git branch name; the contract slug cannot be orchestrated")
    git(repo, "worktree", "prune")   # forget worktrees whose directory was removed by hand
    if wt.exists():
        listed = git(repo, "worktree", "list", "--porcelain")
        if f"worktree {wt}" not in listed and f"worktree {os.path.realpath(wt)}" not in listed:
            raise GitError(f"{wt} exists and is not a worktree of {repo}; move it away (it is never deleted)")
        return wt, False
    exists = subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0
    if exists:
        git(repo, "worktree", "add", str(wt), branch)
    else:
        git(repo, "worktree", "add", "-b", branch, str(wt), base)
    return wt, True


def _z(out: str) -> list[str]:
    return [p for p in out.split("\0") if p]


def changed_files(wt: str | Path, start_commit: str, slug: str | None = None) -> list[str]:
    """Every path that differs from ``start_commit``: commits since, staged and unstaged edits,
    deletions and untracked files; local work state excluded (see :func:`work_exempt`)."""
    files = set(_z(git(wt, "diff", "--name-only", "-z", "--no-renames", start_commit)))
    files |= set(_z(git(wt, "ls-files", "--others", "--exclude-standard", "-z")))
    return sorted(f for f in files if not work_exempt(f, slug))


def committed_files(wt: str | Path, start_commit: str) -> list[str]:
    """Paths whose content at HEAD differs from ``start_commit``: what the session committed (a file that was untracked
    at dispatch and is now in a commit is listed, since the commit itself is the change)."""
    try:
        return sorted(_z(git(wt, "diff", "--name-only", "-z", "--no-renames", start_commit, "HEAD")))
    except GitError:
        return []


def session_changes(wt: str | Path, start_commit: str, dirty_at_start: dict[str, Any] | None, slug: str | None = None,
                    allowed: list[str] | None = None) -> list[str]:
    """The files a session changed: every path that differs from ``start_commit``, minus uncommitted changes that were
    already there at dispatch and are untouched. A file inside ``allowed`` that the session committed counts even when
    its bytes are the ones lying untracked at dispatch: the console's lifecycle contract was judged ``no_change`` twice
    for committing the very spec an earlier attempt had left untracked, and committing the step's work is the step's
    change. A pre-existing edit outside the allowed paths that a commit swept up stays a human's, not drift."""
    dirty0 = dirty_at_start or {}
    committed = set(committed_files(wt, start_commit))
    out = []
    for f in changed_files(wt, start_commit, slug):
        untouched = f in dirty0 and sha256_file(Path(wt) / f) == dirty0[f]
        own_commit = f in committed and allowed is not None and not outside([f], allowed)
        if own_commit or not untouched:
            out.append(f)
    return out


def _is_cache(path: str) -> bool:
    """A gitignored file a session's own tools create: dependency trees, interpreter and linter caches, build state.
    The console's third contract lost an implement attempt to drift because `tsc` wrote tsconfig.tsbuildinfo."""
    parts = path.split("/")
    return any(p in CACHE_PARTS for p in parts) or parts[-1] in CACHE_NAMES or path.endswith(CACHE_SUFFIXES)


def ignored_snapshot(wt: str | Path) -> dict[str, str | None]:
    """``{path: sha256}`` of gitignored files (tool caches excluded). Git's diff never shows these, so the
    drift gate compares them with the state at dispatch."""
    out = _z(git(wt, "ls-files", "--others", "--ignored", "--exclude-standard", "-z"))
    return {f: sha256_file(Path(wt) / f) for f in sorted(out) if not _is_cache(f)}


def new_ignored(wt: str | Path, before: dict[str, str | None] | None, slug: str | None = None) -> list[str]:
    """Ignored files that are new, or differ from ``before`` (the snapshot at dispatch), outside local work state."""
    before = before or {}
    now = ignored_snapshot(wt)
    return sorted(f for f, sha in now.items() if not work_exempt(f, slug) and (f not in before or before[f] != sha))


def escaping_links(wt: str | Path, files: list[str]) -> list[str]:
    """Symlinks among ``files`` whose target resolves outside the worktree."""
    root = Path(os.path.realpath(wt))
    bad = []
    for f in files:
        p = Path(wt) / f
        if p.is_symlink():
            target = Path(os.path.realpath(p))
            if target != root and root not in target.parents:
                bad.append(f)
    return bad


def dirty_files(wt: str | Path) -> list[str]:
    """Uncommitted changes (tracked edits and untracked files), ``.claude/work/`` excluded."""
    out = git(wt, "status", "--porcelain", "-z", "--untracked-files=all")
    files = []
    for entry in _z(out):
        path = entry[3:]
        if path and not path.startswith(WORK_DIR):
            files.append(path)
    return sorted(set(files))


def dirty_snapshot(wt: str | Path) -> dict[str, str | None]:
    """``{path: sha256 or None (deleted)}`` of the uncommitted changes, ``.claude/work/`` excluded."""
    return {f: sha256_file(Path(wt) / f) for f in dirty_files(wt)}


def new_dirty(wt: str | Path, before: dict[str, str | None] | None) -> list[str]:
    """Uncommitted changes that were not there (or differed) when ``before`` was taken."""
    before = before or {}
    now = dirty_snapshot(wt)
    return sorted(f for f, sha in now.items() if f not in before or before[f] != sha)


def reset_to(wt: str | Path, commit: str) -> str:
    """Hard-reset to ``commit`` and remove untracked files outside ``.claude/work``. Returns the
    HEAD it discarded (still reachable from the reflog)."""
    import shutil
    import tempfile

    was = head(wt)
    work = Path(wt) / WORK_DIR
    with tempfile.TemporaryDirectory(prefix="orch-work-") as keep:
        # .claude/work is local state; a session that committed it must not lose it to the reset
        if work.is_dir():
            shutil.copytree(work, Path(keep) / "work", symlinks=True)
        git(wt, "reset", "--hard", "-q", commit)
        git(wt, "clean", "-fdq", "-e", ".claude/work", "-e", ".claude/work/**")
        if (Path(keep) / "work").is_dir():
            shutil.copytree(Path(keep) / "work", work, symlinks=True, dirs_exist_ok=True)
    return was


def snapshot(wt: str | Path, extra: list[str] | None = None) -> str | None:
    """A commit object holding the worktree as it is now (tracked edits, deletions and untracked
    files not ignored; ``.claude/work/`` excluded), parented on HEAD, without touching HEAD, the
    index or the files. None when the tree is clean. Drift resets restore this, so uncommitted work
    from earlier steps (the pack commits app code only at ship) survives a reset."""
    if not dirty_files(wt) and not extra:
        return None
    idx = git(wt, "rev-parse", "--git-path", "index.orchestrator").strip()
    idx_path = Path(idx) if Path(idx).is_absolute() else Path(wt) / idx
    env = {**os.environ, "GIT_INDEX_FILE": str(idx_path)}

    def run(*args: str) -> str:
        proc = subprocess.run(["git", "-C", str(wt), *GIT_ID, *args], capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed in {wt}: {proc.stderr.strip()}")
        return proc.stdout

    try:
        run("read-tree", "HEAD")
        run("add", "-A")
        run("rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", ".claude/work")
        for f in extra or []:
            run("add", "-f", "--", f)
        tree = run("write-tree").strip()
        return run("commit-tree", tree, "-p", "HEAD", "-m", "sdlc-graph orchestrator: worktree at step start").strip()
    finally:
        idx_path.unlink(missing_ok=True)


def save_discarded(wt: str | Path, label: str, extra: list[str] | None = None) -> str:
    """Keep what a reset is about to destroy: a snapshot commit of the tree as it is (tracked edits,
    untracked files, plus any ``extra`` ignored files), or HEAD when the tree is clean, under
    ``refs/sdlc/discarded/<label>``. Returns the ref name. Uncommitted work a human made in the
    worktree after dispatch is recoverable from it (``git show <ref>:<path>``)."""
    ref = f"refs/sdlc/discarded/{label}"
    commit = snapshot(wt, extra=extra) or head(wt)
    git(wt, "update-ref", ref, commit)
    return ref


def restore(wt: str | Path, commit: str, snap: str | None, *, label: str | None = None,
            extra: list[str] | None = None) -> str:
    """Put the worktree back to how a step found it: HEAD at ``commit`` and, when ``snap`` is set,
    the uncommitted changes it held (as uncommitted changes again). Returns the discarded HEAD.
    With ``label`` the tree is first saved by :func:`save_discarded`; ``extra`` (ignored files the
    session created) are saved too and then removed."""
    if label:
        save_discarded(wt, label, extra)
    for f in extra or []:
        (Path(wt) / f).unlink(missing_ok=True)
    was = reset_to(wt, commit)
    if snap:
        git(wt, "checkout", snap, "--", ".")
        for f in _z(git(wt, "diff", "--name-only", "-z", "--no-renames", "--diff-filter=D", commit, snap)):
            # the snapshot leaves .claude/work out, so a tracked file there is absent from it, not deleted
            if not f.startswith(WORK_DIR):
                (Path(wt) / f).unlink(missing_ok=True)
        git(wt, "reset", "-q")
    return was


def diff_text(wt: str | Path, start_commit: str, limit: int = 60_000) -> str:
    out = git(wt, "diff", start_commit, check=False)
    untracked = _z(git(wt, "ls-files", "--others", "--exclude-standard", "-z"))
    for f in untracked:
        if f.startswith(WORK_DIR):
            continue
        try:
            raw = (Path(wt) / f).read_bytes()
        except OSError:
            continue
        if b"\0" in raw:    # a binary file (a .pyc the tests left behind) cannot go into a prompt
            out += f"\n--- /dev/null\n+++ b/{f}\n+[binary file, {len(raw)} bytes]\n"
            continue
        body = raw.decode(errors="replace")
        out += f"\n--- /dev/null\n+++ b/{f}\n" + "".join("+" + line + "\n" for line in body.splitlines())
    return out if len(out) <= limit else out[:limit] + f"\n... [diff truncated at {limit} characters]\n"


def sha256_file(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None
