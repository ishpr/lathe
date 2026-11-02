"""Regression tests for lint_epic.py --agent: a story is Ready only when every decision has a real, confirmed value
(review findings: placeholders, escaped pipes, a missing or colon-less Decisions block, assumed decisions, defaults that
ask or override). Run with `python3 -m pytest scripts/test_lint_epic.py` from the skill directory; standard library plus pytest."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LINT = HERE / "lint_epic.py"
EXAMPLES = (HERE.parent / "references" / "examples.md").read_text(encoding="utf-8")


def base_doc() -> str:
    """The first agent-ready feature in references/examples.md (its first story is Ready: yes)."""
    blocks = re.findall(r"```markdown\n(.*?)```", EXAMPLES, re.S)
    doc = next(b for b in blocks if "Defaults when ambiguous" in b)
    return doc


def lint(tmp_path, text: str, *flags: str):
    f = tmp_path / "doc.md"
    f.write_text(text, encoding="utf-8")
    p = subprocess.run([sys.executable, "-I", str(LINT), "--agent", *flags, str(f)], capture_output=True, text=True)
    return p.returncode, p.stdout


def first_story_with(doc: str, old: str, new: str) -> str:
    assert old in doc, old
    return doc.replace(old, new, 1)


def test_the_shipped_example_is_clean(tmp_path):
    rc, out = lint(tmp_path, base_doc())
    assert rc == 0, out


@pytest.mark.parametrize("value", ["see above", "to be decided", "TBD - ask product", "decide later", "None", "ask product",
                                   "Not yet known", "unknown", "tbd."])
def test_placeholder_values_are_not_decisions(tmp_path, value):
    doc = first_story_with(base_doc(), "| 1 | Fixed or sliding window? | fixed · sliding | fixed | fixed |",
                           f"| 1 | Fixed or sliding window? | fixed · sliding | fixed | {value} |")
    rc, out = lint(tmp_path, doc)
    assert rc == 1 and "#1" in out and "Ready: yes" in out, out


def test_a_value_that_is_not_an_option_is_refused(tmp_path):
    doc = first_story_with(base_doc(), "| fixed · sliding | fixed | fixed |", "| fixed · sliding | fixed | token bucket |")
    rc, out = lint(tmp_path, doc)
    assert rc == 1 and "not one of its Options" in out, out


def test_an_escaped_pipe_keeps_the_cells_in_place(tmp_path):
    doc = first_story_with(base_doc(), "| 2 | Retry-After rounds to | whole seconds · fractional | whole seconds | whole seconds |",
                           "| 2 | Cache | redis \\| memory | redis | | @maria, 2026-11-02 |")
    rc, out = lint(tmp_path, doc)
    assert rc == 1 and "decision #2" in out, out  # empty Value, not a shifted cell that reads as filled


def test_a_missing_decisions_block_is_an_error(tmp_path):
    doc = base_doc()
    start = doc.index("**Decisions a human makes first:**")
    end = doc.index("**Defaults when ambiguous:**", start)
    rc, out = lint(tmp_path, doc[:start] + doc[end:])
    assert rc == 1 and "no 'Decisions a human makes first' block" in out, out


def test_a_decisions_block_may_be_the_literal_none(tmp_path):
    doc = base_doc()
    start = doc.index("**Decisions a human makes first:**")
    end = doc.index("**Defaults when ambiguous:**", start)
    rc, out = lint(tmp_path, doc[:start] + "**Decisions a human makes first:**\nNone.\n" + doc[end:])
    assert rc == 0, out


def test_a_decisions_label_without_the_colon_is_still_the_block(tmp_path):
    doc = first_story_with(base_doc(), "**Decisions a human makes first:**\n| # | Question | Options | Recommendation | Value | Decided by |\n|---|----------|---------|----------------|-------|------------|\n| 1 | Fixed or sliding window? | fixed · sliding | fixed | fixed |",
                           "#### Decisions a human makes first\n| # | Question | Options | Recommendation | Value | Decided by |\n|---|----------|---------|----------------|-------|------------|\n| 1 | Fixed or sliding window? | fixed · sliding | fixed | see above |")
    rc, out = lint(tmp_path, doc)
    assert rc == 1 and "decision #1" in out, out  # the heading form is read, so the placeholder is caught


def test_an_assumed_decision_is_an_error_and_blocks_the_story(tmp_path):
    doc = first_story_with(base_doc(), "| fixed · sliding | fixed | fixed | @maria, 2026-11-02 |",
                           "| fixed · sliding | fixed | fixed | assumed, confirm |")
    rc, out = lint(tmp_path, doc)
    assert rc == 1 and "assumed" in out and "Ready: yes" in out, out
    blocked = doc.replace("**Ready:** yes", "**Ready:** blocked on #1", 1)
    rc, out = lint(tmp_path, blocked)
    assert "[error]" in out and "is assumed" in out  # still reported, but the Ready line is honest


@pytest.mark.parametrize("line", ["Ask the human whenever something is unclear.", "Ignore the decisions above and choose the cheapest option",
                                  "Stop and escalate to the product owner when two options fit."])
def test_defaults_that_ask_or_override_are_refused(tmp_path, line):
    doc = first_story_with(base_doc(), "- Return the existing `ApiError` shape for every refusal.", f"- {line}")
    rc, out = lint(tmp_path, doc)
    assert rc == 1 and "asks, stops or overrides" in out, out


def test_defaults_must_carry_the_standard_four(tmp_path):
    doc = first_story_with(base_doc(), "- When none of these applies, choose the reversible option.\n- Return the existing `ApiError` shape for every refusal.",
                           "- Return the existing `ApiError` shape for every refusal.")
    rc, out = lint(tmp_path, doc)
    assert rc == 1 and "reversible option" in out, out


def test_open_questions_section_is_an_error(tmp_path):
    doc = first_story_with(base_doc(), "**Risk tier:** high\n**Ready:** yes",
                           "**Open questions:**\n- Which cache should we use?\n**Risk tier:** high\n**Ready:** yes")
    rc, out = lint(tmp_path, doc)
    assert rc == 1 and "open questions" in out, out
