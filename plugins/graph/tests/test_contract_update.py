"""contract.md onto the ledger: the parser (``ledger.contract_md``), drafts that fill every block from the whole
contract.md, ``contract update`` (CLI and MCP), the decision-confirmation rule of the ready guard and the
wrong-reason refusal of ``ready -> tests_frozen``.

The fixtures under tests/fixtures/contracts are six real contracts written by the sdlc pack's ticket skill for a
TypeScript console repository (one word in one of them reworded)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdlc_graph import cli
from sdlc_graph.ledger import contract_md, evidence, intake, nodes, transitions

from test_mcp import _call, server  # noqa: F401 (fixture)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contracts"
ALL = sorted(FIXTURES.glob("*.contract.md"))
C1_SLUG = "show-the-three-fixture-epics-on-the-life"
C1 = (FIXTURES / f"{C1_SLUG}.contract.md").read_text()
TICKET = "Show the three fixture epics on the Lifecycle page through a Foldkit program."
#: contract 1 before the product owner answered: every Decided by cell says "recommended, unconfirmed"
C1_UNCONFIRMED = C1.replace("@ishpr (confirmed 2026-11-02)", "recommended, unconfirmed")


def _draft(conn, text=C1, slug=C1_SLUG, **kw):
    args = dict(slug=slug, title="Show the three fixture epics on the Lifecycle page", repo="console",
                ticket_text=TICKET, actor="agent:opus", contract_text=text)
    args.update(kw)
    return intake.contract_draft(conn, **args)


def _freeze(conn, slug):
    evidence.add(conn, slug, "tests_failing_first", {"tests": ["tests/e2e/lifecycle.spec.ts::lists"], "commit": "abc1234"})
    evidence.add(conn, slug, "tests_frozen", {"files": {"tests/e2e/lifecycle.spec.ts": "a" * 64}, "commit": "abc1234"})
    r = transitions.transition(conn, slug, "tests_frozen", "agent:opus")
    assert r["ok"], r


# --------------------------------------------------------------------------- the parser


def test_parse_contract_one_yields_every_block():
    p = contract_md.parse_contract(C1)
    assert p["missing"] == [] and p["optional_missing"] == [] and p["unknown"] == [] and p["warnings"] == []
    assert p["title"] == C1_SLUG and p["outcome"].startswith("An engineer opening `/lifecycle`")
    assert [r["id"] for r in p["acceptance_criteria"]] == ["AC1", "AC2", "AC3", "AC4", "AC5"]
    assert p["acceptance_criteria"][0]["name"] == "lifecycle lists the three fixture epics"
    assert [r["negative"] for r in p["acceptance_criteria"]] == [False, True, False, True, False]
    assert p["acceptance_lines"] == [{"kind": "shell", "command": "test -f tests/e2e/lifecycle.spec.ts", "expects": ["exit 0"]}]
    assert [d["n"] for d in p["decisions"]] == [1, 2, 3, 4, 5, 6]
    assert [d["n"] for d in p["decisions"] if d["confirmed"]] == [2, 4]
    assert {d["handle"] for d in p["decisions"] if d["confirmed"]} == {"@ishpr"}
    d2 = p["decisions"][1]
    # an escaped pipe inside a cell is a literal pipe, not a column break
    assert d2["value"] == "NEXT_PUBLIC_LATHE_DATA = mock | live, default mock"
    assert d2["options"] == ["NEXT_PUBLIC_LATHE_DATA = mock | live, default mock", "a runtime toggle in the user menu"]
    assert d2["decided_by"] == "@ishpr (confirmed 2026-11-02)"
    assert all(d["value"] for d in p["decisions"])
    assert len(p["defaults"]) == 11 and p["defaults"][:4] == transitions.defaults_lines({"defaults": p["defaults"]})[:4]
    caps = p["capabilities"]
    assert {k: len(v) for k, v in caps.items()} == {"paths": 9, "commands": 7, "egress": 2, "data": 0}
    assert caps["egress"] == ["registry.npmjs.org", "localhost:3100"] and caps["data"] == []
    assert "pnpm exec msw init public --no-save" in caps["commands"]
    assert caps["paths"] == p["allowed_paths"] and "src/app/(dashboard)/lifecycle/**" in p["allowed_paths"]
    assert p["tier"] == "low" and len(p["out_of_scope"]) == 5 and len(p["evidence_plan"]) == 4


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name.split(".")[0])
def test_every_console_contract_parses_without_missing_blocks(path):
    p = contract_md.parse_contract(path.read_text())
    assert p["missing"] == [] and p["unknown"] == [] and p["warnings"] == [], p["missing"]
    assert p["tier"] == "low" and p["acceptance_criteria"] and p["decisions"] and p["allowed_paths"]
    assert not any(d["confirmed"] for d in p["decisions"]) or path.name.startswith(C1_SLUG)
    touched, skipped = contract_md.touched_from_paths(p["allowed_paths"])
    assert touched and skipped == []


@pytest.mark.parametrize("cell,confirmed,handle", [
    ("@ishpr (confirmed 2026-11-02)", True, "@ishpr"),
    ("@pat", True, "@pat"),
    ("**@pat**", True, "@pat"),
    ("recommended, unconfirmed", False, None),
    ("assumed", False, None),
    ("", False, None),
    ("agent:opus", False, None),
    ("@agent:opus", False, None),
    ("pat@example.com", False, None),
    ("recommended by @pat, unconfirmed", False, "@pat"),
])
def test_decided_by_is_a_confirmation_only_with_a_human_handle(cell, confirmed, handle):
    got = contract_md.decision_handle(contract_md._plain(cell))
    assert got == (confirmed, handle)


def test_parser_tolerates_the_templates_wording():
    text = (FIXTURES.parent.parent.parent.parent / "sdlc" / "templates" / "contract.template.md")
    if not text.is_file():
        pytest.skip("the sdlc plugin's template is not next to this one")
    p = contract_md.parse_contract(text.read_text())
    # the template's placeholders are blanks: no Value, no handle, nothing confirmed
    assert p["decisions"] and all((d["value"], d["confirmed"], d["handle"]) == ("", False, None) for d in p["decisions"])
    assert p["decisions"][0]["n"] == 1                           # "D1" is decision 1
    assert p["defaults"][:4] == [
        "Make the smallest change that satisfies the acceptance criteria.",
        "Follow the nearest existing pattern in the code being changed.",
        "Prefer the option the Decisions above imply.",
        "When none applies, choose the reversible option."]
    assert "Per contract:" not in " ".join(p["defaults"])
    assert p["acceptance_lines"][0]["command"] == "python3 -m pytest tests/<area> -q"
    assert "## Risk tier (no tier: trivial, low, medium, high)" not in p["missing"]  # "<trivial | low ...>" reads trivial


def test_parser_reports_missing_blocks_and_none():
    p = contract_md.parse_contract("# Contract: x\n\n## Outcome\n\nA thing.\n\n## Decisions\n\nNone.\n\n"
                                   "## Capabilities\n\n- Paths: `app/**`\n- Egress: none.\n")
    assert p["decisions"] == [] and p["capabilities"] == {"paths": ["app/**"], "egress": []}
    assert p["missing"] == ["## Acceptance criteria", "## Defaults when ambiguous", "## Capabilities (commands, data)",
                            "## Allowed paths", "## Risk tier"]
    assert p["optional_missing"] == ["## Acceptance", "## Out of scope", "## Evidence plan"]
    bad = contract_md.parse_contract("## Decisions\n\n| # | Question |\n|---|---|\n| 1 | q |\n")
    assert "## Decisions (no Value column)" in bad["missing"]
    short = contract_md.parse_contract("## Decisions\n\n| # | Question | Value | Decided by |\n|---|---|---|---|\n"
                                       "| 1 | a | b | c | @x |\n")
    assert short["warnings"] and short["decisions"][0]["n"] == 1


def test_touched_from_paths_reduces_globs_to_their_directory():
    touched, skipped = contract_md.touched_from_paths(
        ["src/lib/api/**", "./src/app/(dashboard)/lifecycle/**", "src/app/[id]/page.tsx", "src/**/x.ts", "**", "a b/c"])
    assert touched == ["src/lib/api/", "src/app/(dashboard)/lifecycle/", "src/app/[id]/page.tsx", "src/"]
    assert skipped == ["**", "a b/c"]


# --------------------------------------------------------------------------- draft from the whole contract.md


def test_draft_from_contract_one_fills_every_block_and_ready_passes_at_tier_low(clean_conn):
    out = _draft(clean_conn)
    assert out["status"] == "created" and out["blocks"]["missing"] == [] and out["blocks"]["unknown"] == [], out
    n = out["node"]
    assert n["touched_symbols"] == ["src/lib/api/", "src/lib/foldkit/", "src/lib/hooks/", "src/programs/lifecycle/",
                                    "src/components/pages/lifecycle/", "src/app/(dashboard)/lifecycle/",
                                    "src/app/providers.tsx", "public/mockServiceWorker.js", "tests/e2e/lifecycle.spec.ts"]
    assert set(n["capabilities"]) == {"paths", "commands", "egress", "data"} and len(n["capabilities"]["paths"]) == 9
    assert len(n["attrs"]["defaults"]) == 11 and n["tier"] == "low"
    assert [(r["id"], r["source"]) for r in n["acceptance"]] == [(f"AC{i}", "contract") for i in range(1, 6)]
    assert [d["n"] for d in n["attrs"]["decisions"]] == [1, 2, 3, 4, 5, 6]
    assert n["attrs"]["acceptance_lines"][0]["command"] == "test -f tests/e2e/lifecycle.spec.ts"
    assert n["contract_md"] == C1 and nodes.get_row(clean_conn, C1_SLUG)["contract_md"] == C1
    # four decisions are recommended, unconfirmed: fine at tier low. The commands and egress need a human.
    r = transitions.transition(clean_conn, C1_SLUG, "ready", "agent:opus")
    assert not r["ok"] and r["missing"] == ["capabilities_approved"]
    r = transitions.transition(clean_conn, C1_SLUG, "ready", "@ishpr")
    assert r["ok"], r


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name.split(".")[0])
def test_every_console_contract_drafts_strictly_and_is_ready_for_a_human(clean_conn, path):
    slug = path.name.split(".")[0]
    out = _draft(clean_conn, text=path.read_text(), slug=slug, title=f"Console contract {slug}", strict=True)
    assert out["status"] == "created" and out["blocks"]["missing"] == [], out
    assert transitions.transition(clean_conn, slug, "ready", "@ishpr")["ok"]


def test_explicit_arguments_win_over_the_text(clean_conn):
    out = _draft(clean_conn, touched_symbols=["src/lib/api/"], tier="high",
                 capabilities={"egress": []}, acceptance=[{"id": "AC1", "text": "lists three epics", "source": "ticket"}],
                 defaults=["Make the smallest change that satisfies the acceptance criteria.",
                           "Follow the nearest existing pattern in the code being changed.",
                           "Prefer the option the Decisions above imply.",
                           "When none applies, choose the reversible option."])
    n = out["node"]
    assert n["touched_symbols"] == ["src/lib/api/"] and n["tier"] == "high" and n["capabilities"]["egress"] == []
    assert len(n["capabilities"]["commands"]) == 7 and len(n["attrs"]["defaults"]) == 4
    assert n["acceptance"] == [{"id": "AC1", "text": "lists three epics", "source": "ticket"}]
    # tier high: decisions 1, 3, 5 and 6 are unconfirmed, so even a human may not mark it ready
    r = transitions.transition(clean_conn, C1_SLUG, "ready", "@ishpr")
    assert not r["ok"] and r["missing"] == ["decisions_resolved"]


def test_strict_draft_refuses_missing_blocks_and_creates_nothing(clean_conn):
    text = C1.split("## Allowed paths")[0]          # no Allowed paths, Risk tier or Evidence plan
    text = text.replace("## Defaults when ambiguous", "## Notes")
    out = _draft(clean_conn, text=text, strict=True)
    assert out["status"] == "refused" and out["node"] is None
    assert out["blocks"]["missing"] == ["## Defaults when ambiguous", "## Risk tier"]
    assert "nothing was created" in out["error"]
    with pytest.raises(LookupError):
        nodes.get_row(clean_conn, C1_SLUG)
    # strict with no contract.md at all: the draft the ticket skill sent is refused too
    out = intake.contract_draft(clean_conn, slug="bare", title="A bare draft", repo="console", ticket_text=TICKET,
                                actor="agent:opus", acceptance=[{"id": "AC1", "text": "x"}], strict=True)
    assert out["status"] == "refused"
    assert out["blocks"]["missing"] == ["## Defaults when ambiguous", "## Capabilities (paths, commands, egress, data)"]


def test_a_draft_that_repeats_a_slug_still_raises_and_update_is_the_way(clean_conn):
    import psycopg

    _draft(clean_conn)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _draft(clean_conn, title="Another title for the same contract")


# --------------------------------------------------------------------------- contract update


def test_update_of_a_bare_draft_attaches_every_block_and_ready_passes(clean_conn, capsys, tmp_path):
    # the draft the ticket skill made: slug, title, ticket text and acceptance only
    intake.contract_draft(clean_conn, slug=C1_SLUG, title="Show the three fixture epics on the Lifecycle page",
                          repo="console", ticket_text=TICKET, actor="agent:opus",
                          acceptance=[{"id": f"AC{i}", "text": f"criterion {i}", "source": "ticket"} for i in range(1, 6)])
    r = transitions.transition(clean_conn, C1_SLUG, "ready", "@ishpr")
    assert not r["ok"] and {"allowed_paths_present", "defaults_present", "capabilities_present"} <= set(r["missing"])
    f = tmp_path / "contract.md"
    f.write_text(C1)
    code = cli.main(["contract", "update", C1_SLUG, "--from-file", str(f), "--actor", "agent:opus", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["status"] == "updated", out
    assert out["changed"] == ["touched_symbols", "capabilities", "defaults", "acceptance", "tier", "decisions",
                              "acceptance_lines", "contract_md"]
    assert out["decisions_confirmed"] == [2, 4] and out["blocks"]["missing"] == []
    row = nodes.get_row(clean_conn, C1_SLUG)
    assert row["tier"] == "low" and len(row["touched_symbols"]) == 9 and row["contract_md"] == C1
    assert [r["source"] for r in row["acceptance"]] == ["contract"] * 5
    ev = evidence.list_for(clean_conn, C1_SLUG, kind="contract_updated")
    assert len(ev) == 1 and ev[0]["id"] == out["evidence_id"]
    assert ev[0]["payload"]["decisions_confirmed"] == [2, 4] and ev[0]["payload"]["actor"] == "agent:opus"
    assert transitions.transition(clean_conn, C1_SLUG, "ready", "@ishpr")["ok"]


def test_update_records_the_product_owners_confirmations(clean_conn):
    _draft(clean_conn, text=C1_UNCONFIRMED)
    assert not any(d["confirmed"] for d in nodes.get_row(clean_conn, C1_SLUG)["attrs"]["decisions"])
    out = intake.contract_update(clean_conn, C1_SLUG, C1, actor="agent:opus")
    assert out["status"] == "updated" and out["changed"] == ["decisions", "contract_md"]
    assert out["decisions_confirmed"] == [2, 4]
    stored = nodes.get_row(clean_conn, C1_SLUG)["attrs"]["decisions"]
    assert [d["n"] for d in stored if d["confirmed"]] == [2, 4] and stored[1]["handle"] == "@ishpr"
    # the same text again changes nothing and records nothing
    again = intake.contract_update(clean_conn, C1_SLUG, C1, actor="agent:opus")
    assert again["status"] == "unchanged" and again["changed"] == [] and again["evidence_id"] is None
    assert len(evidence.list_for(clean_conn, C1_SLUG, kind="contract_updated")) == 1


def test_feed_shows_the_contract_update(clean_conn):
    from sdlc_graph.orchestrator import feed

    _draft(clean_conn, text=C1_UNCONFIRMED)
    out = intake.contract_update(clean_conn, C1_SLUG, C1, actor="agent:opus")
    rows = feed.feed(clean_conn)
    assert any(r["kind"] == "evidence" and f"contract_updated #{out['evidence_id']}" in r["what"] for r in rows), rows


def test_tier_medium_needs_every_decision_confirmed_and_an_update_supplies_them(clean_conn):
    medium = C1_UNCONFIRMED.replace("\nlow — ", "\nmedium — ")
    _draft(clean_conn, text=medium)
    assert nodes.get_row(clean_conn, C1_SLUG)["tier"] == "medium"
    r = transitions.transition(clean_conn, C1_SLUG, "ready", "@ishpr")
    assert not r["ok"] and r["missing"] == ["decisions_resolved"]
    # the owner confirms two of six: still not ready
    intake.contract_update(clean_conn, C1_SLUG, C1.replace("\nlow — ", "\nmedium — "), actor="agent:opus")
    assert transitions.transition(clean_conn, C1_SLUG, "ready", "@ishpr")["missing"] == ["decisions_resolved"]
    # an agent writing its own label into Decided by confirms nothing
    agent = medium.replace("recommended, unconfirmed", "agent:opus")
    out = intake.contract_update(clean_conn, C1_SLUG, agent, actor="agent:opus")
    assert out["decisions_confirmed"] == []
    assert transitions.transition(clean_conn, C1_SLUG, "ready", "@ishpr")["missing"] == ["decisions_resolved"]
    every = medium.replace("recommended, unconfirmed", "@ishpr (confirmed 2026-11-02)")
    out = intake.contract_update(clean_conn, C1_SLUG, every, actor="@ishpr")
    assert out["decisions_confirmed"] == [1, 2, 3, 4, 5, 6]
    assert transitions.transition(clean_conn, C1_SLUG, "ready", "@ishpr")["ok"]


def test_update_is_refused_from_tests_frozen_on(clean_conn, capsys, tmp_path):
    _draft(clean_conn)
    assert transitions.transition(clean_conn, C1_SLUG, "ready", "@ishpr")["ok"]
    _freeze(clean_conn, C1_SLUG)
    with pytest.raises(PermissionError, match="tests_frozen"):
        intake.contract_update(clean_conn, C1_SLUG, C1_UNCONFIRMED, actor="@ishpr")
    f = tmp_path / "contract.md"
    f.write_text(C1)
    assert cli.main(["contract", "update", C1_SLUG, "--from-file", str(f), "--actor", "@ishpr", "--json"]) == 2
    capsys.readouterr()
    assert evidence.list_for(clean_conn, C1_SLUG, kind="contract_updated") == []


def test_on_a_ready_contract_an_agent_may_update_decisions_only(clean_conn):
    _draft(clean_conn, text=C1_UNCONFIRMED)
    assert transitions.transition(clean_conn, C1_SLUG, "ready", "@ishpr")["ok"]
    wider = C1_UNCONFIRMED.replace("- Egress: `registry.npmjs.org` (install only)", "- Egress: `evil.example`, `registry.npmjs.org` (install only)")
    with pytest.raises(PermissionError, match="capabilities"):
        intake.contract_update(clean_conn, C1_SLUG, wider, actor="agent:opus")
    assert "evil.example" not in nodes.get_row(clean_conn, C1_SLUG)["capabilities"]["egress"]
    out = intake.contract_update(clean_conn, C1_SLUG, C1, actor="agent:opus")
    assert out["status"] == "updated" and out["decisions_confirmed"] == [2, 4]
    assert intake.contract_update(clean_conn, C1_SLUG, wider.replace("recommended, unconfirmed", "x"), actor="@ishpr")["status"] == "updated"


def test_update_refuses_a_capability_the_validator_refuses_and_keeps_missing_blocks(clean_conn):
    _draft(clean_conn)
    with pytest.raises(ValueError):
        intake.contract_update(clean_conn, C1_SLUG, C1.replace("`localhost:3100`", "`not a host!`"), actor="agent:opus")
    no_tier = C1.split("## Risk tier")[0] + "## Evidence plan\n\n- tests\n"
    out = intake.contract_update(clean_conn, C1_SLUG, no_tier, actor="agent:opus")
    assert "## Risk tier" in out["blocks"]["missing"] and out["node"]["tier"] == "low"   # the old value stays


def test_update_keeps_a_rows_bound_test(clean_conn):
    _draft(clean_conn)
    acc = nodes.get_row(clean_conn, C1_SLUG)["acceptance"]
    acc[0]["test"] = "tests/e2e/lifecycle.spec.ts::lists"
    clean_conn.execute("UPDATE node SET acceptance = %s WHERE slug = %s", (json.dumps(acc), C1_SLUG))
    intake.contract_update(clean_conn, C1_SLUG, C1.replace("exactly 3 items", "exactly three items"), actor="agent:opus")
    row = nodes.get_row(clean_conn, C1_SLUG)["acceptance"][0]
    assert row["test"] == "tests/e2e/lifecycle.spec.ts::lists" and "exactly three items" in row["text"]


def test_update_applies_the_overlap_rule(clean_conn):
    _draft(clean_conn)
    other = (FIXTURES / "render-the-interactive-view-helpers-with.contract.md").read_text()
    assert _draft(clean_conn, text=other, slug="views", title="Interactive view helpers")["status"] == "created"
    out = intake.contract_update(clean_conn, "views", other.replace("- `src/lib/design/**`", "- `src/lib/api/**`"),
                                 actor="agent:opus")
    assert out["status"] == "conflict" and out["conflicts"][0]["slug"] == C1_SLUG
    assert nodes.get_row(clean_conn, "views")["touched_symbols"] == ["src/programs/_ui/", "src/lib/design/"]


def test_update_overlap_is_excused_by_a_depends_on_edge_in_either_direction(clean_conn):
    # the console ledger: bare drafts first, then the story that depends on them; each update attaches its paths
    deps = {"decode-every-ledger-resource-through-typ": "Decode every ledger resource through typed endpoints",
            "generate-a-page-program-that-passes-its": "Generate a page program from the template",
            "serve-fixtures-that-tell-the-northwind-l": "Serve fixtures that tell the Northwind story"}
    rows = [{"id": "AC1", "text": "criterion 1", "source": "ticket"}]
    for slug, title in deps.items():
        assert intake.contract_draft(clean_conn, slug=slug, title=title, repo="console", ticket_text=f"{title}.",
                                     actor="agent:opus", acceptance=rows)["status"] == "created"
    assert intake.contract_draft(clean_conn, slug=C1_SLUG, title="Show the three fixture epics on the Lifecycle page",
                                 repo="console", ticket_text=TICKET, actor="agent:opus", acceptance=rows,
                                 depends_on=list(deps))["status"] == "created"
    text = {slug: (FIXTURES / f"{slug}.contract.md").read_text() for slug in [C1_SLUG, *deps]}
    assert intake.contract_update(clean_conn, C1_SLUG, text[C1_SLUG], actor="@ishpr")["status"] == "updated"
    # the dependencies overlap the story that depends on them: its edge orders them, so the update goes through
    for slug in ("decode-every-ledger-resource-through-typ", "generate-a-page-program-that-passes-its"):
        out = intake.contract_update(clean_conn, slug, text[slug], actor="@ishpr")
        assert out["status"] == "updated", out
    # two siblings on src/lib/api/ with no edge between them are still a conflict, and nothing is written
    serve = "serve-fixtures-that-tell-the-northwind-l"
    out = intake.contract_update(clean_conn, serve, text[serve], actor="@ishpr")
    assert out["status"] == "conflict" and [c["slug"] for c in out["conflicts"]] == ["decode-every-ledger-resource-through-typ"]
    assert nodes.get_row(clean_conn, serve)["touched_symbols"] == []
    nodes.link(clean_conn, serve, "decode-every-ledger-resource-through-typ", "depends_on")
    assert intake.contract_update(clean_conn, serve, text[serve], actor="@ishpr")["status"] == "updated"
    for slug in text:
        r = transitions.transition(clean_conn, slug, "ready", "@ishpr")
        assert r["ok"], (slug, r)


# --------------------------------------------------------------------------- wrong-reason refusal, end to end


def test_tests_frozen_is_refused_for_a_test_that_fails_for_the_wrong_reason(clean_conn):
    _draft(clean_conn)
    assert transitions.transition(clean_conn, C1_SLUG, "ready", "@ishpr")["ok"]
    evidence.add(clean_conn, C1_SLUG, "tests_failing_first", {"tests": [
        {"id": "tests/e2e/lifecycle.spec.ts::lists", "reason": "assertion"},
        {"id": "tests/e2e/lifecycle.spec.ts::errors", "reason": "import_error"}], "commit": "abc1234"})
    evidence.add(clean_conn, C1_SLUG, "tests_frozen", {"files": {"tests/e2e/lifecycle.spec.ts": "a" * 64}, "commit": "abc1234"})
    r = transitions.transition(clean_conn, C1_SLUG, "tests_frozen", "agent:opus")
    assert not r["ok"]
    assert r["missing"] == ["failing_first_reasons",
                            "test tests/e2e/lifecycle.spec.ts::errors fails for the wrong reason: import_error"]
    evidence.add(clean_conn, C1_SLUG, "tests_failing_first", {"tests": [
        {"id": "tests/e2e/lifecycle.spec.ts::lists", "reason": "assertion"},
        {"id": "tests/e2e/lifecycle.spec.ts::errors", "reason": "not_implemented"}], "commit": "abc1234"})
    assert transitions.transition(clean_conn, C1_SLUG, "tests_frozen", "agent:opus")["ok"]


# --------------------------------------------------------------------------- MCP round trip


def test_mcp_draft_update_and_refusals_round_trip(server, clean_conn):  # noqa: F811
    base = {"slug": C1_SLUG, "title": "Show the three fixture epics on the Lifecycle page", "repo": "console",
            "ticket_text": TICKET}
    is_error, out = _call(server, "contract_draft", {**base, "contract_md": C1.split("## Allowed paths")[0]})
    assert not is_error and out["status"] == "refused" and "## Risk tier" in out["blocks"]["missing"], out
    is_error, out = _call(server, "node_get", {"slug": C1_SLUG})
    assert out["ok"] is False and out["error"]["type"] == "not_found"
    is_error, out = _call(server, "contract_draft", {**base, "contract_md": C1_UNCONFIRMED})
    assert not is_error and out["status"] == "created" and out["blocks"]["missing"] == [], out
    assert out["node"]["created_by"] == "agent:mcp" and out["node"]["tier"] == "low"
    is_error, out = _call(server, "contract_update", {"slug": C1_SLUG, "contract_md": C1})
    assert not is_error and out["status"] == "updated" and out["decisions_confirmed"] == [2, 4], out
    ev = evidence.list_for(clean_conn, C1_SLUG, kind="contract_updated")
    assert ev[0]["payload"]["actor"] == "agent:mcp" and ev[0]["source"] == "local"
    # the MCP server acts as an agent: it may not mark the contract ready (commands need a human)
    is_error, out = _call(server, "contract_transition", {"slug": C1_SLUG, "to_state": "ready"})
    assert out["ok"] is False and out["missing"] == ["capabilities_approved"]
    assert transitions.transition(clean_conn, C1_SLUG, "ready", "@ishpr")["ok"]
    _freeze(clean_conn, C1_SLUG)
    is_error, out = _call(server, "contract_update", {"slug": C1_SLUG, "contract_md": C1_UNCONFIRMED})
    assert out["ok"] is False and out["error"]["type"] == "forbidden" and "tests_frozen" in out["error"]["message"]
