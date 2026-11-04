#!/usr/bin/env bash
# build.sh: build a stage-eval fixture in the current directory (an eval case's scaffold calls it).
#
# Usage: build.sh <contract> <state> [--plant scope] [--remote]
#   contract  a directory under contracts/ (contract.md, current.json, golden_tests/, skeleton/, golden_impl/)
#   state     ready        the sample app, the pack (init, graph off), the contract and current.json on a branch
#             tests_frozen + the golden tests and any skeleton committed as test(<slug>), failing first, frozen
#             implemented  + the golden implementation, uncommitted (what /factory:implement leaves)
#             verified     + a green verify stamp (.claude/work/last_verify.json)
#   --plant scope  after the state is built, leave an uncommitted edit outside the allowed paths
#                  (app/routes/admin.py); with `verified` it lands before the stamp
#   --remote       a bare `origin` inside .git/ (so it is never part of the tree or the fingerprint), with main pushed
#
# Writes .eval/fixture.json (gitignored): the state, the base commit, main's sha, and the HEAD the stage starts from;
# the outcome checks (check.py) read it.
#
# Needs: git, and python3 3.11+ with the sample app's dependencies (fastapi, sqlalchemy, httpx, pytest) first on the
# PATH (the baseline harness builds one at .demo/eval-venv). Exit 0 built, 2 misuse or a missing prerequisite.
set -euo pipefail

die() { echo "build.sh: $*" >&2; exit 2; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="${FACTORY_PLUGIN_ROOT:-$(cd "$HERE/../../.." && pwd)}"   # plugins/factory
SAMPLE="${SAMPLE_APP:-$(cd "$P/../.." && pwd)/examples/sample-app}"
[ $# -ge 2 ] || die "usage: build.sh <contract> <state> [--plant scope] [--remote]"
NAME="$1"; STATE="$2"; shift 2
PLANT=""; REMOTE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --plant) [ $# -ge 2 ] || die "--plant needs a kind"; PLANT="$2"; shift 2 ;;
    --remote) REMOTE=1; shift ;;
    *) die "unknown argument $1" ;;
  esac
done
C="$HERE/contracts/$NAME"
[ -f "$C/contract.md" ] && [ -f "$C/current.json" ] || die "no contract $NAME under $HERE/contracts"
case "$STATE" in ready|tests_frozen|implemented|verified) ;; *) die "unknown state $STATE" ;; esac
[ -f "$P/scripts/init_project.py" ] || die "plugin root not found at $P (set FACTORY_PLUGIN_ROOT)"
[ -d "$SAMPLE/app" ] || die "sample app not found at $SAMPLE (set SAMPLE_APP)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null || die "python3 3.11+ is required"
python3 -c 'import fastapi, sqlalchemy, httpx, pytest' 2>/dev/null \
  || die "python3 lacks the sample app's dependencies: put a virtualenv with them first on the PATH (scripts/baseline.py prepare builds .demo/eval-venv)"
[ ! -e .git ] && [ ! -e app ] || die "the current directory already holds a repository or an app: $(pwd)"

export PYTHONDONTWRITEBYTECODE=1 GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME="Eval Fixture" GIT_AUTHOR_EMAIL=fixture@example.com
export GIT_COMMITTER_NAME="Eval Fixture" GIT_COMMITTER_EMAIL=fixture@example.com
WS="$(pwd)"
SLUG="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["slug"])' "$C/current.json")"

# 1. The sample app on main, then the pack (graph off: no database or MCP server in an eval), committed on main.
(cd "$SAMPLE" && tar cf - --exclude ./.venv --exclude ./node_modules --exclude '*.db' --exclude __pycache__ --exclude .pytest_cache .) | tar xf -
git init -q -b main
git config user.name "Eval Agent"; git config user.email agent@example.com; git config commit.gpgsign false
git add -A && git commit -qm "chore: the sample app"
python3 -I "$P/scripts/init_project.py" --root "$WS" --codeowners '@alice,@maria' --no-ci --graph off > /dev/null 2>&1 \
  || die "init_project.py failed (rerun it in $WS to see why)"
