#!/usr/bin/env bash
# review_pr.sh <n>: helper for the human approver of a PR (REC-53).
#
# Usage: review_pr.sh <pr-number> [--root DIR] [--contract PATH] [--run-tests] [--test-cmd CMD]
#                     [--trust-fork] [--unsafe-no-sandbox]
#
# It does NOT execute the PR's code unless you pass --run-tests: a PR's test files are arbitrary code
# and would run with your HOME, tokens and cloud credentials. With --run-tests it refuses a PR from a
# fork (--trust-fork overrides, for a fork you have read) and runs each test under a sandbox with an
# empty environment: macOS sandbox-exec (no network, writes only inside the throwaway worktree, the
# credential directories unreadable) or Linux bwrap; with neither available it refuses
# (--unsafe-no-sandbox runs unsandboxed: only for a PR you have already read line by line).
#
# 1. Scope: lists the PR's changed files (gh pr view) and checks them against allowed_paths of a
#    TRUSTED contract: --contract PATH, else .claude/work/current.json as it was at the merge base.
#    A contract that only the PR carries is the author grading their own work: it is shown with a
#    warning and the scope is reported as NOT VERIFIED. With no contract at all the report says
#    SCOPE NOT VERIFIED in capitals (/ship deletes current.json before the final commit, so a normal
#    PR has none: pass the ticket's contract with --contract).
# 2. New tests against the base (only with --run-tests): creates a throwaway git worktree at the PR's
#    base commit, copies ONLY the PR's added or changed test files into it from the PR head, and runs
#    each one with the test command (default: commands.test in .claude/sdlc.json, else
#    `python3 -m pytest -q`). Without --run-tests the files are only listed.
#      FAILS on base  = good: the test needs the PR's code, so it tests something new.
#      PASSES on base = suspicious: it would pass without the change.
#    A failure may be a collection/import error (the module does not exist on base); the report
#    prints the exit code so the reviewer can tell.
# Never writes to your main worktree: it fetches the PR head into FETCH_HEAD only, works in a
# temp worktree under $TMPDIR and removes it on exit. Needs gh (authenticated) and git.
# Exit: 0 clean, 1 out-of-scope files or suspicious tests, 2 misuse / cannot read the PR.
set -uo pipefail

die() { echo "review_pr: $*" >&2; exit 2; }
[ $# -ge 1 ] || die "usage: review_pr.sh <pr-number> [--root DIR] [--contract PATH] [--run-tests] [--test-cmd CMD]"
case "$1" in -h|--help) sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;; esac
PR="$1"; shift
[[ "$PR" =~ ^[0-9]+$ ]] || die "PR number must be an integer"
ROOT="."; TEST_CMD=""; CONTRACT_ARG=""; RUN_TESTS=0; TRUST_FORK=0; NO_SANDBOX=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root) [ $# -ge 2 ] || die "--root needs a value"; ROOT="$2"; shift 2 ;;
    --test-cmd) [ $# -ge 2 ] || die "--test-cmd needs a value"; TEST_CMD="$2"; shift 2 ;;
    --contract) [ $# -ge 2 ] || die "--contract needs a value"; CONTRACT_ARG="$2"; shift 2 ;;
    --run-tests) RUN_TESTS=1; shift ;;
    --trust-fork) TRUST_FORK=1; shift ;;
    --unsafe-no-sandbox) NO_SANDBOX=1; shift ;;
    *) die "unknown option $1" ;;
  esac
done
ROOT="$(cd "$ROOT" && git rev-parse --show-toplevel)" || die "not a git repository"
command -v gh >/dev/null || die "gh not found"

