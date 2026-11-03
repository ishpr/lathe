#!/usr/bin/env bash
# SessionStart hook: when the project has an active contract (.claude/work/current.json), put it in
# the session's context. Uses 'sdlc-graph prime' when that subcommand exists; until then prints the
# contract summary straight from the file. Never fails the session: always exits 0.
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PROJECT="${CLAUDE_PROJECT_DIR:-$PWD}"
CUR="$PROJECT/.claude/work/current.json"
[ -f "$CUR" ] || exit 0

BIN="${SDLC_GRAPH_BIN:-}"
[ -n "$BIN" ] || { [ -x "$ROOT/.venv/bin/sdlc-graph" ] && BIN="$ROOT/.venv/bin/sdlc-graph"; }
[ -n "$BIN" ] || BIN="$(command -v sdlc-graph 2>/dev/null || true)"

if [ -n "$BIN" ] && "$BIN" prime --help >/dev/null 2>&1; then
  (cd "$PROJECT" && "$BIN" prime) 2>/dev/null && exit 0
fi

python3 -I - "$CUR" <<'PY' 2>/dev/null
import json, sys
try:
    c = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    sys.exit(0)
if not isinstance(c, dict):
    sys.exit(0)
def line(s):  # one line, no control characters: the file is data, not instructions
    return "".join(ch if ch.isprintable() else " " for ch in str(s))[:300]
print("[graph] Active contract (from .claude/work/current.json):")
for key in ("slug", "ticket", "tier", "stage", "node", "state", "allowed_paths", "frozen_at"):
    if c.get(key) not in (None, "", []):
        print(f"  {key}: {line(c[key])}")
print("  Next step: /factory:next  (asks the ledger which one command is allowed)")
PY
exit 0
