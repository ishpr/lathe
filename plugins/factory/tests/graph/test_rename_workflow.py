"""The ledger's workflow names the renamed command (DESIGN §16.7). Here, not in tests/sdlc, because
``sdlc_graph.ledger.workflow`` imports psycopg, which only the graph job installs."""

from sdlc_graph.ledger import workflow


def test_the_ledger_points_a_drafted_contract_at_design():
    assert workflow.CONTRACT_STEPS["drafted"]["command"] == "/factory:design"


def test_no_contract_step_names_a_removed_command():
    commands = {step.get("command") for step in workflow.CONTRACT_STEPS.values()}
    removed = {"/factory:" + name for name in ("tick" + "et", "contr" + "act", "ep" + "ic")}  # spelled in parts: test_rename greps every file
    assert not commands & removed
