#!/usr/bin/env bash
# run_harness.sh: end-to-end check of the sdlc plugin as Claude Code runs it (REC-41).
#
# Usage: run_harness.sh [--keep] [--work DIR]
#
# What it does: validates the plugin manifest (when `claude` is on the PATH), copies the sample app
# (examples/sample-app, or $SAMPLE_APP) into a throwaway git repository, runs init, and then drives
# the hooks with the JSON Claude Code sends them: scope, protected files, secrets, the commit stamp,
# commit messages, frozen tests, tier, data stores, credential reads, humans-only scripts, runners
# (uv run, poetry run, npx ...), `git diff --output`, post-edit feedback, the stop nudge and the
# ratchet. It then checks that every planted finding in the sample app is in the baseline. Small
# Node, Go and Rust repositories are generated inline and initialised when their tools are present.
# The sample app itself is never written: the harness checks its tree is unchanged at the end.
#
# Needs: git, python3 3.11+ with the sample app's dependencies (fastapi, sqlalchemy, httpx, pytest)
# on the PATH; ruff and mypy for a full baseline (without them init records a partial one and the
# harness says so). Put a virtualenv first on the PATH to supply them.
# Exit: 0 every check passed, 1 any check failed, 2 misuse or a missing prerequisite.
set -u

die() { echo "run_harness: $*" >&2; exit 2; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(cd "$HERE/../.." && pwd)"               # plugins/sdlc
TOP="$(cd "$P/../.." && pwd)"                # the repository root
SAMPLE="${SAMPLE_APP:-$TOP/examples/sample-app}"
KEEP=0; WORK=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) sed -n '2,/^set -u/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    --keep) KEEP=1; shift ;;
    --work) [ $# -ge 2 ] || die "--work needs a directory"; WORK="$2"; shift 2 ;;
    *) die "unknown argument $1 (see --help)" ;;
  esac
done
[ -d "$SAMPLE/app" ] || die "sample app not found at $SAMPLE (set SAMPLE_APP)"
command -v git >/dev/null || die "git is required"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null || die "python3 3.11 or later is required"
python3 -c 'import fastapi, sqlalchemy, httpx, pytest' 2>/dev/null || die "the sample app's dependencies are not importable by python3 (put a virtualenv with fastapi, sqlalchemy, httpx and pytest first on the PATH)"
if [ -z "$WORK" ]; then WORK="$(mktemp -d "${TMPDIR:-/tmp}/sdlc-harness.XXXXXX")"; else mkdir -p "$WORK"; WORK="$(cd "$WORK" && pwd)"; fi
cleanup() { [ "$KEEP" -eq 1 ] || rm -rf "$WORK"; }
trap cleanup EXIT

export CLAUDE_PLUGIN_ROOT="$P"
export PYTHONDONTWRITEBYTECODE=1
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=Harness GIT_AUTHOR_EMAIL=harness@example.com GIT_COMMITTER_NAME=Harness GIT_COMMITTER_EMAIL=harness@example.com
unset CI GITHUB_ACTIONS
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "PASS  $1 (exit $3)"; else fail=$((fail+1)); echo "FAIL  $1 (expected $2, got $3)"; printf '%s\n' "$4" | head -6 | sed 's/^/      /'; fi; }
hook() { OUT=$(printf '%s' "$2" | python3 -I "$P/hooks/$1" 2>&1); RC=$?; }
bash_cmd() { hook guard_bash.py "$(python3 -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$1")"; }
edit_json() { python3 -c 'import json,sys; print(json.dumps({"tool_name":sys.argv[1],"tool_input":{"file_path":sys.argv[2],"old_string":"a","new_string":"b","content":sys.argv[3]}}))' "$1" "$2" "${3:-x = 1}"; }
tree_sum() { (cd "$1" && find . -type f -not -path './.venv/*' -not -path '*/__pycache__/*' -not -path './.pytest_cache/*' -not -name '*.db' -print0 | sort -z | xargs -0 shasum | shasum | cut -c1-16); }
SAMPLE_SUM="$(tree_sum "$SAMPLE")"

