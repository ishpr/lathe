"""Integrator tests: seams between owners that the per-owner suites did not cover."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sdlc_graph.cli import _split_targets, main

ROOT = Path(__file__).resolve().parents[2]


def test_split_targets_sorts_paths_and_symbol_keys():
    paths, symbols = _split_targets(["app/auth/api_key.py", "function:app.a.f@app/a.py"], None, ["module:app.b@app/b.py"])
    assert paths == ["app/auth/api_key.py"]
    assert symbols == ["module:app.b@app/b.py", "function:app.a.f@app/a.py"]
    assert _split_targets(None, None, None) == (None, None)


def test_query_impact_accepts_a_positional_path(clean_conn, synthetic_repo, capsys):
    """'sdlc-graph query impact app/routes/a.py' (the form the demo and README use) works like --paths."""
    from sdlc_graph.codegraph import snapshot

    snapshot.index(clean_conn, synthetic_repo, name="synthetic")
    assert main(["query", "impact", "app/routes/a.py", "--repo", "synthetic", "--json"]) == 0
    positional = json.loads(capsys.readouterr().out)
    assert main(["query", "impact", "--paths", "app/routes/a.py", "--repo", "synthetic", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == positional
    assert "tests/test_a.py::test_list_items" in positional["tests"]
    assert main(["query", "impact", "--repo", "synthetic", "--json"]) == 2  # nothing to start from


def test_demo_script_parses_and_show_views_exist():
    assert subprocess.run(["bash", "-n", str(ROOT / "demo" / "run_demo.sh")]).returncode == 0
    text = (ROOT / "demo" / "run_demo.sh").read_text()
    import importlib.util

    spec = importlib.util.spec_from_file_location("demo_show", ROOT / "demo" / "show.py")
    show = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(show)
    used = {line.split('"${SHOW[@]}" ')[1].split()[0] for line in text.splitlines() if '"${SHOW[@]}" ' in line}
    assert used and used <= set(show.VIEWS), used - set(show.VIEWS)
