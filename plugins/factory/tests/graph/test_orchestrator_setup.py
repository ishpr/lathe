"""Worktree setup (``commands.setup``) and the ship credential fallback, with the fake executor.

A fake setup command is a shell script that counts its runs in a file outside the worktree and creates
``node_modules/.ok`` inside it; a failing one prints many lines and exits 3. A fake ``gh`` on PATH answers
``auth token``.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

from sdlc_graph.ledger import decisions
from sdlc_graph.orchestrator import dispatch, gitops, loop, setup as wsetup, store
from sdlc_graph.orchestrator.steps import STEPS

from test_orchestrator import (A, FEATURE, GIT, _git, cfg_for, env, feature, repo, state,  # noqa: F401 (fixtures)
                               tick_until)


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _configure(repo: Path, command: str | None, *, lock: str = "lock v1\n") -> None:
    """Commit a lockfile and a ``.claude/sdlc.json`` with ``commands.setup`` (the key is absent when None)."""
    (repo / "pnpm-lock.yaml").write_text(lock)
    (repo / ".gitignore").write_text(".claude/work/\nnode_modules/\n")
    cmds = {"test": "true"}
    if command is not None:
        cmds["setup"] = command
    (repo / ".claude").mkdir(exist_ok=True)
    (repo / ".claude" / "sdlc.json").write_text(json.dumps({"stack": "node", "commands": cmds}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "setup config")


@pytest.fixture
def counter(tmp_path: Path) -> Path:
    return tmp_path / "setup-count.txt"


@pytest.fixture
def ok_setup(tmp_path: Path, counter: Path) -> str:
    s = _script(tmp_path / "install.sh", f'echo installing\npwd >> "{counter}"\nmkdir -p node_modules\n: > node_modules/.ok\n')
    return f"sh {s}"


def _setup_runs(conn, slug=A):
    return [r for r in store.step_runs(conn, slug, "setup")]


# --------------------------------------------------------------------------- setup


def test_setup_runs_in_the_new_worktree_once_and_records_a_run(feature, repo, env, counter, ok_setup):
    _configure(repo, ok_setup)
    cfg = cfg_for(repo)
    run = dispatch.dispatch(feature, cfg, A, STEPS["ready"], feature=FEATURE, attempt=1)
    wt = gitops.worktree_path(repo, A)
    assert run["step"] == "tests" and run["finished_at"] is None
    assert counter.read_text().split() == [os.path.realpath(wt)] or counter.read_text().split() == [str(wt)]
    assert (wt / "node_modules" / ".ok").is_file()
    marker = json.loads((wt / ".claude" / "work" / "orchestrator" / "setup.ok").read_text())
    assert marker["lockfile"] == "pnpm-lock.yaml" and len(marker["sha256"]) == 64
    log = cfg.log_root / A / "setup.log"
    assert "installing" in log.read_text()
    (r,) = _setup_runs(feature)
    assert (r["executor"], r["outcome"], r["requested_model"]) == ("local", "ok", "none") and r["resolved_model"] is None
    assert r["meta"]["exit_code"] == 0 and r["meta"]["duration_seconds"] >= 0 and r["finished_at"]
    assert r["id"] < run["id"]
    # the marker is the orchestrator's own state: not drift
    assert not gitops.changed_files(wt, gitops.head(wt), A)


def test_setup_is_skipped_while_the_lockfile_is_unchanged_and_runs_again_when_it_changes(feature, repo, env, counter, ok_setup):
    _configure(repo, ok_setup)
    cfg = cfg_for(repo)
    wt, _ = gitops.ensure_worktree(repo, A, "main")
    assert wsetup.ensure(feature, cfg, A, wt) is None
    assert wsetup.ensure(feature, cfg, A, wt) is None
    assert len(counter.read_text().split()) == 1 and len(_setup_runs(feature)) == 1
    (wt / "pnpm-lock.yaml").write_text("lock v2\n")                       # a reset to another commit, or an edit
    assert wsetup.ensure(feature, cfg, A, wt) is None
    assert len(counter.read_text().split()) == 2 and len(_setup_runs(feature)) == 2
    # a reset keeps ignored directories and the marker, so the same lockfile is not set up again
    gitops.reset_to(wt, gitops.head(wt))
    assert (wt / "node_modules" / ".ok").exists()
    (wt / "pnpm-lock.yaml").write_text("lock v1\n")
    gitops.reset_to(wt, gitops.head(wt))
    assert (wt / "pnpm-lock.yaml").read_text() == "lock v1\n"
    assert wsetup.ensure(feature, cfg, A, wt) is None                      # marker holds v2, the file is v1: again
    assert len(counter.read_text().split()) == 3


@pytest.mark.parametrize("command", [None, "", "   "])
def test_setup_never_runs_when_the_key_is_missing_or_empty(feature, repo, env, counter, command):
    _configure(repo, command)
    cfg = cfg_for(repo)
    run = dispatch.dispatch(feature, cfg, A, STEPS["ready"], feature=FEATURE, attempt=1)
    assert run["step"] == "tests"
    assert not _setup_runs(feature) and not (cfg.log_root / A / "setup.log").exists()
    assert not (gitops.worktree_path(repo, A) / ".claude" / "work" / "orchestrator").exists()


def test_a_failing_setup_stops_the_contract_with_a_p1_decision_quoting_the_log(feature, repo, env, tmp_path, counter):
    fail = _script(tmp_path / "fail.sh", 'i=1\nwhile [ $i -le 30 ]; do echo "line $i"; i=$((i+1)); done\necho boom >&2\nexit 3\n')
    _configure(repo, f"sh {fail}")
    cfg = cfg_for(repo)
    rep = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)
    entry = next(c for c in rep["last"]["contracts"] if c["slug"] == A)
    assert entry["action"] == "dispatch_failed"
    (r,) = _setup_runs(feature)
    assert r["outcome"] == "setup_failed" and r["meta"]["exit_code"] == 3 and r["finished_at"]
    assert not store.step_runs(feature, A, "tests")                                       # no session was started
    (d,) = store.open_escalations(feature, [A])
    assert d["severity"] == "P1"
    q = d["question"].splitlines()
    assert "line 30" in q and "boom" in q and "line 1" not in q and "line 10" not in q    # the last 20 lines only
    assert not (gitops.worktree_path(repo, A) / ".claude" / "work" / "orchestrator" / "setup.ok").exists()
    # the next tick waits on the decision and does not run setup again
    rep2 = loop.orchestrate(FEATURE, cfg, once=True, quiet=True)
    assert next(c for c in rep2["last"]["contracts"] if c["slug"] == A)["action"] == "blocked_on_decision"
    assert len(_setup_runs(feature)) == 1
    # a human fixes the cause and answers retry: setup runs again and the contract goes on
    _script(fail, "mkdir -p node_modules\n")
    decisions.resolve(feature, d["id"], "retry", actor="@ish")
    tick_until(feature, cfg, lambda rp: state(feature, A) == "tests_frozen")
    assert [x["outcome"] for x in _setup_runs(feature)] == ["setup_failed", "ok"]


def test_setup_has_a_timeout_and_a_missing_command_fails_cleanly(feature, repo, env, tmp_path):
    slow = _script(tmp_path / "slow.sh", "echo start\nsleep 30\n")
    _configure(repo, f"sh {slow}")
    cfg = cfg_for(repo, setup_timeout=0.5)
    wt, _ = gitops.ensure_worktree(repo, A, "main")
    t0 = time.monotonic()
    run = wsetup.ensure(feature, cfg, A, wt)
    assert time.monotonic() - t0 < 20 and run["outcome"] == "setup_failed" and run["meta"]["exit_code"] == 124
    assert "timed out" in (cfg.log_root / A / "setup.log").read_text()
    _configure(repo, "definitely-not-installed-tool --frozen")
    run = wsetup.ensure(feature, cfg_for(repo), "rate-limit-headers", gitops.ensure_worktree(repo, "rate-limit-headers", "main")[0])
    assert run["outcome"] == "setup_failed" and run["meta"]["exit_code"] == 127


def test_setup_chains_steps_with_and_and_stops_at_the_first_failure(feature, repo, env, tmp_path):
    """The console's setup is an install and then Next's typegen; no shell runs the command, so `&&` is understood
    here, one step after another in the same worktree, and a package script in the worktree is not needed."""
    first = _script(tmp_path / "first.sh", "echo one > step-one\n")
    second = _script(tmp_path / "second.sh", "echo two > step-two\n")
    _configure(repo, f"sh {first} && sh {second}")
    cfg = cfg_for(repo)
    wt, _ = gitops.ensure_worktree(repo, A, "main")
    assert wsetup.ensure(feature, cfg, A, wt) is None
    assert (wt / "step-one").read_text() == "one\n" and (wt / "step-two").read_text() == "two\n"
    log = (cfg.log_root / A / "setup.log").read_text()
    assert f"$ sh {first}\n" in log and f"$ sh {second}\n" in log
    # the second step never runs when the first fails, and the failure keeps its exit code
    boom = _script(tmp_path / "boom.sh", "echo boom >&2\nexit 7\n")
    _configure(repo, f"sh {boom} && sh {second}")
    wt2, _ = gitops.ensure_worktree(repo, "rate-limit-headers", "main")
    run = wsetup.ensure(feature, cfg_for(repo), "rate-limit-headers", wt2)
    assert run["outcome"] == "setup_failed" and run["meta"]["exit_code"] == 7
    assert not (wt2 / "step-two").exists()
    # an empty step is a misuse, not a shell surprise (a changed command runs again in the first worktree)
    _configure(repo, f"sh {first} && ")
    run = wsetup.ensure(feature, cfg_for(repo), A, wt)
    assert run["outcome"] == "setup_failed" and run["meta"]["exit_code"] == 2


def test_setup_uses_the_cleaned_path_and_the_daemons_secrets_stay_out(feature, repo, env, tmp_path, monkeypatch):
    out = tmp_path / "env.txt"
    probe = _script(tmp_path / "probe.sh", f'env > "{out}"\n')
    _configure(repo, f"sh {probe}")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secret@h/db")
    monkeypatch.setenv("GH_TOKEN", "not-for-setup")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "venv"))
    monkeypatch.setenv("PATH", f"{tmp_path / 'venv' / 'bin'}{os.pathsep}{os.environ['PATH']}")
    wt, _ = gitops.ensure_worktree(repo, A, "main")
    assert wsetup.ensure(feature, cfg_for(repo), A, wt) is None
    text = out.read_text()
    assert "DATABASE_URL" not in text and "not-for-setup" not in text and "VIRTUAL_ENV" not in text
    assert str(tmp_path / "venv" / "bin") not in text


# --------------------------------------------------------------------------- the ship credential


@pytest.fixture
def fake_gh_on_path(tmp_path: Path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    calls = tmp_path / "gh-calls.txt"
    _script(bin_ / "gh", f'echo "$@" >> "{calls}"\n[ "$1 $2" = "auth token" ] && echo tok-from-gh\n')
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return calls


def _dump_executor(tmp_path: Path) -> tuple[list[str], Path]:
    out = tmp_path / "executor-env.txt"
    return ["sh", str(_script(tmp_path / "exec.sh", f'env > "{out}"\n'))], out


def test_ship_gets_the_gh_auth_token_in_local_mode_only_when_the_environment_has_none(feature, repo, env, tmp_path, fake_gh_on_path, capsys):
    cmd, out = _dump_executor(tmp_path)
    cfg = cfg_for(repo, executor=cmd)
    run = dispatch.dispatch(feature, cfg, A, STEPS["verified"], feature=FEATURE, attempt=1)
    dispatch.CHILDREN[run["pid"]].wait(timeout=30)
    text = out.read_text()
    assert "GH_TOKEN=tok-from-gh" in text and "credential.helper" in text
    assert run["meta"]["ship_credential"] == "gh auth token"
    err = capsys.readouterr().err
    assert "ship credential: gh auth token" in err and "tok-from-gh" not in err
    assert "tok-from-gh" not in json.dumps(run, default=str)
    # resolved once: a second ship does not call gh again
    dispatch.ship_token(cfg)
    assert fake_gh_on_path.read_text().count("auth token") == 1
    # other steps never get a token
    out.unlink()
    run2 = dispatch.dispatch(feature, cfg_for(repo, executor=cmd), "rate-limit-headers", STEPS["ready"], feature=FEATURE, attempt=1)
    dispatch.CHILDREN[run2["pid"]].wait(timeout=30)
    assert "tok-from-gh" not in out.read_text() and run2["meta"]["ship_credential"] is None


def test_an_explicit_token_wins_and_runner_mode_keeps_its_behaviour(feature, repo, env, tmp_path, fake_gh_on_path, monkeypatch):
    cmd, out = _dump_executor(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "explicit-token")
    run = dispatch.dispatch(feature, cfg_for(repo, executor=cmd), A, STEPS["verified"], feature=FEATURE, attempt=1)
    dispatch.CHILDREN[run["pid"]].wait(timeout=30)
    text = out.read_text()
    assert "GITHUB_TOKEN=explicit-token" in text and "tok-from-gh" not in text and run["meta"]["ship_credential"] is None
    monkeypatch.delenv("GITHUB_TOKEN")
    assert dispatch.ship_token(cfg_for(repo, executor_mode="runner")) is None
    assert not fake_gh_on_path.exists()


def test_without_gh_or_a_login_ship_gets_no_token(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _script(bin_ / "gh", "echo 'not logged in' >&2\nexit 1\n")
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    cfg = cfg_for(repo)
    assert dispatch.ship_token(cfg) is None and cfg.github_token is None
    assert "ship credential: none" in capsys.readouterr().err
