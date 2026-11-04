"""The WRITTEN intent ledger: nodes, edges, transitions, evidence, decisions and runs.

Every write goes through a typed function here (and the MCP tools wrap these functions); each
state change is a guarded transition (``transitions``). Actors are strings:
``@handle`` (human), ``agent:<model alias>`` (an agent run), ``ci:<system>`` (CI).
"""

from pathlib import Path

LEDGER_DIR = Path(__file__).resolve().parent
STATE_MACHINES_PATH = LEDGER_DIR / "state_machines.json"
ROUTING_POLICY_PATH = LEDGER_DIR / "routing_policy.json"
LIFECYCLE_PATH = LEDGER_DIR / "lifecycle.json"
