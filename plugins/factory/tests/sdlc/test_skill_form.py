"""The loop skills are written in the DESIGN 18.4 form: goal, inputs, outputs (as a contract), guards and optional
hints, and nothing else, in about a hundred lines. A skill that needs more is carrying process that belongs to the
model, so each skill's body has a ceiling; the ceiling is the measured size plus a little room, never a target."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
SKILLS = PLUGIN / "skills"

#: The five sections of DESIGN 18.4, as the `##` headings each rewritten skill carries, in this order.
SECTIONS = ("Goal", "Inputs", "Outputs", "Guards", "Hints (non-binding)")

#: Body lines (after the frontmatter) each skill may hold. About 100 is the aim (DESIGN 18.4); design and brief carry
#: the most contract (design: four parsed artifacts and the ledger protocol; brief: the security epic's rules).
CEILING = {"brief": 125, "design": 160, "tests": 100, "implement": 100, "verify": 110, "ship": 95}


def body_of(text: str) -> str:
    """The text after the YAML frontmatter."""
    parts = text.split("---", 2)
    return parts[2] if text.startswith("---") and len(parts) == 3 else text


def form_problems(text: str, ceiling: int) -> list[str]:
    """What keeps a SKILL.md from the 18.4 form: a missing, repeated or out-of-order section, an extra `##` section,
    or a body over its ceiling. Empty when the skill is in form."""
    body = body_of(text)
    heads = re.findall(r"(?m)^## (.+?)\s*$", body)
    problems = []
    for name in SECTIONS:
        n = heads.count(name)
        if n != 1:
            problems.append(f"section '## {name}' appears {n} times")
    extra = [h for h in heads if h not in SECTIONS]
    if extra:
        problems.append(f"sections outside the form: {extra}")
    if not problems and heads != list(SECTIONS):
        problems.append(f"sections out of order: {heads}")
    lines = len(body.strip("\n").splitlines())
    if lines > ceiling:
        problems.append(f"{lines} body lines, ceiling {ceiling}")
    return problems


@pytest.mark.parametrize("name", sorted(CEILING))
def test_loop_skill_is_goal_inputs_outputs_guards_hints_under_its_ceiling(name):
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert form_problems(text, CEILING[name]) == [], name
    assert re.search(r"(?m)^name: " + re.escape(name) + "$", text.split("---", 2)[1]), name


@pytest.mark.parametrize("name", sorted(CEILING))
def test_loop_skill_hints_are_marked_non_binding_and_come_last(name):
    body = body_of((SKILLS / name / "SKILL.md").read_text(encoding="utf-8"))
    assert body.rstrip().rfind("## Hints (non-binding)") > body.find("## Guards") > body.find("## Outputs") >= 0, name


def _skill(sections, filler: int = 0) -> str:
    body = "".join(f"## {s}\n\ntext\n\n" for s in sections) + "line\n" * filler
    return f"---\nname: planted\nallowed-tools:\n  - Read\n---\n\n# Planted\n\n{body}"


def test_form_check_passes_a_skill_in_form():
    assert form_problems(_skill(SECTIONS), 40) == []


def test_form_check_rejects_a_missing_section_an_extra_one_the_wrong_order_and_a_long_body():
    # negative cases: the check must catch each way a skill drifts back into a script of steps
    no_guards = form_problems(_skill([s for s in SECTIONS if s != "Guards"]), 40)
    assert any("'## Guards' appears 0 times" in p for p in no_guards), no_guards
    steps = form_problems(_skill(list(SECTIONS) + ["Workflow"]), 40)
    assert any("outside the form" in p and "Workflow" in p for p in steps), steps
    swapped = form_problems(_skill(["Goal", "Outputs", "Inputs", "Guards", "Hints (non-binding)"]), 40)
    assert any("out of order" in p for p in swapped), swapped
    long = form_problems(_skill(SECTIONS, filler=200), 40)
    assert any("ceiling 40" in p for p in long), long
    twice = form_problems(_skill(list(SECTIONS) + ["Goal"]), 40)
    assert any("'## Goal' appears 2 times" in p for p in twice), twice
