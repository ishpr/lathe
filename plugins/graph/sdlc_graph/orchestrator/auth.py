"""Where the executor's Claude comes from: executor mode, credential detection, the runner's refusal.

``local`` runs ``claude -p`` with whatever the engineer's machine has (usually the Claude Code login).
``runner`` is the same command, but it refuses to start unless the environment carries a credential
that is not the machine's subscription login. Both modes record ``auth_source`` on every run row.
Design: docs/DESIGN.md section 12.5; docs/orchestrator.md.
"""

from __future__ import annotations

import os
from typing import Mapping

EXECUTOR_MODES = ("local", "runner")
AUTH_SOURCES = ("subscription", "api_key", "oauth_token", "claude_platform_aws", "bedrock")

OAUTH_NOTE = "subscription token: tied to one person (the subscription of whoever ran `claude setup-token`)"

RUNNER_REFUSAL = (
    "--executor runner needs a credential that is not a machine's subscription login; none is set. Provide one of: "
    "(1) ANTHROPIC_API_KEY; "
    "(2) CLAUDE_CODE_OAUTH_TOKEN (allowed, but it is one person's subscription); "
    "(3) CLAUDE_CODE_USE_ANTHROPIC_AWS=1 with ANTHROPIC_AWS_WORKSPACE_ID (plus ANTHROPIC_AWS_API_KEY or ambient AWS "
    "credentials), for Claude Platform on AWS; "
    "(4) CLAUDE_CODE_USE_BEDROCK=1. "
    "Use --executor local to run on this machine's own Claude Code login."
)


def _set(env: Mapping[str, str], name: str) -> bool:
    return bool((env.get(name) or "").strip())


def detect_auth_source(env: Mapping[str, str] | None = None) -> str:
    """The credential the executor's ``claude`` will be given, from its environment. Precedence:
    ``ANTHROPIC_API_KEY`` (api_key), ``CLAUDE_CODE_OAUTH_TOKEN`` (oauth_token), Claude Platform on AWS
    (``CLAUDE_CODE_USE_ANTHROPIC_AWS=1`` plus ``ANTHROPIC_AWS_WORKSPACE_ID``), ``CLAUDE_CODE_USE_BEDROCK=1``
    (bedrock); otherwise the machine's Claude Code login (subscription)."""
    env = os.environ if env is None else env
    if _set(env, "ANTHROPIC_API_KEY"):
        return "api_key"
    if _set(env, "CLAUDE_CODE_OAUTH_TOKEN"):
        return "oauth_token"
    if (env.get("CLAUDE_CODE_USE_ANTHROPIC_AWS") or "").strip() == "1" and _set(env, "ANTHROPIC_AWS_WORKSPACE_ID"):
        return "claude_platform_aws"
    if (env.get("CLAUDE_CODE_USE_BEDROCK") or "").strip() == "1":
        return "bedrock"
    return "subscription"


def check_mode(mode: str, env: Mapping[str, str] | None = None) -> str:
    """Validate ``mode``; for ``runner`` refuse (ValueError, which the CLI turns into exit 2) when no
    non-subscription credential is present. Returns the detected ``auth_source``."""
    if mode not in EXECUTOR_MODES:
        raise ValueError(f"unknown executor mode {mode!r}; expected one of {', '.join(EXECUTOR_MODES)}")
    source = detect_auth_source(env)
    if mode == "runner" and source == "subscription":
        raise ValueError(RUNNER_REFUSAL)
    return source
