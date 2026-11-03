"""The ``graph`` block of ``.claude/sdlc.json`` and where the connection string comes from.

``/factory:init`` writes the block (``scripts/init_project.py``; ``lib/sdlc.py`` validates it for the hooks)::

    "graph": {"enabled": true, "provider": "local", "url_env": "DATABASE_URL",
              "spanner": {"project": "", "instance": "", "database": ""}}

The connection string is never in that file or in git. :func:`resolve_url` takes the first of:

1. the environment variable named by ``url_env`` (the shell, or Claude Code's own ``env``), unless it holds the
   plugin's local default: the plugin's ``.mcp.json`` fills ``DATABASE_URL`` with that default for its server, so
   the default there counts as unset;
2. ``env.<url_env>`` in ``.claude/settings.local.json`` (gitignored);
3. ``<url_env>=...`` in ``.env`` (gitignored);
4. the environment value (the default), else :data:`DEFAULT_DATABASE_URL`.

Files are read on every call, so a URL that ``/factory:graph provision`` or ``/factory:graph url`` stored is used by
the running MCP server at its next tool call, without a restart. ``scripts/graph.py`` repeats this order in the
standard library (a test keeps the two in step).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

#: Same value as ``sdlc_graph.db.DEFAULT_DATABASE_URL`` (kept here so this module imports nothing).
DEFAULT_DATABASE_URL = "postgresql://sdlc:sdlc@localhost:5435/sdlc"
PROVIDERS: tuple[str, ...] = ("local", "neon", "aws", "spanner")
GRAPH_DEFAULTS: dict[str, Any] = {"enabled": True, "provider": "local", "url_env": "DATABASE_URL",
                                  "spanner": {"project": "", "instance": "", "database": ""}}
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
LOCAL_ENV_FILES: tuple[str, ...] = (os.path.join(".claude", "settings.local.json"), ".env")
OFF_LINE = "graph off: every graph step is skipped (/factory:graph on turns it back on)"


def project_dir(path: str | Path | None = None) -> Path:
    """``path``, else ``CLAUDE_PROJECT_DIR``, else the working directory."""
    return Path(path or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def defaults() -> dict[str, Any]:
    return json.loads(json.dumps(GRAPH_DEFAULTS))


def validate(block: Any) -> tuple[dict[str, Any], list[str]]:
    """(usable block, problems). A key of the wrong type falls back to its default and is reported."""
    out = defaults()
    if block is None:
        return out, []
    if not isinstance(block, dict):
        return out, ["graph must be an object"]
    problems: list[str] = []
    for key, value in block.items():
        if key == "enabled":
            if isinstance(value, bool):
                out[key] = value
            else:
                problems.append("graph.enabled must be true or false")
        elif key == "provider":
            if value in PROVIDERS:
                out[key] = value
            else:
                problems.append(f"graph.provider must be one of {', '.join(PROVIDERS)}")
        elif key == "url_env":
            if isinstance(value, str) and ENV_NAME.match(value):
                out[key] = value
            else:
                problems.append("graph.url_env must be an environment variable name (A-Z, 0-9, _)")
        elif key == "spanner":
            if isinstance(value, dict) and all(isinstance(value.get(k, ""), str) for k in ("project", "instance", "database")):
                out[key] = {**out["spanner"], **{k: v for k, v in value.items() if k in ("project", "instance", "database")}}
            else:
                problems.append("graph.spanner must be an object of strings (project, instance, database)")
        else:
            out[key] = value  # unknown keys are kept, not an error
    return out, problems


def load(path: str | Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """The project's graph block with defaults filled in, and its problems. No file or no block: the defaults."""
    cfg = project_dir(path) / ".claude" / "sdlc.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return defaults(), []
    except (OSError, ValueError) as exc:
        return defaults(), [f".claude/sdlc.json cannot be read as JSON ({exc})"]
    if not isinstance(data, dict):
        return defaults(), [".claude/sdlc.json: the top level must be a JSON object"]
    return validate(data.get("graph"))


def _from_settings_local(root: Path, name: str) -> str | None:
    try:
        data = json.loads((root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    env = data.get("env") if isinstance(data, dict) else None
    value = env.get(name) if isinstance(env, dict) else None
    return value if isinstance(value, str) and value else None


def _from_dotenv(root: Path, name: str) -> str | None:
    try:
        lines = (root / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m and m.group(1) == name:
            value = m.group(2)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            return value or None
    return None


def resolve_url(path: str | Path | None = None, environ: dict[str, str] | None = None) -> tuple[str, str]:
    """(url, source): source is ``env:<NAME>``, ``.claude/settings.local.json``, ``.env`` or ``default``."""
    env = os.environ if environ is None else environ
    root = project_dir(path)
    graph, _ = load(root)
    name = graph["url_env"]
    value = env.get(name) or ""
    if value and value != DEFAULT_DATABASE_URL:
        return value, f"env:{name}"
    stored = _from_settings_local(root, name)
    if stored:
        return stored, ".claude/settings.local.json"
    stored = _from_dotenv(root, name)
    if stored:
        return stored, ".env"
    if value:
        return value, f"env:{name}"
    if name != "DATABASE_URL" and env.get("DATABASE_URL"):
        return env["DATABASE_URL"], "env:DATABASE_URL"
    return DEFAULT_DATABASE_URL, "default"
