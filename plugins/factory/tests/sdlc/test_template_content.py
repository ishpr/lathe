"""Content checks on the shipped templates (CI gate, review workflow, evidence job, dogfood findings 25, 29-30, 32-34).

Plain text assertions, so the suite stays stdlib plus pytest; the YAML parse test runs when PyYAML is installed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TPL = Path(__file__).resolve().parents[2] / "templates"


def read(rel: str) -> str:
    return (TPL / rel).read_text(encoding="utf-8")


def step_block(text: str, name_fragment: str) -> str:
    """The text of the step whose `- name:` contains name_fragment, up to the next step."""
    parts = re.split(r"(?m)^      - ", text)
    for p in parts:
        if f"name: {name_fragment}" in p.split("\n", 1)[0] or p.startswith(
            f"name: {name_fragment}"
        ):
            return p
    raise AssertionError(f"no step named {name_fragment!r}")


@pytest.mark.parametrize(
    "rel",
    [
        "ci/python.yml",
        "ci/go.yml",
        "ci/node.yml",
        "ci/rust.yml",
        "ci/claude-review.yml",
        "ci/sdlc-evidence.yml",
        "github/dependabot.yml",
    ],
)
def test_templates_parse_as_yaml(rel):
    yaml = pytest.importorskip("yaml")
    text = read(rel).replace("__NODE_PM_SETUP__", "true")
    assert isinstance(yaml.safe_load(text), dict)


def test_gate_runs_the_configured_test_command_not_a_hardcoded_pytest():
    gate = read("ci/python.yml")
    tests = step_block(gate, "Tests")
    assert '["commands"]["test"]' in tests
    assert "pytest" not in tests
    assert "python -m pytest -q" not in gate


def test_gate_installs_project_before_tests_and_audits_and_pins_tools():
    gate = read("ci/python.yml")
    assert "__PY_INSTALL__" in gate
    order = [
        gate.index(s)
        for s in (
            "__PY_INSTALL__",
            "name: Ratchet",
            "name: Tests",
            "name: Dependency audit",
            "name: Licences are on the allowlist",
        )
    ]
    assert order == sorted(order)
    install = step_block(gate, "Install the project(s)")
    assert "tool_versions" in install and "==" in install


def test_gate_route_sweep_sits_between_removable_markers():
    gate = read("ci/python.yml")
    begin, end = gate.index("__ROUTES_BEGIN__"), gate.index("__ROUTES_END__")
    assert begin < gate.index("name: Route sweep") < gate.index("scripts/route_sweep.py") < end
    assert "uvicorn" not in gate[:begin] and "uvicorn" not in gate[end:]


@pytest.mark.parametrize(
    "rel",
    [
        "ci/python.yml",
        "ci/go.yml",
        "ci/node.yml",
        "ci/rust.yml",
        "ci/sdlc-evidence.yml",
    ],
)
def test_plugin_ref_is_a_pinned_sha_check(rel):
    text = read(rel)
    assert "ref: __MARKETPLACE_REF__" in text
    assert "[0-9a-f]{40}" in text
    assert not re.search(r"ref:\s*main\b", text)


def test_review_workflow_never_runs_pr_code_with_the_secret():
    wf = read("ci/claude-review.yml")
    body = "\n".join(l for l in wf.splitlines() if not l.lstrip().startswith("#"))
    assert re.search(r"(?m)^  pull_request_target:", body)
    assert not re.search(r"(?m)^  pull_request:", body)
    assert "id-token" not in body
    assert "github.event.pull_request.base.sha" in body
    assert "head.sha" not in body and "head.ref" not in body and "refs/pull" not in body
    # every checkout is of the base commit and keeps no credentials
    for m in re.finditer(
        r"actions/checkout@[^\n]*\n(?:\s+with:\n(?:\s+[^\n]*\n)*)?", body
    ):
        assert "base.sha" in m.group(0) and "persist-credentials: false" in m.group(0)
    assert "Bash" not in body  # read-only tools; no shell for the model


def test_evidence_secret_is_step_scoped_and_absent_from_plugin_install():
    wf = read("ci/sdlc-evidence.yml")
    job_env = wf.split("    env:\n", 1)[1].split("    steps:", 1)[0]
    assert "SDLC_DATABASE_URL" not in job_env
    holders = [
        p for p in re.split(r"(?m)^      - ", wf) if "secrets.SDLC_DATABASE_URL" in p
    ]
    assert len(holders) == 1
    assert "evidence add" in holders[0] and "pip install" not in holders[0]
    install = step_block(wf, "Install the plugin")
    assert "SDLC_DATABASE_URL" not in install


def test_licence_allowlist_ships_without_exceptions():
    text = read("docs/licence-allowlist.txt")
    assert not re.search(r"(?m)^exception\|", text)
    assert "psycopg2" not in text


def test_dependabot_has_a_placeholder_and_no_root_manifest_blocks():
    text = read("github/dependabot.yml")
    assert re.search(r"(?m)^  # __DEPENDABOT_ENTRIES__$", text)
    assert [
        m for m in re.findall(r'(?m)^  - package-ecosystem: "(\w[\w-]*)"', text)
    ] == ["github-actions"]


@pytest.mark.parametrize(
    "rel",
    [
        "CLAUDE.working-agreement.md",
        "docs/REVIEW.md",
        "pull_request_template.md",
        "rules/common.md",
        "rules/python.md",
        "rules/go.md",
        "rules/node.md",
        "rules/rust.md",
        "docs/day-one.md",
    ],
)
def test_conditional_blocks_are_balanced_and_named(rel):
    text = read(rel)
    opens = re.findall(r"<!-- sdlc:if (\w+) -->", text)
    assert set(opens) <= {"routes", "migrate", "codeowners"}
    assert len(opens) == len(re.findall(r"<!-- sdlc:end -->", text))


def test_app_specific_text_only_inside_conditional_blocks():
    def outside(text: str) -> str:
        return re.sub(
            r"<!-- sdlc:if \w+ -->.*?<!-- sdlc:end -->", "", text, flags=re.DOTALL
        )

    for rel in (
        "CLAUDE.working-agreement.md",
        "docs/REVIEW.md",
        "pull_request_template.md",
        "rules/common.md",
        "rules/python.md",
        "docs/day-one.md",
    ):
        rest = outside(read(rel))
        for word in (
            "alembic",
            "APIRouter",
            "request path",
            "api_key_auth",
            "public routes",
        ):
            assert word not in rest, (rel, word)


def test_review_md_lists_tier_high_paths_and_agreement_has_marker():
    assert "__TIER_HIGH_PATHS__" in read("docs/REVIEW.md")
    assert "{tier_high_paths}" in read("CLAUDE.working-agreement.md")


def test_db_roles_has_no_hardcoded_app_database():
    sql = read("docs/db-roles.sql")
    assert "appdb" not in sql and "__APP_DB__" in sql
