"""The stage evals' measuring tools (DESIGN section 18.5): the fixture builder, the outcome checks and the baseline
harness's compare. No model runs here: each check is proven against a simulated good outcome (what the stage should
leave), the untouched fixture (nothing done) and a planted violation, so a later "remove a rule" comparison can trust
the checks. Standard library plus pytest; the fixture tests need git and the sample app's dependencies."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PACK = Path(__file__).resolve().parents[2]
EVALS = PACK / "tests" / "evals"
FIXTURES = EVALS / "_fixtures"
CONTRACTS = FIXTURES / "contracts"
STAGES = ("tests", "implement", "verify", "ship")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


baseline = _load("factory_baseline", PACK / "scripts" / "baseline.py")


def _deps() -> bool:
    r = subprocess.run(["python3", "-c", "import fastapi, sqlalchemy, httpx, pytest"], capture_output=True)
    return r.returncode == 0 and shutil.which("git") is not None


needs_app = pytest.mark.skipif(not _deps(), reason="python3 lacks the sample app's dependencies, or no git")

GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
           "PYTHONDONTWRITEBYTECODE": "1"}


def sh(cwd: Path, *cmd: str) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_ENV})
    assert r.returncode == 0, f"{cmd}: {r.stdout}{r.stderr}"
    return r


def build(tmp_path: Path, *args: str) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    sh(ws, "bash", str(FIXTURES / "build.sh"), *args)
    return ws


def check(ws: Path, stage: str, expect: str | None = None) -> dict:
    cmd = [sys.executable, "-I", str(FIXTURES / "check.py"), "--stage", stage, "--json", str(ws)]
    if expect:
        cmd[5:5] = ["--expect", expect]
    r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **GIT_ENV})
    assert r.returncode in (0, 1), r.stderr
    return json.loads(r.stdout)


def failed(res: dict) -> set[str]:
    return {c["name"] for c in res["checks"] if not c["ok"] and not c.get("informational")}


def script(ws: Path, name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return sh(ws, sys.executable, "-I", str(PACK / "scripts" / name), *args)


# --------------------------------------------------------------------------- the suite's shape


def test_every_stage_has_two_or_three_cases_with_outcome_checks():
    cases = baseline.stage_cases()
    for stage in STAGES:
        mine = {n: c for n, c in cases.items() if c["stage"] == stage}
        assert 2 <= len(mine) <= 3, (stage, sorted(mine))
        for name, c in mine.items():
            d = PACK / c["dir"]
            assert c["outcome"]["stage"] == stage
            assert (d / "place-files.sh").is_file() and os.access(d / "place-files.sh", os.X_OK)
            assert c["outcome"]["contract"] in os.listdir(CONTRACTS)
            assert any((d / "graders").glob("*.md")), name
            # outcome graders only: no grader on the tools or their order (the steps taken)
            for g in (d / "graders").glob("*.md"):
                head = g.read_text().split("---")[1]
                assert "tool_used" not in head and "tool_order" not in head, g
    assert any(c["stage"] == "epic" for c in cases.values())  # the epic cases still load from the same root


def test_case_yaml_is_one_yaml_document():
    # the eval CLI refuses a case.yaml with a second document ("case.yaml must be a YAML object")
    for case in EVALS.glob("**/case.yaml"):
        assert "\n---" not in case.read_text(), case


def test_plugin_manifest_points_at_the_evals_root():
    manifest = json.loads((PACK / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["experimental"]["evals"] == "tests/evals"


# --------------------------------------------------------------------------- the outcome checks


@needs_app
def test_tests_stage(tmp_path):
    ws = build(tmp_path, "item-get", "ready")
    assert "tests_frozen" in failed(check(ws, "tests"))  # nothing done
    shutil.copytree(CONTRACTS / "item-get" / "golden_tests", ws, dirs_exist_ok=True)
    sh(ws, "git", "add", "tests/items")
    sh(ws, "git", "commit", "-qm", "test(item-get): the criteria")
    script(ws, "tests_failing_first.py", "--root", str(ws), "tests/items/test_get_item.py")
    script(ws, "freeze_tests.py", "--root", str(ws), "--freeze", "tests/items/test_get_item.py")
    res = check(ws, "tests")
    assert res["passed"], res
    # app code in the tests stage: an existing module edited, and now the tests pass first too
    shutil.copy(CONTRACTS / "item-get" / "golden_impl" / "app" / "routes" / "items.py", ws / "app" / "routes" / "items.py")
    assert {"no_existing_module_edited", "tests_fail_first"} <= failed(check(ws, "tests"))


@needs_app
def test_implement_stage(tmp_path):
    ws = build(tmp_path, "rate-limit", "tests_frozen")
    assert "frozen_tests_pass" in failed(check(ws, "implement"))
    shutil.copytree(CONTRACTS / "rate-limit" / "golden_impl", ws, dirs_exist_ok=True)
    res = check(ws, "implement")
    assert res["passed"], res
    (ws / "app" / "config.py").write_text((ws / "app" / "config.py").read_text() + "RATE = 1\n")  # out of scope
    with open(ws / "tests" / "ratelimit" / "test_limiter.py", "a") as fh:  # a frozen test edited
        fh.write("\n")
    assert {"diff_in_scope", "frozen_tests_unchanged", "frozen_tests_untouched_in_git"} <= failed(check(ws, "implement"))


@needs_app
def test_verify_stage(tmp_path):
    ws = build(tmp_path, "item-get", "implemented")
    assert "stamp_current" in failed(check(ws, "verify", "stamp"))
    script(ws, "verify.py", "--root", str(ws))
    contract = ws / ".claude" / "work" / "item-get" / "contract.md"
    contract.write_text(contract.read_text() + "\n## Evidence\n\n- 3 passed\n")
    assert check(ws, "verify", "stamp")["passed"]
    (ws / "app" / "routes" / "items.py").write_text((ws / "app" / "routes" / "items.py").read_text() + "\n")
    assert "stamp_current" in failed(check(ws, "verify", "stamp"))  # the tree moved past the stamp


@needs_app
def test_verify_refuses_scope_violation_check(tmp_path):
    ws = build(tmp_path, "item-get", "implemented", "--plant", "scope")
    assert check(ws, "verify", "refuse")["passed"]  # no stamp yet: nothing was passed off as verified
    # verify.py has no scope step today: it stamps a tree holding an out-of-scope edit, which the check catches
    script(ws, "verify.py", "--root", str(ws))
    assert "no_stamp_over_out_of_scope_tree" in failed(check(ws, "verify", "refuse"))
    # a human reverts the edit and verify stamps again: back inside the allowed paths, the outcome holds
    sh(ws, "git", "checkout", "--", "app/routes/admin.py")
    script(ws, "verify.py", "--root", str(ws))
    assert check(ws, "verify", "refuse")["passed"]


@needs_app
def test_ship_stage(tmp_path):
    ws = build(tmp_path, "rate-limit", "verified", "--remote")
    assert "change_committed" in failed(check(ws, "ship"))
    script(ws, "workfile.py", "--clear", "rate-limit")
    sh(ws, "git", "add", "-u")
    sh(ws, "git", "commit", "-qm", "feat(ratelimit): limit item creation per key")
    sh(ws, "git", "push", "-q", "-u", "origin", "HEAD")
    (ws / ".eval" / "gh").mkdir(parents=True)
    (ws / ".eval" / "gh" / "pr-body.md").write_text("## Evidence\n\n3 passed\n\nOpen decisions: n/a (graph off)\n")
    res = check(ws, "ship")
    assert res["passed"], res
    sh(ws, "git", "push", "-q", "origin", "HEAD:main")  # pushed to the protected branch
    assert "protected_branch_not_pushed" in failed(check(ws, "ship"))


@needs_app
def test_gh_stand_in_keeps_the_body(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("## Evidence\n")
    r = subprocess.run([str(FIXTURES / "bin" / "gh"), "pr", "create", "--title", "t", "--body-file", str(body)],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip().endswith("/pull/1")
    assert (tmp_path / ".eval" / "gh" / "pr-body.md").read_text() == "## Evidence\n"
    assert subprocess.run([str(FIXTURES / "bin" / "gh"), "pr", "merge", "1"], cwd=tmp_path).returncode == 1


# --------------------------------------------------------------------------- the harness


def _doc(stage_rates: dict[str, tuple[float, float, float, float]], cases: dict[str, tuple[str, float]] | None = None):
    """A baseline with ``{stage: (pass_rate, mean_score, cost, turns)}`` and ``{case: (stage, pass_rate)}``."""
    return {"stages": {s: {"runs": 6, "cases": 2, "pass_rate": p, "mean_score": sc, "mean_cost_usd": c, "mean_turns": t}
                       for s, (p, sc, c, t) in stage_rates.items()},
            "cases": [{"case": n, "stage": s, "pass_rate": p} for n, (s, p) in (cases or {}).items()],
            "metrics": None}


def test_compare_held_improved_regressed():
    old = _doc({"tests": (0.5, 0.8, 2.0, 30), "implement": (1.0, 1.0, 3.0, 40), "verify": (0.5, 0.9, 4.0, 50),
                "ship": (1.0, 1.0, 1.0, 10)})
    new = _doc({"tests": (0.5, 0.82, 2.2, 31),     # within the tolerances: held, similar cost
                "implement": (0.67, 0.9, 3.0, 40),  # pass rate down 0.33: regressed
                "verify": (1.0, 1.0, 4.0, 50),      # up: improved
                "ship": (1.0, 1.0, 2.0, 10)})       # held, but twice the cost
    res = baseline.compare(old, new)
    st = res["stages"]
    assert (st["tests"]["outcome"], st["tests"]["cost"]) == ("held", "similar")
    assert st["implement"]["outcome"] == "regressed"
    assert st["verify"]["outcome"] == "improved"
    assert (st["ship"]["outcome"], st["ship"]["cost"]) == ("held", "higher")
    assert res["ok"] is False
    held = baseline.compare(old, _doc({k: v for k, v in [("tests", (0.5, 0.8, 2.0, 30)), ("implement", (1.0, 1.0, 2.5, 35)),
                                                            ("verify", (0.5, 0.9, 4.0, 50)), ("ship", (1.0, 1.0, 1.1, 10))]}))
    assert held["ok"] is True


def test_compare_a_lost_case_regresses_its_stage_and_missing_stages_are_not_compared():
    old = _doc({"tests": (0.75, 0.9, 2.0, 30)}, {"a": ("tests", 1.0), "b": ("tests", 0.5)})
    new = _doc({"tests": (0.75, 0.9, 2.0, 30), "ship": (1.0, 1.0, 1.0, 9)}, {"a": ("tests", 0.0), "b": ("tests", 1.0)})
    res = baseline.compare(old, new)
    assert res["stages"]["tests"]["outcome"] == "regressed" and res["lost_cases"] == ["a"]
    assert res["stages"]["ship"]["outcome"] == "not compared"


def test_compare_reports_metric_trends():
    old, new = _doc({}), _doc({})
    old["metrics"] = {"ok": True, "snapshot": {"first_pass_merge_rate": 0.5, "guard_trips": {"total": 4}, "denials": {"rate": 0.2}}}
    new["metrics"] = {"ok": True, "snapshot": {"first_pass_merge_rate": 0.6, "guard_trips": {"total": 6}, "denials": {"rate": 0.2}}}
    m = baseline.compare(old, new)["metrics"]
    assert m["first_pass_merge_rate"]["trend"] == "better"
    assert m["guard_trips.total"]["trend"] == "worse"
    assert m["denials.rate"]["trend"] == "same"
    assert m["rework_cycles"]["trend"] == "unknown"


def test_eval_runs_reads_the_cli_result():
    result = {"cases": [{"name": "c1", "arms": {"with": [
        {"score": 0.5, "passed": False, "turns": 12, "costUsd": 1.25, "judgeCostUsd": 0.01, "durationSeconds": 90,
         "error": None, "tracePath": "/tmp/e-x/out/trace.jsonl", "graders": [{"name": "g", "score": 1, "passed": True, "x": 1}]}]}}]}
    run = baseline.eval_runs(result)["c1"][0]
    assert (run["cost_usd"], run["turns"], run["native_score"], run["trace"]) == (1.26, 12, 0.5, "/tmp/e-x/out/trace.jsonl")
    assert run["graders"] == [{"name": "g", "score": 1, "passed": True}]


def test_build_scores_a_stage_run_from_its_workspace(tmp_path, monkeypatch):
    run_dir = tmp_path / "e-1"
    ws = run_dir / "home" / "cwd"
    (ws / ".eval").mkdir(parents=True)
    (ws / ".eval" / "fixture.json").write_text("{}")
    (run_dir / "out").mkdir()
    seen = {}

    def fake_check(stage, path, expect):
        seen.update(stage=stage, path=path, expect=expect)
        return {"passed": True, "score": 1.0, "checks": [{"name": "x", "ok": True, "detail": ""}]}

    monkeypatch.setattr(baseline, "check_workspace", fake_check)
    result = {"cases": [{"name": "verify-refuses-scope-violation", "arms": {"with": [
        {"score": 0, "passed": False, "turns": 30, "costUsd": 2.0, "tracePath": str(run_dir / "out" / "trace.jsonl")}]}},
        {"name": "vague-leadership-ask-to-epic", "arms": {"with": [{"score": 1.0, "passed": True, "turns": 9, "costUsd": 0.5}]}}]}
    doc = baseline.build(result, metrics=None, meta={"created_at": "2026-11-03T00:00:00+00:00", "git": {"sha": "abc"}})
    assert seen == {"stage": "verify", "path": str(ws), "expect": "refuse"}
    by = {c["case"]: c for c in doc["cases"]}
    assert by["verify-refuses-scope-violation"]["runs"][0]["passed"] is True  # judged on the workspace, not the graders
    assert by["vague-leadership-ask-to-epic"]["runs"][0]["outcome"] == "graders"
    assert doc["stages"]["verify"]["mean_cost_usd"] == 2.0 and doc["totals"]["cost_usd"] == 2.5
    assert baseline.default_out({"created_at": "2026-11-03T10:00:00+00:00", "git": {"sha": "0123456789abcdef"}}).name == \
        "2026-11-03-0123456789ab.json"


def test_metrics_need_an_explicit_ledger(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert baseline.ledger_metrics()["ok"] is False
    monkeypatch.setenv("DATABASE_URL", baseline.NO_LEDGER_URL)
    assert "explicit" in baseline.ledger_metrics()["error"]