echo "== validate"
if command -v claude >/dev/null; then
  claude plugin validate "$P" >"$WORK/validate.log" 2>&1; check "claude plugin validate" 0 $? "$(cat "$WORK/validate.log")"
else
  echo "skip  claude not on the PATH"
fi

echo; echo "== python: the sample app"
T="$WORK/py"; mkdir -p "$T"
(cd "$SAMPLE" && tar cf - --exclude ./.venv --exclude ./node_modules --exclude '*.db' --exclude __pycache__ --exclude .pytest_cache .) | (cd "$T" && tar xf -)
cd "$T" || exit 2
export CLAUDE_PROJECT_DIR="$T"
git init -q -b main && git add -A && git commit -qm "chore: the sample app" && git switch -qc feat/harness
INIT_FLAGS=()
if ! python3 -m ruff --version >/dev/null 2>&1 || ! python3 -m mypy --version >/dev/null 2>&1; then
  echo "   NOTE: ruff or mypy missing; init records a partial baseline (--allow-partial-baseline)"; INIT_FLAGS+=(--allow-partial-baseline)
fi
python3 -I "$P/scripts/init_project.py" --root "$T" --marketplace-repo ishpr/lathe --codeowners '@alice,@bob' ${INIT_FLAGS[@]+"${INIT_FLAGS[@]}"} >"$WORK/init.log" 2>&1
check "init on the sample app" 0 $? "$(tail -20 "$WORK/init.log")"
python3 - <<'EOF'
import json, collections
d = json.load(open(".claude/baseline.json")); c = json.load(open(".claude/sdlc.json"))
print("   config stack:", c["stack"], "app:", c["paths"]["app"], "checkers:", c["checkers"])
print("   baseline v%s:" % d["version"], len(d["counts"]), "keys,", sum(d["counts"].values()), "violations", dict(collections.Counter(k.split("|")[0] for k in d["counts"])))
EOF
check "CLAUDE.md has one sdlc marker" 1 "$(grep -c 'sdlc:start' CLAUDE.md)" "$(head -5 CLAUDE.md)"
[ -f .github/workflows/sdlc-gate.yml ]; check "CI gate written as sdlc-gate.yml" 0 $? "$(ls .github/workflows)"
grep -q 'repository: ishpr/lathe' .github/workflows/sdlc-gate.yml; check "gate checks the plugin out of ishpr/lathe" 0 $? "$(grep -n repository: .github/workflows/sdlc-gate.yml)"
python3 -c 'import json,sys; a=json.load(open(".claude/settings.local.json"))["permissions"]["allow"]; sys.exit(0 if any(sys.argv[1] in r for r in a) else 1)' "$P/scripts/"
check "local settings pre-approve the plugin's scripts by its real root" 0 $? "$(grep -n scripts .claude/settings.local.json 2>&1 | head -5)"
python3 -c 'import json,sys; a=json.load(open(".claude/settings.json"))["permissions"]["allow"]; sys.exit(1 if any(sys.argv[1] in r for r in a) else 0)' "$P/scripts/"
check "shared settings carry no machine path" 0 $? "$(grep -n "$P" .claude/settings.json | head -5)"
grep -qx '.claude/settings.local.json' .gitignore; check "local settings are gitignored" 0 $? "$(grep -n settings .gitignore)"
python3 -I "$P/scripts/check_skill_permissions.py" --root . --skills-dir "$P/skills" --settings .claude/settings.json --settings .claude/settings.local.json >"$WORK/perm.log" 2>&1
check "every skill command is pre-approved by the written settings" 0 $? "$(tail -5 "$WORK/perm.log")"
git add -A && git commit -qm "chore: initialise the SDLC working agreement" && echo "   committed on $(git rev-parse --abbrev-ref HEAD)"

