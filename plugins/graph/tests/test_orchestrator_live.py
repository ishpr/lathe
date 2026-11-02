"""One real end-to-end orchestrator run on a copy of a sample repository (opt-in, costs money).

Skipped unless all of these are set:

    SDLC_ORCH_LIVE=1          the explicit opt-in (real claude sessions, real cost)
    SAMPLE_REPO=<path>        a git checkout to copy (it is copied, never written); unset, the
                              repository's examples/sample-app is copied and committed instead
    ORCH_LIVE_REMOTE=<url>    a SCRATCH GitHub repository the run may push to and open a draft PR on;
                              never the fixture's own origin

and a logged-in ``claude`` and ``gh`` are on PATH. The copy gets the pack initialised
(``init_project.py``), its origin replaced by ``ORCH_LIVE_REMOTE``, one feature with the demo's
rate-limit contract, and then ``orchestrate --live`` runs until the contract is ``in_review`` with a
``pr_url`` in the ledger (or ``ORCH_LIVE_MINUTES``, default 90, pass).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from sdlc_graph.ledger import evidence, intake, nodes, transitions
from sdlc_graph.orchestrator import config, loop

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "demo" / "data"
PACK = ROOT.parent / "sdlc"
SAMPLE_APP = ROOT.parent.parent / "examples" / "sample-app"

pytestmark = pytest.mark.skipif(
    not (os.environ.get("SDLC_ORCH_LIVE") == "1" and os.environ.get("ORCH_LIVE_REMOTE")
         and (os.environ.get("SAMPLE_REPO") or SAMPLE_APP.is_dir())
         and shutil.which("claude") and shutil.which("gh") and PACK.is_dir()),
    reason="live orchestrator run: set SDLC_ORCH_LIVE=1 and ORCH_LIVE_REMOTE (a scratch GitHub repo); SAMPLE_REPO optional")


def test_live_run_ends_with_a_draft_pr(clean_conn, tmp_path, monkeypatch):
    remote = os.environ["ORCH_LIVE_REMOTE"]
    src = Path(os.environ.get("SAMPLE_REPO") or SAMPLE_APP)
    origin = subprocess.run(["git", "-C", str(src), "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    assert not origin or remote.rstrip("/").removesuffix(".git") != origin.rstrip("/").removesuffix(".git"), \
        "ORCH_LIVE_REMOTE must be a scratch repo"
    repo = tmp_path / "sample-app"
    shutil.copytree(src, repo, symlinks=True,
                    ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "node_modules", "*.db"))
    git = ["git", "-C", str(repo), "-c", "user.name=orchestrator-live", "-c", "user.email=live@example.invalid"]
    if not (repo / ".git").exists():
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-qm", "sample-app"], check=True)
    subprocess.run([*git, "remote", "remove", "origin"], check=False)
    subprocess.run([*git, "remote", "add", "origin", remote], check=True)
    subprocess.run(["python3", "-I", str(PACK / "scripts" / "init_project.py"), "--root", str(repo), "--stack", "python",
                    "--no-ci", "--allow-partial-baseline"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "chore: sdlc init"], check=True)
    base = subprocess.run([*git, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
    subprocess.run([*git, "push", "-q", "-u", "origin", f"HEAD:{base}"], check=True)

    conn = clean_conn
    nodes.create(conn, kind="feature", slug="per-key-rate-limit", title="Per-key rate limit", repo="sample-app", created_by="@live")
    res = intake.contract_draft(conn, slug="rate-limit", title="Per-key rate limiting: 429 over 100 req/min per key",
                                repo="sample-app", actor="@live", feature="per-key-rate-limit",
                                ticket_text=(DATA / "rate-limit.ticket.md").read_text(),
                                touched_symbols=["app/ratelimit/", "app/auth.py"],
                                capabilities=json.loads((DATA / "rate-limit.capabilities.json").read_text()),
                                acceptance=json.loads((DATA / "rate-limit.acceptance.json").read_text()), tier="high",
                                contract_text=(DATA / "rate-limit.contract.md").read_text())
    assert res["status"] == "created" and res["blocks"]["missing"] == [], res
    assert transitions.transition(conn, "rate-limit", "ready", "@live")["ok"]

    monkeypatch.delenv("ORCHESTRATOR_EXECUTOR", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_CLAUDE", raising=False)
    cfg = config.Config(repo=repo, base=base, tick=30, max_runs=1, reviewer=os.environ.get("ORCH_LIVE_REVIEWER", "@reviewer"),
                        budget_usd=float(os.environ.get("ORCH_LIVE_BUDGET", "5")))
    deadline = time.monotonic() + 60 * float(os.environ.get("ORCH_LIVE_MINUTES", "90"))
    while time.monotonic() < deadline:
        out = loop.orchestrate("per-key-rate-limit", cfg, once=True)
        if out["done"] or out["last"]["escalations"]:
            break
        time.sleep(cfg.tick)
    print(json.dumps(out["last"], indent=1, default=str))
    assert nodes.get_row(conn, "rate-limit")["state"] == "in_review", out["last"]
    prs = evidence.list_for(conn, "rate-limit", "pr_url")
    assert prs and prs[-1]["payload"]["url"].startswith("https://github.com/")
