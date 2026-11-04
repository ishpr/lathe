"""Init ends by suggesting epics from its assessments and starts the ones the human picks (headless: none)."""

from __future__ import annotations

import re
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2] / "skills"


def _flat(name: str) -> str:
    return re.sub(r"\s+", " ", (SKILLS / name / "SKILL.md").read_text(encoding="utf-8"))


def test_init_suggests_epics_and_starts_the_picked_ones_with_brief():
    text = _flat("init")
    assert "Suggested epics" in text
    assert "invoke the skill `factory:brief`" in text
    assert "multi-select" in text and "none for now" in text
    assert "AskUserQuestion" in (SKILLS / "init" / "SKILL.md").read_text(encoding="utf-8").split("---")[1]


def test_init_headless_lists_epics_but_starts_nothing():
    # negative case: an unattended run never kicks off work
    text = _flat("init")
    assert "**Headless:** list them in the summary and start nothing." in text


def test_assess_offers_the_extra_epics_in_the_same_question():
    text = _flat("assess")
    assert "Add up to two more suggested epics" in text and "in the same question" in text


def test_brief_offers_design_of_the_first_ready_story_and_headless_only_prints_it():
    text = _flat("brief")
    assert "offer the next lifecycle stage once" in text and "invoke the skill `factory:design`" in text
    assert "Headless: print the `/factory:design` line and stop." in text


def test_design_offers_to_continue_only_when_ready_and_never_headless():
    text = _flat("design")
    assert "Contract ready with no open decision, interactive: offer once to continue" in text
    assert "headless or not ready, print the next command only" in text