echo; echo "== planted findings are in the baseline"
planted() { python3 -c 'import json,re,sys; keys=json.load(open(".claude/baseline.json"))["counts"]; sys.exit(0 if any(re.search(sys.argv[1], k) for k in keys) else 1)' "$1"; check "planted: $2" 0 $? "$(python3 -c 'import json; print("\n".join(json.load(open(".claude/baseline.json"))["counts"]))')"; }
planted '^route-auth\|.*(/items|/users|/login)' "routes without the auth dependency"
planted '^dead\|app/routes/admin\.py\|' "an APIRouter never included"
planted '^state\|app/cache\.py|module.state.*cache\.py|cache\.py.*HITS' "a module-level dict"
planted '^stubs\|app/routes/items\.py\|ENV-OUTSIDE-CONFIG' "an os.environ read outside config"
planted '^stubs\|app/routes/users\.py\|CRED-PARAM' "a credential-named query parameter"
planted '^stubs\|app/routes/items\.py\|TODO-NO-ID' "a TODO without a ticket id"
planted '^contract\|GET /reports/summary' "a frontend call to a missing route"
planted 'tests/test_items\.py' "an assertion-free test"

echo; echo "== guards"
hook session_start.py '{"source":"startup"}'; printf '%s\n' "$OUT" | head -3 | sed 's/^/   /'
hook guard_edit.py "$(edit_json Edit "$T/.claude/sdlc.json")"; check "config is protected" 2 $RC "$OUT"
hook guard_edit.py "$(edit_json Edit "$T/app/auth.py")"; check "edit on branch, no contract" 0 $RC "$OUT"
mkdir -p .claude/work; echo '{"slug":"rate-limit","allowed_paths":["app/auth.py","app/ratelimit/**","tests/**"],"tier":"high","require_tests":true}' > .claude/work/current.json
hook guard_edit.py "$(edit_json Edit "$T/app/routes/items.py")"; check "out-of-scope edit blocked" 2 $RC "$OUT"
hook guard_edit.py "$(edit_json Write "$T/app/ratelimit/store.py")"; check "in-scope new file allowed" 0 $RC "$OUT"
hook guard_edit.py "$(edit_json Write "$T/app/ratelimit/x.py" "K='AKIAABCDEFGHIJKLMNOP'")"; check "secret blocked" 2 $RC "$OUT"
git switch -q main; hook guard_edit.py "$(edit_json Edit "$T/app/auth.py")"; check "edit on main blocked" 2 $RC "$OUT"; git switch -q feat/harness
bash_cmd "gh pr merge 3"; check "pr merge blocked" 2 $RC "$OUT"
bash_cmd "git push origin main"; check "push to main blocked" 2 $RC "$OUT"
printf '\n# touch\n' >> app/auth.py; git add app/auth.py
bash_cmd 'git commit -m "feat(auth): note"'; check "app commit without stamp blocked" 2 $RC "$OUT"
python3 -I "$P/scripts/verify.py" --root "$T" > "$WORK/verify-py.log" 2>&1; check "verify on the sample app" 0 $? "$(tail -15 "$WORK/verify-py.log")"
bash_cmd 'git commit -m "feat(auth): note"'; check "commit after verify allowed" 0 $RC "$OUT"
bash_cmd 'git commit -m "fix stuff"'; check "vague message blocked" 2 $RC "$OUT"
bash_cmd 'git -C . commit -m "fix stuff"'; check "git -C . commit still guarded" 2 $RC "$OUT"
bash_cmd 'git commit -n -m "feat(auth): note"'; check "commit -n blocked" 2 $RC "$OUT"
bash_cmd 'git -c core.hooksPath=/dev/null commit -m "feat(auth): note"'; check "hooksPath override blocked" 2 $RC "$OUT"
printf 'wip\n' > "$WORK/msg.txt"
bash_cmd "git commit -F $WORK/msg.txt"; check "vague -F message blocked" 2 $RC "$OUT"
bash_cmd "git push origin HEAD:main"; check "push HEAD:main blocked" 2 $RC "$OUT"
bash_cmd "git checkout -b feature/x"; check "checkout -b allowed" 0 $RC "$OUT"
bash_cmd "git stash list"; check "stash list allowed" 0 $RC "$OUT"
printf '\n# t2\n' >> app/auth.py
bash_cmd 'git commit -am "feat(auth): note"'; check "stale stamp blocked" 2 $RC "$OUT"
bash_cmd "git commit --amend --no-edit"; check "amend needs the stamp" 2 $RC "$OUT"
git reset -q HEAD app/auth.py; git checkout -q -- app/auth.py