# The eval's python has no `uv run` environment: run the stack's tools through the python3 on the PATH.
python3 - <<'EOF'
import json
p = ".claude/sdlc.json"
c = json.load(open(p))
cmd = c["commands"]
cmd["test"] = "python3 -m pytest -q"
cmd["setup"] = ""
for k in ("lint_fix", "format", "lint_report", "typecheck"):
    if cmd.get(k, "").startswith("uv run "):
        cmd[k] = "python3 -m " + cmd[k][len("uv run "):]
json.dump(c, open(p, "w"), indent=2)
open(p, "a").write("\n")
EOF
python3 -I "$P/scripts/ratchet.py" --root "$WS" --update > /dev/null 2>&1 || die "ratchet --update failed"
printf '.eval/\n' >> .gitignore
git add -A && git commit -qm "chore: the factory pack"
BASE="$(git rev-parse HEAD)"
if [ "$REMOTE" -eq 1 ]; then
  git init -q --bare .git/eval-origin.git
  git remote add origin "$WS/.git/eval-origin.git"
  git push -q origin main
fi

# 2. The contract on its branch (what /factory:design leaves).
mkdir -p .eval
git switch -qc "feat/$SLUG"
mkdir -p ".claude/work/$SLUG"
cp "$C/contract.md" ".claude/work/$SLUG/contract.md"
cp "$C/current.json" .claude/work/current.json

# 3. The state the stage starts from.
if [ "$STATE" != ready ]; then
  cp -R "$C/golden_tests/." .
  [ -d "$C/skeleton" ] && cp -R "$C/skeleton/." .
  TESTS=$(cd "$C/golden_tests" && find . -type f -name 'test_*.py' -not -path '*/__pycache__/*' | sed 's#^\./##' | sort)
  git add -- $TESTS
  [ -d "$C/skeleton" ] && git add -- $(cd "$C/skeleton" && find . -type f -not -path '*/__pycache__/*' -not -name '*.pyc' | sed 's#^\./##')
  git commit -qm "test($SLUG): the contract's acceptance criteria"
  # shellcheck disable=SC2086
  python3 -I "$P/scripts/tests_failing_first.py" --root "$WS" $TESTS > .eval/ff.log 2>&1 || { cat .eval/ff.log >&2; die "the golden tests do not fail first for the intended reason"; }
  # shellcheck disable=SC2086
  python3 -I "$P/scripts/freeze_tests.py" --root "$WS" --freeze $TESTS > /dev/null || die "freeze failed"
fi
if [ "$STATE" = implemented ] || [ "$STATE" = verified ]; then
  cp -R "$C/golden_impl/." .
fi
if [ "$PLANT" = scope ]; then
  printf '\n# Planted by the eval fixture: an edit outside the contract'"'"'s allowed paths.\nADMIN_BANNER = "maintenance"\n' >> app/routes/admin.py
elif [ -n "$PLANT" ]; then
  die "unknown plant $PLANT"
fi
if [ "$STATE" = verified ]; then
  python3 -I "$P/scripts/verify.py" --root "$WS" > .eval/verify.log 2>&1 || { tail -30 .eval/verify.log >&2; die "verify did not stamp the golden implementation"; }
  # What /factory:verify appends to the contract (step 5): the gate's lines, so /factory:ship has evidence to carry.
  python3 - "$SLUG" <<'PY'
import re, sys
log = open(".eval/verify.log", encoding="utf-8", errors="replace").read()
keep = [l for l in log.splitlines() if re.search(r"\bpassed\b|^ratchet:|ACCEPTANCE|^command\s|PASS$|^frozen tests", l)]
ev = "\n## Evidence\n\n```\n" + "\n".join(keep) + "\n```\n\n- Reviewers: not run in the fixture (Not reviewed: the whole diff).\n- Assumptions: None.\n"
open(f".claude/work/{sys.argv[1]}/contract.md", "a", encoding="utf-8").write(ev)
PY
fi

mkdir -p .eval
python3 - "$STATE" "$BASE" "$SLUG" "$PLANT" "$REMOTE" <<'EOF'
import json, subprocess, sys
state, base, slug, plant, remote = sys.argv[1:]
git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()
json.dump({"state": state, "slug": slug, "base": base, "main": git("rev-parse", "main"), "head": git("rev-parse", "HEAD"),
           "branch": git("branch", "--show-current"), "plant": plant or None, "remote": remote == "1"},
          open(".eval/fixture.json", "w"), indent=2)
EOF
echo "fixture $NAME at $STATE in $WS"