META="$(gh pr view "$PR" --json title,baseRefName,headRefName,files,additions,deletions,isCrossRepository 2>&1)" || die "gh pr view failed: $META"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/review-pr.XXXXXX")"
WT="$TMP/base"
cleanup() { git -C "$ROOT" worktree remove --force "$WT" >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
printf '%s' "$META" > "$TMP/meta.json"

BASE_REF="$(python3 -I -c 'import json,sys;print(json.load(open(sys.argv[1]))["baseRefName"])' "$TMP/meta.json")"
git -C "$ROOT" fetch -q origin "pull/$PR/head" || die "cannot fetch pull/$PR/head from origin"
HEAD_SHA="$(git -C "$ROOT" rev-parse FETCH_HEAD)"
git -C "$ROOT" fetch -q origin "$BASE_REF" || die "cannot fetch base $BASE_REF"
BASE_SHA="$(git -C "$ROOT" merge-base "$HEAD_SHA" FETCH_HEAD)" || die "no merge base"

python3 -I - "$TMP/meta.json" "$PR" <<'EOF'
import json, sys
m = json.load(open(sys.argv[1]))
print("PR #%s: %s" % (sys.argv[2], m["title"]))
print("base %s <- head %s   +%s -%s, %d file(s)" % (m["baseRefName"], m["headRefName"], m["additions"], m["deletions"], len(m["files"])))
EOF
git -C "$ROOT" diff --name-status --no-renames "$BASE_SHA" "$HEAD_SHA" > "$TMP/changes.txt"

# --- 1. scope ---------------------------------------------------------------
echo; echo "== Scope =="
CONTRACT_JSON=""; CONTRACT_SRC=""
if [ -n "$CONTRACT_ARG" ]; then
  [ -f "$CONTRACT_ARG" ] || die "--contract $CONTRACT_ARG not found"
  CONTRACT_JSON="$(cat "$CONTRACT_ARG")"; CONTRACT_SRC="--contract $CONTRACT_ARG"
else
  CONTRACT_JSON="$(git -C "$ROOT" show "$BASE_SHA:.claude/work/current.json" 2>/dev/null || true)"
  [ -n "$CONTRACT_JSON" ] && CONTRACT_SRC="the merge base ${BASE_SHA:0:9}"
fi
RC=0
if [ -z "$CONTRACT_JSON" ]; then
  PR_CONTRACT="$(git -C "$ROOT" show "$HEAD_SHA:.claude/work/current.json" 2>/dev/null || true)"
  echo "SCOPE NOT VERIFIED: no trusted contract (pass the ticket's contract with --contract PATH)."
  if [ -n "$PR_CONTRACT" ]; then
    echo "WARNING: the PR carries its own .claude/work/current.json. That is the author grading their own work (it can say allowed_paths [\"**\"]); it is NOT used for the verdict. Its claim:"
    printf '%s' "$PR_CONTRACT" | python3 -I -c 'import json,sys
try:
    c=json.load(sys.stdin); print("  slug=%s tier=%s allowed_paths=%s" % (c.get("slug"), c.get("tier"), c.get("allowed_paths")))
except Exception as e:
    print("  unreadable: %s" % e)'
  fi
else
  echo "contract source: $CONTRACT_SRC"
  printf '%s' "$CONTRACT_JSON" > "$TMP/contract.json"
  python3 -I - "$TMP/contract.json" "$TMP/changes.txt" <<'EOF' || RC=1
import fnmatch, json, re, sys

def to_re(glob):
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i): out += "(?:.*/)?"; i += 3
        elif glob.startswith("**", i): out += ".*"; i += 2
        elif glob[i] == "*": out += "[^/]*"; i += 1
        elif glob[i] == "?": out += "[^/]"; i += 1
        else: out += re.escape(glob[i]); i += 1
    return re.compile("^" + out + "$")

c = json.load(open(sys.argv[1]))
pats = [to_re(p) for p in c.get("allowed_paths") or []]
bad = []
for line in open(sys.argv[2]):
    st, _, path = line.rstrip("\n").partition("\t")
    if path.startswith(".claude/work/"):
        continue
    if not any(p.match(path) for p in pats):
        bad.append(path)
print("contract slug=%s tier=%s allowed_paths=%s" % (c.get("slug"), c.get("tier"), c.get("allowed_paths")))
if bad:
    print("OUT OF SCOPE (%d):" % len(bad)); [print("  " + b) for b in bad]; sys.exit(1)
print("every changed file is inside allowed_paths")
EOF
fi

# --- 2. new tests against the base -------------------------------------------
echo; echo "== New tests against the base commit =="
python3 -I - "$TMP/changes.txt" > "$TMP/tests.txt" <<'EOF'
import re, sys
for line in open(sys.argv[1]):
    st, _, p = line.rstrip("\n").partition("\t")
    b = p.rsplit("/", 1)[-1]
    if st.startswith("D"): continue
    if re.search(r"(^|/)(tests?|__tests__|spec)/", p) or b.startswith("test_") or b.endswith(("_test.py", "_test.go", "_test.rs")) or ".test." in b or ".spec." in b:
        if b == "conftest.py" or not re.search(r"\.(py|js|jsx|ts|tsx|go|rs)$", b): continue
        print(p)
EOF
if [ ! -s "$TMP/tests.txt" ]; then
  echo "the PR adds or changes no test files"
elif [ "$RUN_TESTS" -eq 0 ]; then
  echo "test files in the PR (NOT run: they are the PR's code; pass --run-tests to run each against the base in a sandbox):"
  sed 's/^/  /' "$TMP/tests.txt"