echo; echo "== runners and git --output"
for runner in "uv run" "poetry run" "npx" "timeout 30" "nice -n 5"; do
  bash_cmd "$runner git push -f origin feat/harness"; check "$runner: force push blocked" 2 $RC "$OUT"
  bash_cmd "$runner git reset --hard"; check "$runner: reset --hard blocked" 2 $RC "$OUT"
done
bash_cmd "uv run pytest -q"; check "uv run pytest allowed" 0 $RC "$OUT"
bash_cmd "git diff --output=.claude/baseline.json"; check "git diff --output to the baseline blocked" 2 $RC "$OUT"
bash_cmd "git log -p --output $WORK/changes.txt"; check "git log --output elsewhere allowed" 0 $RC "$OUT"

echo; echo "== post-edit and stop"
cat > app/routes/newthing.py <<'EOF'
from fastapi import APIRouter
router = APIRouter(prefix="/things")
_CACHE = {}

@router.get("")
def list_things():
    return []
EOF
hook post_edit.py "{\"tool_input\":{\"file_path\":\"$T/app/routes/newthing.py\"}}"; check "post-edit reports the new violations" 2 $RC "$OUT"; printf '%s\n' "$OUT" | head -3 | sed 's/^/      /'
rm -f app/routes/newthing.py
hook post_edit.py "{\"tool_input\":{\"file_path\":\"$T/app/cache.py\"}}"; check "post-edit on known debt passes" 0 $RC "$OUT"
printf '\n# touch\n' >> app/auth.py
hook stop_check.py '{"stop_hook_active":false}'; check "stop nudge once" 2 $RC "$OUT"
hook stop_check.py '{"stop_hook_active":false}'; check "second stop passes (marker)" 0 $RC "$OUT"
git checkout -q -- app/auth.py

