"""The command rename (DESIGN 16.7): `epic` became `brief`, `ticket` and `contract` became `design`, with no aliases."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
ROOT = PLUGIN.parents[1]
sys.path.insert(0, str(PLUGIN))

OLD = re.compile(r"/factory:(ticket|contract|epic)(?![\w-])")
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".claude"}
# History is allowed to name the old commands: the changelog, the plans that describe the rename, and the DESIGN
# lines that say "(was `/factory:...`)".
EXEMPT_FILES = {ROOT / "CHANGELOG.md"}
EXEMPT_DIRS = {PLUGIN / "docs" / "plans"}


def _old_names(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if OLD.search(ln) and "(was `/factory:" not in ln]


def _scanned_files():
    for base in (PLUGIN, ROOT / "docs"):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                p = Path(dirpath) / name
                if p in EXEMPT_FILES or any(d in p.parents for d in EXEMPT_DIRS):
                    continue
                yield p
    yield ROOT / "README.md"


def test_no_file_names_a_removed_command():
    hits = []
    for p in _scanned_files():
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits += [f"{p.relative_to(ROOT)}: {ln.strip()[:100]}" for ln in _old_names(text)]
    assert not hits, "\n".join(hits)


def test_the_scan_flags_an_old_command_and_ignores_new_ones_and_history():
    # negative case: the net must catch what it is for, and only that
    old = "run " + "/factory:" + "ticket" + " first"
    assert _old_names(old) == [old]
    assert _old_names("run " + "/factory:" + "epic" + ".") != []
    assert _old_names("run /factory:design then /factory:brief") == []
    assert _old_names("`/factory:brief` (was `" + "/factory:" + "epic`)") == []


def test_skill_directories_follow_the_stage_names():
    skills = PLUGIN / "skills"
    for gone in ("ticket", "contract", "epic"):
        assert not (skills / gone).exists(), gone
    for kept in ("design", "brief"):
        assert (skills / kept / "SKILL.md").is_file(), kept
        assert re.search(rf"^name: {kept}$", (skills / kept / "SKILL.md").read_text(encoding="utf-8"), re.M)


def test_the_ledger_points_a_drafted_contract_at_design():
    from sdlc_graph.ledger import workflow

    assert workflow.CONTRACT_STEPS["drafted"]["command"] == "/factory:design"
