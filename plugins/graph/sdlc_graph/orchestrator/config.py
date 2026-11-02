"""Orchestrator options (one object passed through the loop, dispatch, gates and watchdog)."""

from __future__ import annotations

import os
import shlex
import sys
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PACKAGE_DIR = Path(__file__).resolve().parent
SDLC_GRAPH_ROOT = PACKAGE_DIR.parent.parent          # the graph plugin root (plugins/graph)
#: The sdlc plugin next to this one in the repository (plugins/sdlc), when it is there.
SDLC_PACK_ROOT = SDLC_GRAPH_ROOT.parent / "sdlc"
#: How Claude Code names this server's tools: ``mcp__graph__<tool>`` from a project .mcp.json,
#: ``mcp__plugin_graph_graph__<tool>`` when the graph plugin provides it, and the older ``sdlc_graph``
#: server name a hand-written .mcp.json may still use. Allow and deny lists carry every form.
TOOL_PREFIXES: tuple[str, ...] = ("mcp__graph__", "mcp__plugin_graph_graph__", "mcp__sdlc_graph__")
STEP_EXECUTOR = PACKAGE_DIR / "step_executor.sh"
FAKE_EXECUTOR = PACKAGE_DIR / "fake_executor.py"
FAKE_CLAUDE = PACKAGE_DIR / "fake_claude.py"


def _default_plugin_dirs() -> list[str]:
    env = os.environ.get("ORCHESTRATOR_PLUGIN_DIRS")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    dirs = []
    pack = SDLC_PACK_ROOT
    if pack.is_dir():
        dirs.append(str(pack))
    if (SDLC_GRAPH_ROOT / ".claude-plugin" / "plugin.json").is_file():
        dirs.append(str(SDLC_GRAPH_ROOT))
    return dirs


def executor_command(explicit: str | None = None) -> list[str]:
    """The executor argv prefix: ``--executor``, else ``ORCHESTRATOR_EXECUTOR`` (a shell-style
    command line, e.g. ``"python3 /path/fake_executor.py"``), else the bundled step executor."""
    text = explicit or os.environ.get("ORCHESTRATOR_EXECUTOR")
    if text:
        return shlex.split(text)
    return ["bash", str(STEP_EXECUTOR)]


def claude_command() -> list[str]:
    """The ``claude`` binary for the judge and the conformance reviewer (``ORCHESTRATOR_CLAUDE``
    substitutes a fake in tests)."""
    return shlex.split(os.environ.get("ORCHESTRATOR_CLAUDE") or "claude")


#: Environment variables a session (and a test command the orchestrator runs on its behalf) may inherit.
#: Everything else in the daemon's environment (database URLs, cloud and API credentials) stays out.
ENV_ALLOW = frozenset({"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TMPDIR", "TZ", "COLORTERM",
                       "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "CURL_CA_BUNDLE",
                       "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy",
                       "all_proxy", "SDLC_SCHEMA", "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "PYTHONUTF8"})
ENV_ALLOW_PREFIXES = ("LC_", "XDG_", "ANTHROPIC_", "CLAUDE_", "ORCH_", "ORCHESTRATOR_", "FAKE_")
#: Forwarded only to a step that pushes (ship): the GitHub credentials ``gh`` needs.
ENV_GITHUB = frozenset({"GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"})
_SECRET_NAME = re.compile(r"DATABASE_URL", re.I)


def clean_env(source: dict[str, str] | None = None, *, github: bool = False, extra: tuple[str, ...] = ()) -> dict[str, str]:
    """An allowlisted copy of the environment. ``extra`` names more variables to forward (the
    daemon's ``ORCHESTRATOR_ENV_PASSTHROUGH``, comma separated, is added automatically). A database URL is
    never forwarded here: the caller sets the ledger URL explicitly."""
    src = os.environ if source is None else source
    more = {n.strip() for n in (src.get("ORCHESTRATOR_ENV_PASSTHROUGH") or "").split(",") if n.strip()} | set(extra)
    out = {}
    for k, v in src.items():
        if _SECRET_NAME.search(k):
            continue
        if k in ENV_ALLOW or k.startswith(ENV_ALLOW_PREFIXES) or k in more or (github and k in ENV_GITHUB):
            out[k] = v
    return out


@dataclass
class Config:
    repo: Path                                   # the source checkout (worktrees go next to it)
    base: str | None = None                      # base branch; default origin/HEAD or the current branch
    tick: float = 60.0                           # seconds between ticks
    max_runs: int = 2                            # max_concurrent_runs across the feature
    stall_minutes: float = 30.0                  # no evidence/transition/log output for this long = stalled
    max_failures: int = 3                        # the third failure on a step escalates (P1)
    max_turns: int = 40                          # per executor session
    budget_usd: float = 5.0                      # per executor session (--max-budget-usd)
    max_cost_usd: float | None = 50.0            # per feature: spent + budget_usd per open run; None = unlimited (explicit only)
    reviewer: str = "@reviewer"                  # named human for the draft PR
    plugin_dirs: list[str] = field(default_factory=_default_plugin_dirs)
    executor: list[str] = field(default_factory=executor_command)
    executor_mode: str = "local"                 # local | runner (see orchestrator.auth); the credential is checked at orchestrate()
    conformance: bool = True                     # run the conformance reviewer after implement
    conformance_threshold: float = 0.8
    conformance_reentries: int = 2               # then escalate
    judge: bool = False                          # bounded Opus judge (off unless --judge)
    refinery: bool = False                       # goal is merged; merges only when GitHub reports approval
    log_root: Path | None = None                 # default <repo>/.claude/work/orchestrator
    python: str = sys.executable                 # interpreter for the sdlc-graph hooks in generated settings
    test_command: list[str] | None = None        # e.g. ["python3", "-m", "pytest", "-q"]: the orchestrator runs the acceptance tests itself
    require_test_command: bool = True            # refuse tests/verify when no test command can be found (the session's evidence alone is not proof)
    test_timeout: float = 600.0
    session_database_url: str | None = None      # ledger URL for sessions (a restricted role); default ORCHESTRATOR_SESSION_DATABASE_URL, else the daemon's
    guard: Callable[[], None] | None = field(default=None, repr=False)   # set by the loop: heartbeat + ownership check, raises LockLost

    def __post_init__(self) -> None:
        self.repo = Path(self.repo).resolve()
        if self.log_root is None:
            self.log_root = self.repo / ".claude" / "work" / "orchestrator"
        self.log_root = Path(self.log_root)
        if self.session_database_url is None:
            self.session_database_url = os.environ.get("ORCHESTRATOR_SESSION_DATABASE_URL") or None
        if self.executor_mode not in ("local", "runner"):
            raise ValueError(f"executor_mode must be 'local' or 'runner', not {self.executor_mode!r}")
        if self.max_runs < 1:
            raise ValueError("max_runs must be at least 1")
        if self.tick <= 0:
            raise ValueError("tick must be positive")
        if not 0 <= self.conformance_threshold <= 1:
            raise ValueError("conformance_threshold must be between 0 and 1")