echo; echo "== fail closed, data stores, humans-only, frozen tests, tier (REC-41, 07, 18, 54, 116)"
hook guard_bash.py ''; check "empty stdin refused" 2 $RC "$OUT"
hook guard_bash.py '{not json'; check "malformed json refused" 2 $RC "$OUT"
hook guard_edit.py ''; check "edit guard: empty stdin refused" 2 $RC "$OUT"
hook post_edit.py ''; check "post_edit: empty stdin passes" 0 $RC "$OUT"
hook stop_check.py '{nope'; check "stop_check: malformed stdin passes" 0 $RC "$OUT"
hook session_start.py ''; check "session_start: empty stdin passes" 0 $RC "$OUT"
bash_cmd "dropdb app"; check "dropdb blocked" 2 $RC "$OUT"
bash_cmd "docker compose down -v"; check "compose down -v blocked" 2 $RC "$OUT"
bash_cmd "cat .env"; check "cat .env blocked" 2 $RC "$OUT"
bash_cmd "python3 -I scripts/ratchet.py --prune"; check "ratchet --prune blocked" 2 $RC "$OUT"
bash_cmd "python3 -I \"$P/scripts/ratchet.py\" --update"; check "ratchet --update (plugin path, quoted) blocked" 2 $RC "$OUT"
bash_cmd "python3 -I scripts/freeze_tests.py --unfreeze tests/x.py"; check "freeze --unfreeze blocked" 2 $RC "$OUT"
bash_cmd "python3 -I scripts/freeze_tests.py --freeze tests/x.py"; check "freeze --freeze allowed" 0 $RC "$OUT"
printf '%s\n' "   block log: $(wc -l < .claude/work/blocks.jsonl | tr -d ' ') line(s), rules: $(python3 -c "import json;print(sorted({json.loads(l)['rule'] for l in open('.claude/work/blocks.jsonl')}))")"
python3 -I "$P/scripts/freeze_tests.py" --root "$T" --freeze tests/test_items.py >/dev/null 2>&1; check "freeze tests/test_items.py" 0 $? ""
hook guard_edit.py "$(edit_json Edit "$T/tests/test_items.py")"; check "frozen test edit blocked" 2 $RC "$OUT"
bash_cmd "git show HEAD --output=tests/test_items.py"; check "git show --output onto a frozen test blocked" 2 $RC "$OUT"
python3 -I "$P/scripts/freeze_tests.py" --root "$T" --unfreeze tests/test_items.py >/dev/null 2>&1
hook guard_edit.py "$(edit_json Edit "$T/tests/test_items.py")"; check "unfrozen test editable again" 0 $RC "$OUT"
echo '{"slug":"rate-limit","allowed_paths":["app/**","tests/**","docker-compose.yml"],"tier":"low","require_tests":true}' > .claude/work/current.json
hook guard_edit.py "$(edit_json Write "$T/docker-compose.yml")"; check "tier low + tier-high path blocked" 2 $RC "$OUT"
rm -f .claude/work/current.json
python3 -I "$P/scripts/ratchet.py" --root "$T" --all 2>&1 | tail -1 | sed 's/^/   /'
python3 -I "$P/scripts/ratchet.py" --root "$T" > "$WORK/ratchet.log" 2>&1; check "ratchet clean against its own baseline" 0 $? "$(tail -5 "$WORK/ratchet.log")"

other_stack() {  # name, dir: init with --no-ci and the ratchet against its own baseline
  python3 -I "$P/scripts/init_project.py" --root "$2" --no-ci --allow-partial-baseline >"$WORK/init-$1.log" 2>&1
  check "$1: init" 0 $? "$(tail -8 "$WORK/init-$1.log")"
  grep -E 'stack:|baseline' "$WORK/init-$1.log" | sed 's/^/   /'
  python3 -I "$P/scripts/ratchet.py" --root "$2" --all 2>&1 | sed 's/^/   /'
  python3 -I "$P/scripts/ratchet.py" --root "$2" >"$WORK/ratchet-$1.log" 2>&1; check "$1: ratchet clean against its own baseline" 0 $? "$(tail -5 "$WORK/ratchet-$1.log")"
}

echo; echo "== node (generated)"
N="$WORK/node"; mkdir -p "$N/src" "$N/test"; cd "$N" || exit 2; export CLAUDE_PROJECT_DIR="$N"; git init -q -b main
echo '{"name":"fx","version":"1.0.0","scripts":{"test":"node --test"}}' > package.json
cat > src/store.ts <<'EOF'
export let counter = 0;
const cache = new Map<string, number>();
export const REGISTRY = { a: 1 };
export function hit(k: string) { counter++; cache.set(k, (cache.get(k) ?? 0) + 1); return cache.get(k); }
EOF
cat > test/store.test.ts <<'EOF'
import { hit } from "../src/store";
it("hits", () => { hit("a"); });
it("counts", () => { expect(hit("b")).toBe(1); });
EOF
git add -A; git commit -qm "chore: init"
other_stack node "$N"