else
  IS_FORK="$(python3 -I -c 'import json,sys;print("yes" if json.load(open(sys.argv[1])).get("isCrossRepository") else "no")' "$TMP/meta.json")"
  if [ "$IS_FORK" = "yes" ] && [ "$TRUST_FORK" -eq 0 ]; then
    die "the PR comes from a fork; refusing to run its tests (read them, then pass --trust-fork if you accept the risk)"
  fi
  SANDBOX=""
  if [ "$NO_SANDBOX" -eq 0 ]; then
    if command -v sandbox-exec >/dev/null 2>&1; then SANDBOX="sandbox-exec"
    elif command -v bwrap >/dev/null 2>&1; then SANDBOX="bwrap"
    else die "no sandbox available (sandbox-exec or bwrap); refusing to run a PR's tests with your credentials in reach (--unsafe-no-sandbox overrides)"; fi
  fi
  if [ -z "$TEST_CMD" ]; then
    TEST_CMD="$(git -C "$ROOT" show "$BASE_SHA:.claude/sdlc.json" 2>/dev/null | python3 -I -c 'import json,sys
try: print((json.load(sys.stdin).get("commands") or {}).get("test",""))
except Exception: print("")')"
    [ -n "$TEST_CMD" ] || TEST_CMD="python3 -m pytest -q"
  fi
  echo "test command: $TEST_CMD <file>   (base ${BASE_SHA:0:9}, head ${HEAD_SHA:0:9})"
  git -C "$ROOT" worktree add --detach "$WT" "$BASE_SHA" >/dev/null 2>&1 || die "cannot create the throwaway worktree"
  SBHOME="$TMP/home"; mkdir -p "$SBHOME" "$TMP/tmp"
  TMP_REAL="$(cd "$TMP" && pwd -P)"; WT_REAL="$TMP_REAL/base"  # sandbox profiles need symlink-free paths (/var -> /private/var)
  REALHOME="${HOME:-/nonexistent}"
  run_test() {  # run one test file inside the sandbox with an empty environment
    local f="$1"
    local envs=(env -i "HOME=$SBHOME" "PATH=$PATH" "TMPDIR=$TMP/tmp" "LANG=C.UTF-8" "PYTHONDONTWRITEBYTECODE=1" "CI=true")
    case "$SANDBOX" in
      sandbox-exec)
        local prof="$TMP/profile.sb"
        {
          echo "(version 1)(allow default)(deny network*)(deny file-write*)"
          echo "(allow file-write* (subpath \"$WT_REAL\") (subpath \"$TMP_REAL\") (literal \"/dev/null\") (literal \"/dev/tty\") (regex #\"^/dev/fd/\"))"
          for d in .ssh .aws .gnupg .netrc .docker .kube .npmrc .pypirc .config/gh .config/gcloud .claude .claude-pi .claude-eg .claude-team .claude-servsys .claude-orch Library/Keychains; do
            echo "(deny file-read* (subpath \"$REALHOME/$d\"))"
          done
        } > "$prof"
        (cd "$WT" && "${envs[@]}" sandbox-exec -f "$prof" bash -c "$TEST_CMD \"\$1\"" _ "$f") ;;
      bwrap)
        (cd "$WT" && "${envs[@]}" bwrap --ro-bind / / --bind "$WT" "$WT" --bind "$TMP/tmp" "$TMP/tmp" --tmpfs "$REALHOME" --dev /dev --proc /proc --unshare-net --unshare-pid --die-with-parent --chdir "$WT" bash -c "$TEST_CMD \"\$1\"" _ "$f") ;;
      *)
        (cd "$WT" && "${envs[@]}" bash -c "$TEST_CMD \"\$1\"" _ "$f") ;;
    esac
  }
  [ -n "$SANDBOX" ] && echo "sandbox: $SANDBOX (no network, empty HOME/env, writes confined to the throwaway worktree)" || echo "WARNING: running unsandboxed (--unsafe-no-sandbox)"
  GOOD=(); SUSP=()
  while IFS= read -r f; do
    mkdir -p "$WT/$(dirname "$f")"
    git -C "$ROOT" show "$HEAD_SHA:$f" > "$WT/$f"
    run_test "$f" > "$TMP/out.txt" 2>&1
    code=$?
    if [ "$code" -ne 0 ]; then GOOD+=("$f (exit $code)"); else SUSP+=("$f"); fi
  done < "$TMP/tests.txt"
  echo; echo "FAIL on base (good, they test something new): ${#GOOD[@]}"
  for g in "${GOOD[@]+"${GOOD[@]}"}"; do echo "  $g"; done
  echo "PASS on base (suspicious, they pass without the change): ${#SUSP[@]}"
  for s in "${SUSP[@]+"${SUSP[@]}"}"; do echo "  $s"; done
  [ "${#SUSP[@]}" -eq 0 ] || RC=1
  echo "(a failure may be an import error because the module is new; check the exit code: pytest 1 = assertion, 2/4 = collection or usage)"
fi

echo; echo "== Not covered here =="
echo "behaviour outside the PR's own tests, callers of changed code, and whether the commit messages match the diff: read those yourself."
exit "$RC"