echo; echo "== node + pnpm lockfile (generated)"
NP="$WORK/node-pnpm"; mkdir -p "$NP/src" "$NP/test"; cd "$NP" || exit 2; export CLAUDE_PROJECT_DIR="$NP"; git init -q -b main
echo '{"name":"fxp","version":"1.0.0","scripts":{"test":"node --test","typecheck":"tsc --noEmit"}}' > package.json
: > pnpm-lock.yaml
cp "$N/src/store.ts" src/store.ts; cp "$N/test/store.test.ts" test/store.test.ts
git add -A; git commit -qm "chore: init"
other_stack node-pnpm "$NP"
python3 -I - "$NP" <<'EOF'
import json, sys
c = json.load(open(sys.argv[1] + "/.claude/sdlc.json"))["commands"]
want = {"test": "pnpm test", "lint_fix": "pnpm exec eslint --fix --quiet {file}", "format": "pnpm exec prettier --write --log-level silent {file}",
        "lint_report": "pnpm exec eslint -f json .", "typecheck": "pnpm typecheck", "setup": "pnpm install --frozen-lockfile"}
bad = {k: c.get(k) for k, v in want.items() if c.get(k) != v}
if bad:
    print("pnpm commands differ:", bad)
    sys.exit(1)
EOF
check "node-pnpm: pnpm commands and the setup command land in .claude/sdlc.json" 0 $? "$(python3 -I -c 'import json,sys;print(json.load(open(sys.argv[1]+"/.claude/sdlc.json"))["commands"])' "$NP")"
grep -q '"Bash(pnpm install --frozen-lockfile)"' "$NP/.claude/settings.json"; check "node-pnpm: settings allow the setup command" 0 $? "setup rule missing"
grep -q '"Bash(pnpm test \*)"' "$NP/.claude/settings.json"; check "node-pnpm: settings allow pnpm test" 0 $? "test rule missing"

echo; echo "== go (generated)"
if command -v go >/dev/null; then
  G="$WORK/go"; mkdir -p "$G"; cd "$G" || exit 2; export CLAUDE_PROJECT_DIR="$G"; git init -q -b main; go mod init example.com/fx >/dev/null 2>&1
  cat > store.go <<'EOF'
package fx

var bucket = map[string]int{}
var limit = 100

func Hit(k string) int { bucket[k]++; return bucket[k] + 0*limit }
EOF
  cat > store_test.go <<'EOF'
package fx

import "testing"

func TestHit(t *testing.T) { Hit("a") }

func TestHitTwice(t *testing.T) {
	if Hit("b") != 1 {
		t.Fatal("expected 1")
	}
}
EOF
  git add -A; git commit -qm "chore: init"
  other_stack go "$G"
else
  echo "skip  go not on the PATH"
fi

echo; echo "== rust (generated)"
if command -v cargo >/dev/null; then
  R="$WORK/rust"; mkdir -p "$R/src"; cd "$R" || exit 2; export CLAUDE_PROJECT_DIR="$R"; git init -q -b main
  printf '[package]\nname = "fx"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n' > Cargo.toml
  printf 'target/\n' > .gitignore
  cat > src/lib.rs <<'EOF'
use std::collections::HashMap;
use std::sync::Mutex;

static BUCKET: Mutex<Option<HashMap<String, u32>>> = Mutex::new(None);

pub fn hit(k: &str) -> u32 {
    let mut g = BUCKET.lock().unwrap();
    let m = g.get_or_insert_with(HashMap::new);
    let c = m.entry(k.to_string()).or_insert(0);
    *c += 1;
    *c
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hits() {
        hit("a");
    }

    #[test]
    fn counts() {
        assert_eq!(hit("b"), 1);
    }
}
EOF
  git add -A; git commit -qm "chore: init"
  other_stack rust "$R"
else
  echo "skip  cargo not on the PATH"
fi

echo
check "the sample app is unchanged" "$SAMPLE_SUM" "$(tree_sum "$SAMPLE")" "a harness step wrote into $SAMPLE"
[ "$KEEP" -eq 1 ] && echo "work kept at $WORK"
echo "RESULT pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
