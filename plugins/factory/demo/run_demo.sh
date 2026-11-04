#!/usr/bin/env bash
# sdlc-graph end to end on a copy of the repository's sample app (examples/sample-app).
#
# Idempotent: resets the local database, re-copies the fixture, then runs every step. Exits
# non-zero on the first unexpected result (an expected refusal that is NOT refused also fails).
# Talking points per step: demo/DEMO.md.
#
# Environment (all optional):
#   DATABASE_URL           default postgresql://sdlc:sdlc@localhost:5435/sdlc (must be local; the
#                          demo runs 'db reset', so a remote URL is refused unless DEMO_ALLOW_REMOTE_DB=1)
#   SPANNER_EMULATOR_HOST  default localhost:9110 (compose 'spanner' profile; started if not running)
#   SAMPLE_REPO            a different repository to copy instead of examples/sample-app (a FastAPI app
#                          with an auth dependency in DEMO_AUTH_FILE); the source is only ever read
#   DEMO_FIXTURE           overrides SAMPLE_REPO when both are set
#   DEMO_AUTH_FILE         the auth module the demo edits and regresses (default app/auth.py)
#   DEMO_KNOWN_FAILING     test ids the regression breaks, used only when no Python can run the suite
#                          (default: the sample app's tests/test_items.py::test_create_requires_key)
#   DEMO_WORK              where the mutable copy goes (default: .demo/sample-app in this plugin)
#   DEMO_APP_PY            python with the app's test deps (default: .demo/app-venv, built by uv)
#   DEMO_SKIP_SPANNER=1    skip the Spanner export (step 9 then prints the DDL only)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
SG="$ROOT/.venv/bin/sdlc-graph"
PY="$ROOT/.venv/bin/python"
SHOW=("$PY" "$ROOT/demo/show.py")
DATA="$ROOT/demo/data"
REPO=demo/sample-app  # owner/name: CI evidence is bound to a repository exactly (a bare name is refused)
AUTH_FILE="${DEMO_AUTH_FILE:-app/auth.py}"
GIT_ID=(-c user.name=demo -c user.email=demo@example.invalid -c commit.gpgsign=false)

export DATABASE_URL="${DATABASE_URL:-postgresql://sdlc:sdlc@localhost:5435/sdlc}"
export SPANNER_EMULATOR_HOST="${SPANNER_EMULATOR_HOST:-localhost:9110}"
unset SDLC_SCHEMA SDLC_STAGE SDLC_NODE SDLC_EPIC
case "$DATABASE_URL" in
  *@localhost:*|*@127.0.0.1:*|*@\[::1\]:*) ;;
  *) [ "${DEMO_ALLOW_REMOTE_DB:-}" = 1 ] || { echo "refusing to 'db reset' a non-local DATABASE_URL (set DEMO_ALLOW_REMOTE_DB=1)" >&2; exit 2; } ;;
esac

SRC="${DEMO_FIXTURE:-${SAMPLE_REPO:-$REPO_ROOT/examples/sample-app}}"
[ -d "$SRC" ] || { echo "no fixture at $SRC (examples/sample-app, or SAMPLE_REPO/DEMO_FIXTURE)" >&2; exit 2; }
WORK="${DEMO_WORK:-$ROOT/.demo/sample-app}"
OUT="$ROOT/.demo/out"   # fixed: never derived from DEMO_WORK, so a bad DEMO_WORK cannot point the delete elsewhere

START=$SECONDS
STEP=0
step() {  # step "title" "one-line talking point"
  STEP=$((STEP + 1))
  printf '\n\033[1m== %s. %s\033[0m  [%ss]\n   %s\n' "$STEP" "$1" "$((SECONDS - START))" "$2"
}
fail() { echo "DEMO FAILED at step $STEP: $*" >&2; exit 1; }
sg() {  # sdlc-graph ... --json, must exit 0
  local out
  out="$("$SG" "$@" --json)" || { echo "$out" >&2; fail "sdlc-graph $* exited non-zero"; }
  printf '%s' "$out"
}
refused() {  # sdlc-graph ... --json, must exit 1 (a refusal returned as a normal result)
  local out rc=0
  out="$("$SG" "$@" --json)" || rc=$?
  [ "$rc" -eq 1 ] || { echo "$out" >&2; fail "expected a refusal (exit 1) from sdlc-graph $*, got exit $rc"; }
  printf '%s' "$out"
}
field() { "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(d[sys.argv[1]])' "$1"; }

# --------------------------------------------------------------------------- setup
[ -x "$SG" ] || { echo "no $SG: run 'uv venv --python 3.11 .venv && uv sync' first" >&2; exit 2; }
# The work copy is deleted and recreated, so DEMO_WORK must resolve (symlinks followed) to a path
# strictly inside this plugin's .demo/ or a temp directory, and must not contain, or sit inside, any
# of the trees the demo reads from or lives in.
real() { "$PY" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1"; }
WORK_REAL="$(real "$WORK")"
inside() {  # inside CHILD PARENT: CHILD is strictly under PARENT
  case "$1/" in "$2/"*) [ "$1" != "$2" ] ;; *) return 1 ;; esac
}
ALLOWED=0
for base in "$(real "$ROOT/.demo")" "$(real /tmp)" "$(real "${TMPDIR:-/tmp}")"; do
  inside "$WORK_REAL" "$base" && ALLOWED=1
done
[ "$ALLOWED" = 1 ] || { echo "refusing DEMO_WORK=$WORK ($WORK_REAL): it must be inside $ROOT/.demo or a temp directory" >&2; exit 2; }
for protected in "$SRC"; do  # never delete, or inside, a tree the demo reads
  p="$(real "$protected")"
  if [ "$WORK_REAL" = "$p" ] || inside "$WORK_REAL" "$p" || inside "$p" "$WORK_REAL"; then
    echo "refusing DEMO_WORK=$WORK ($WORK_REAL): it overlaps $p" >&2; exit 2
  fi
done
if [ "$WORK_REAL" = "$(real "$ROOT")" ] || inside "$(real "$ROOT")" "$WORK_REAL" || inside "$HOME" "$WORK_REAL" || [ "$WORK_REAL" = "$(real "$HOME")" ]; then
  echo "refusing DEMO_WORK=$WORK ($WORK_REAL): it contains this repo or your home directory" >&2; exit 2
fi
[ -n "$(basename "$WORK_REAL")" ] && [ "$(basename "$WORK_REAL")" != "." ] || { echo "refusing DEMO_WORK=$WORK" >&2; exit 2; }
WORK="$WORK_REAL"
[ -f "$SRC/$AUTH_FILE" ] || { echo "no $AUTH_FILE in $SRC (set DEMO_AUTH_FILE to the app's auth module)" >&2; exit 2; }
rm -rf "$WORK" "$OUT"; mkdir -p "$(dirname "$WORK")" "$OUT"
# Copy without build output or local state; the source is only read.
"$PY" -c 'import shutil,sys; shutil.copytree(sys.argv[1], sys.argv[2], symlinks=True, ignore=shutil.ignore_patterns(
    ".venv", "__pycache__", ".pytest_cache", "node_modules", "*.db"))' "$SRC" "$WORK"
if [ ! -d "$WORK/.git" ]; then  # examples/sample-app lives inside this repository: give the copy its own history
  git -C "$WORK" init -q -b main
  git -C "$WORK" "${GIT_ID[@]}" add -A
  git -C "$WORK" "${GIT_ID[@]}" commit -qm "sample app"
fi
# Ownership needs a CODEOWNERS file; a fixture without one gets a generic one. The auth owner matters:
# the example contract is high risk because it touches the auth dependency.
if [ ! -f "$WORK/CODEOWNERS" ] && [ ! -f "$WORK/.github/CODEOWNERS" ] && [ ! -f "$WORK/docs/CODEOWNERS" ]; then
  mkdir -p "$WORK/.github"
  printf '* @example/platform\n/%s @example/security\n/tests/ @example/qa\n' "$AUTH_FILE" > "$WORK/.github/CODEOWNERS"
  git -C "$WORK" "${GIT_ID[@]}" add .github/CODEOWNERS
  git -C "$WORK" "${GIT_ID[@]}" commit -qm "demo: add CODEOWNERS"
fi

# --------------------------------------------------------------------------- 1
step "Fresh database" "Two stores in one Postgres: a derived code graph and a guarded intent ledger."
if ! RESET_OUT="$("$SG" db reset --json 2>&1)"; then
  echo "$RESET_OUT" >&2
  fail "db reset: the database is not reachable. Start it with: docker compose up -d --wait db"
fi
sg db init | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print("  schema", d["schema"], "tables:", ", ".join(d["tables"]))'

# --------------------------------------------------------------------------- 2
step "Index HEAD, change one line, store a delta, verify" \
     "The graph is derived, never written: a re-extraction must reproduce the stored snapshot exactly."
BASE_SHA="$(git -C "$WORK" rev-parse HEAD)"
sg index --repo "$WORK" --name "$REPO" --sha "$BASE_SHA" | "${SHOW[@]}" index
"$PY" - "$WORK/$AUTH_FILE" <<'EOF'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
p.write_text(s.rstrip("\n") + "\n\nDEFAULT_REQUESTS_PER_MINUTE = 100  # per-key-rate-limit: default allowance\n")
EOF
git -C "$WORK" diff --stat | sed 's/^/  /'
git -C "$WORK" "${GIT_ID[@]}" commit -qam "demo: default per-key allowance"
HEAD_SHA="$(git -C "$WORK" rev-parse HEAD)"
sg delta --repo "$WORK" --name "$REPO" --from "$BASE_SHA" --to "$HEAD_SHA" | "${SHOW[@]}" delta
sg verify-snapshot --repo "$WORK" --name "$REPO" --sha "$HEAD_SHA" | "${SHOW[@]}" verify

# --------------------------------------------------------------------------- 3
step "Blast radius of $AUTH_FILE" \
     "Before anyone writes code: which routes, which tests, which owners. Static, from the graph."
sg query impact "$AUTH_FILE" --repo "$REPO" > "$OUT/impact.json"
"${SHOW[@]}" impact < "$OUT/impact.json"

# --------------------------------------------------------------------------- 4
step "Epic, feature, contract (the sample app's rate-limit example)" \
     "Intent is a node with guards; overlap is refused unless declared; ready needs decisions, defaults and capabilities."
sg node create --kind epic --slug rate-limits --title "Rate limits for the public API" --repo "$REPO" \
   --actor @ish --tier high | "${SHOW[@]}" node
sg node create --kind feature --slug per-key-rate-limit --title "Per-key rate limit" --repo "$REPO" \
   --actor @ish --tier high | "${SHOW[@]}" node
sg node link rate-limits per-key-rate-limit --rel decomposes_into | "${SHOW[@]}" link
# The touched symbol is the auth dependency, taken from the impact result (a function in the auth module,
# one whose name says auth first).
TOUCHED="$("$PY" - "$OUT/impact.json" "$AUTH_FILE" <<'EOF'
import json, sys
syms = [s for s in json.load(open(sys.argv[1]))["symbols"] if s["kind"] == "function" and s["path"] == sys.argv[2]]
syms.sort(key=lambda s: ("auth" not in s["name"].lower(), s["qualname"]))
print(f"{syms[0]['kind']}:{syms[0]['qualname']}@{syms[0]['path']}" if syms else "")
EOF
)"
[ -n "$TOUCHED" ] || fail "no function in $AUTH_FILE in the impact result"
TOUCHED_QUALNAME="${TOUCHED#function:}"; TOUCHED_QUALNAME="${TOUCHED_QUALNAME%@*}"
echo "  touched symbol taken from the impact result: $TOUCHED"
sg node draft --slug rate-limit \
   --title "Per-key rate limiting: 429 over 100 req/min per key, with X-RateLimit headers" \
   --repo "$REPO" --actor @ish --ticket-file "$DATA/rate-limit.ticket.md" --feature per-key-rate-limit \
   --touched "$TOUCHED" app/ratelimit/ --capabilities "@$DATA/rate-limit.capabilities.json" \
   --acceptance "@$DATA/rate-limit.acceptance.json" --tier high --contract "$DATA/rate-limit.contract.md" \
   > "$OUT/draft.json"
"${SHOW[@]}" draft < "$OUT/draft.json"
echo "  -- ask before, never during: the contract.md's Defaults when ambiguous and Capabilities blocks went onto the node at draft time:"
"$PY" - "$OUT/draft.json" <<'EOF' || fail "the drafted contract is missing an autonomy block"
import json, sys
d = json.load(open(sys.argv[1])); n = d["node"]
caps = n["capabilities"]
print(f"  defaults: {len(n['attrs'].get('defaults') or [])} line(s); capabilities: "
      + ", ".join(f"{k}={len(v)}" for k, v in caps.items() if isinstance(v, list)))
assert d["blocks"]["missing"] == [], d["blocks"]
EOF
D1="$(sg decision propose rate-limit --question "Window: fixed per calendar minute, or sliding 60 s?" \
   --options '["fixed","sliding"]' --recommendation fixed --actor agent:opus)"
"${SHOW[@]}" decision <<<"$D1"
D2="$(sg decision propose rate-limit --question "Rate-limit store down: fail-open (allow, count, alert) or fail-closed (503)?" \
   --options '["fail-open","fail-closed"]' --recommendation fail-open --actor agent:opus)"
"${SHOW[@]}" decision <<<"$D2"
sg workflow next rate-limit > "$OUT/wf-drafted.json"; "${SHOW[@]}" workflow < "$OUT/wf-drafted.json"
echo "  -- a second contract touching the same function, with no dependency declared:"
refused node draft --slug login-throttle --title "Throttle repeated login attempts" --repo "$REPO" --actor @ish \
   --ticket "Throttle repeated login attempts from one client." --touched "$TOUCHED" | "${SHOW[@]}" draft
echo "  -- the same contract, declaring that it builds on rate-limit:"
sg node draft --slug login-throttle --title "Throttle repeated login attempts" --repo "$REPO" --actor @ish \
   --ticket "Throttle repeated login attempts from one client." --touched "$TOUCHED" --depends-on rate-limit \
   | "${SHOW[@]}" draft
refused node transition rate-limit ready --actor @ish | "${SHOW[@]}" transition
echo "  -- an agent may propose decisions but not make them:"
OUT_RC=0; "$SG" decision resolve "$(field id <<<"$D1")" --choice fixed --actor agent:opus --json > "$OUT/agent-resolve.json" || OUT_RC=$?
[ "$OUT_RC" -eq 2 ] || fail "an agent resolved a decision (exit $OUT_RC)"
"${SHOW[@]}" decision < "$OUT/agent-resolve.json"
sg decision resolve "$(field id <<<"$D1")" --choice fixed --actor @ish | "${SHOW[@]}" decision
sg decision resolve "$(field id <<<"$D2")" --choice fail-open --actor @ish | "${SHOW[@]}" decision
sg node transition rate-limit ready --actor @ish | "${SHOW[@]}" transition
ACCEPTANCE_PY="$ROOT/scripts/acceptance.py"
if [ -f "$ACCEPTANCE_PY" ]; then
  echo "  -- the contract.md the orchestrator renders from the ledger passes the pack's acceptance.py --ready:"
  mkdir -p "$OUT/rendered"
  "$PY" - "$OUT/rendered/contract.md" <<'EOF'
import sys
from sdlc_graph import db
from sdlc_graph.ledger import nodes
from sdlc_graph.orchestrator import dispatch
with db.connect() as conn:
    node = nodes.get_row(conn, "rate-limit")
    open(sys.argv[1], "w").write(dispatch.contract_md(conn, {**node, "id": str(node["id"])}))
EOF
  python3 -I "$ACCEPTANCE_PY" --root "$OUT/rendered" --contract "$OUT/rendered/contract.md" --ready | sed 's/^/  /' \
    || fail "the ledger-rendered contract is not ready"
fi

# --------------------------------------------------------------------------- 5
step "Evidence moves the contract; local evidence is advisory" \
     "Agents can do the work, but only CI-signed evidence and a human reach approved and merged."
FROZEN_SHA="$(printf 'tests/ratelimit/test_limiter.py @ %s' "$HEAD_SHA" | shasum -a 256 | cut -c1-64)"
# Every acceptance criterion's test must have failed first (the frozen_tests_match guard checks it).
FAILING_FIRST="$("$PY" - "$DATA/rate-limit.acceptance.json" "$HEAD_SHA" <<'EOF'
import json, sys
rows = json.load(open(sys.argv[1]))
print(json.dumps({"tests": [r["test"] for r in rows], "commit": sys.argv[2]}))
EOF
)"
sg evidence add rate-limit --kind tests_failing_first --payload "$FAILING_FIRST" | "${SHOW[@]}" evidence
sg evidence add rate-limit --kind tests_frozen \
   --payload "{\"files\": {\"tests/ratelimit/test_limiter.py\": \"$FROZEN_SHA\"}, \"commit\": \"$HEAD_SHA\"}" | "${SHOW[@]}" evidence
sg node transition rate-limit tests_frozen --actor agent:sonnet | "${SHOW[@]}" transition
sg node transition rate-limit implementing --actor agent:sonnet | "${SHOW[@]}" transition
sg workflow next rate-limit > "$OUT/wf-implementing.json"; "${SHOW[@]}" workflow < "$OUT/wf-implementing.json"
sg evidence add rate-limit --kind verify_stamp --payload '{"frozen_ok": true, "acceptance": {"passed": 9, "failed": 0}, "ratchet": {"new": 0, "known": 1}, "db_backend": "sqlite", "tool_versions": {"pytest": "9"}}' \
   | "${SHOW[@]}" evidence
sg node transition rate-limit verified --actor agent:sonnet | "${SHOW[@]}" transition
sg evidence add rate-limit --kind pr_url --payload '{"url": "https://github.com/x/y/pull/1"}' | "${SHOW[@]}" evidence
sg node transition rate-limit in_review --actor agent:sonnet | "${SHOW[@]}" transition
echo "  -- a green CI run reported from a laptop (source local, unsigned):"
sg evidence add rate-limit --kind ci_run --payload '{"url": "https://github.com/x/y/actions/runs/1", "conclusion": "success"}' \
   | "${SHOW[@]}" evidence
sg workflow next rate-limit > "$OUT/wf-in_review.json"; "${SHOW[@]}" workflow < "$OUT/wf-in_review.json"
refused node transition rate-limit approved --actor @ish | "${SHOW[@]}" transition
sg evidence add rate-limit --kind review_approval \
   --payload '{"reviewer": "@ish", "author": "agent:sonnet", "url": "https://github.com/x/y/pull/1#pullrequestreview-1"}' \
   | "${SHOW[@]}" evidence
# CI evidence is binding only with a verified GitHub OIDC token from the trusted evidence workflow
# (templates/ci/sdlc-evidence.yml, a workflow_run job on the default branch). Offline, a throwaway key stands
# in for GitHub's: SDLC_OIDC_JWKS_FILE is the same seam the tests use, and CI would send a real token instead.
export SDLC_OIDC_WORKFLOW_REF="$REPO/.github/workflows/sdlc-evidence.yml@refs/heads/main"
SDLC_OIDC_JWKS_FILE="$OUT/jwks.json" "$PY" - "$OUT" "$REPO" "$SDLC_OIDC_WORKFLOW_REF" <<'PYEOF'
import json, sys, time
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
out, repo, wf = sys.argv[1], sys.argv[2], sys.argv[3]
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())); jwk.update(kid="demo", alg="RS256")
open(f"{out}/jwks.json", "w").write(json.dumps({"keys": [jwk]}))
now = int(time.time())
def mint(name, **over):
    claims = {"iss": "https://token.actions.githubusercontent.com", "aud": "sdlc-graph",
              "sub": f"repo:{repo}:ref:refs/heads/main", "repository": repo, "event_name": "workflow_run",
              "job_workflow_ref": wf, "run_id": "2", "iat": now, "exp": now + 600}
    claims.update(over)
    open(f"{out}/{name}", "w").write(jwt.encode(claims, key, algorithm="RS256", headers={"kid": "demo"}))
mint("oidc.jwt")
# what a PR's own workflow would hold: its own file at the PR's ref, on the pull_request event
mint("oidc-pr.jwt", sub=f"repo:{repo}:pull_request", event_name="pull_request",
     job_workflow_ref=f"{repo}/.github/workflows/evil.yml@refs/pull/1/merge")
PYEOF
CI_PAYLOAD='{"url": "https://github.com/demo/sample-app/actions/runs/1", "conclusion": "success", "green": true, "run_id": 1}'
echo "  -- a token from a PR's own workflow is refused (exit 2, nothing stored):"
rc=0; SDLC_OIDC_JWKS_FILE="$OUT/jwks.json" "$SG" evidence add rate-limit --kind ci_run --payload "$CI_PAYLOAD" \
   --source "ci:https://github.com/demo/sample-app/actions/runs/1" --oidc-token "@$OUT/oidc-pr.jwt" --json \
   > "$OUT/ci-pr-refused.json" || rc=$?
[ "$rc" -eq 2 ] || { cat "$OUT/ci-pr-refused.json" >&2; fail "a pull_request token was not refused (exit $rc)"; }
"$PY" -c 'import json,sys; print("  refused:", json.load(open(sys.argv[1]))["message"])' "$OUT/ci-pr-refused.json"
SDLC_OIDC_JWKS_FILE="$OUT/jwks.json" sg evidence add rate-limit --kind ci_run --payload "$CI_PAYLOAD" \
   --source "ci:https://github.com/demo/sample-app/actions/runs/1" --oidc-token "@$OUT/oidc.jwt" | "${SHOW[@]}" evidence
echo "  -- the same evidence, but an agent asks:"
refused node transition rate-limit approved --actor agent:opus | "${SHOW[@]}" transition
sg node transition rate-limit approved --actor @ish | "${SHOW[@]}" transition
sg evidence add rate-limit --kind merge_sha --payload "{\"sha\": \"$HEAD_SHA\"}" | "${SHOW[@]}" evidence
sg node transition rate-limit merged --actor @ish | "${SHOW[@]}" transition

# --------------------------------------------------------------------------- 6
step "Routing: what runs next, on which model, and the Fable budget" \
     "Each stage has a tier; planner (Fable) spawns are counted per epic and refused when the budget is spent."
for s in drafted implementing in_review; do "${SHOW[@]}" workflow < "$OUT/wf-$s.json"; done
for i in 1 2; do
  RID="$(sg run start --stage contract.design.high --model fable --node rate-limit --tool-use-id "demo-plan-$i" | field id)"
  sg run finish "$RID" --outcome ok --usage '{"tokens_in": 12000, "tokens_out": 3000, "turns": 4}' | "${SHOW[@]}" run
done
sg run budget rate-limit | "${SHOW[@]}" budget
[ "$(sg run budget rate-limit | field remaining)" = 1 ] || fail "expected 1 Fable run left"

# --------------------------------------------------------------------------- 7
step "Predict the affected tests, then score the prediction against a real pytest run" \
     "The graph's guess is recorded before the run and scored after it: recall says whether it is safe to trust."
sg impact predict --node rate-limit > "$OUT/predict.json"
"${SHOW[@]}" predict < "$OUT/predict.json"
PRED_ID="$(field prediction_id < "$OUT/predict.json")"
# A deliberate regression in the working tree: a bad or missing key now gets 403 instead of 401.
"$PY" - "$WORK/$AUTH_FILE" <<'EOF'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
new, n = re.subn(r"status_code=401\b|HTTP_401_UNAUTHORIZED",
                 lambda m: m.group(0).replace("401_UNAUTHORIZED", "403_FORBIDDEN").replace("401", "403"), s, count=1)
assert n == 1, "no 401 in the auth module; update the demo's regression"
p.write_text(new)
EOF
echo "  regression in the working tree: a bad or missing X-API-Key -> 403 instead of 401"
APP_PY="${DEMO_APP_PY:-$ROOT/.demo/app-venv/bin/python}"
if [ ! -x "$APP_PY" ] && [ -z "${DEMO_APP_PY:-}" ] && command -v uv >/dev/null; then
  # the app's declared dependencies (project and the dev group) plus pytest, offline from uv's cache first
  DEPS="$("$PY" - "$WORK/pyproject.toml" <<'EOF'
import sys, tomllib
try:
    d = tomllib.load(open(sys.argv[1], "rb"))
except OSError:
    d = {}
deps = list(d.get("project", {}).get("dependencies", []))
deps += [x for x in d.get("dependency-groups", {}).get("dev", []) if isinstance(x, str)]
print(" ".join(dict.fromkeys(deps + ["pytest", "httpx"])))
EOF
)"
  uv venv -q --python 3.11 "$ROOT/.demo/app-venv" >/dev/null 2>&1 || true
  # shellcheck disable=SC2086
  { uv pip install -q --offline --python "$APP_PY" $DEPS || uv pip install -q --python "$APP_PY" $DEPS; } >/dev/null 2>&1 \
    || rm -rf "$ROOT/.demo/app-venv"
fi
if [ -x "$APP_PY" ]; then
  # The whole suite, not just the predicted tests: only then can a missed test show up in recall.
  # A throwaway sqlite file per run, never the repo's test.db or an exported Postgres URL.
  TDB="$(mktemp -d)"
  (cd "$WORK" && DATABASE_URL="sqlite:///$TDB/test.db" \
     "$APP_PY" -m pytest -q -p no:cacheprovider --rootdir "$WORK" --junitxml="$OUT/junit.xml" 2>&1 | grep -E "^(FAILED|[0-9]+ (passed|failed))" | sed 's/^/  pytest: /') || true
  rm -rf "$TDB"
  [ -s "$OUT/junit.xml" ] || fail "pytest wrote no JUnit report"
  sg impact outcome --prediction "$PRED_ID" --junit "$OUT/junit.xml" > "$OUT/outcome.json"
else
  KNOWN="${DEMO_KNOWN_FAILING:-tests/test_items.py::test_create_requires_key}"
  echo "  (no Python with the app's test dependencies here: recording the known failures with --failing: $KNOWN)"
  # shellcheck disable=SC2086
  sg impact outcome --prediction "$PRED_ID" --failing $KNOWN > "$OUT/outcome.json"
fi
"${SHOW[@]}" outcome < "$OUT/outcome.json"
"$PY" - "$OUT/predict.json" "$OUT/outcome.json" <<'EOF'
import json, sys
pred = json.load(open(sys.argv[1]))["predicted_tests"]; out = json.load(open(sys.argv[2]))
ranks = sorted(pred.index(t) + 1 for t in out["matched"])
print(f"  the failing tests were predicted at positions {ranks} of {len(pred)} (impact order: direct tests first)")
assert out["recall"] == 1.0, f"recall {out['recall']}: the prediction missed {out['missed']}"
EOF
git -C "$WORK" checkout -q -- "$AUTH_FILE"

# --------------------------------------------------------------------------- 8
step "Compile the contract's capabilities into Claude Code settings" \
     "Allowed paths and egress become sandbox and permission rules; what settings cannot enforce is said out loud."
sg capabilities compile rate-limit > "$OUT/capabilities.json"
"${SHOW[@]}" caps < "$OUT/capabilities.json"

# --------------------------------------------------------------------------- 9
step "Spanner Graph on the local emulator" \
     "Same tables as GoogleSQL with CREATE PROPERTY GRAPH; the ledger and the code graph are queried together in GQL."
sg spanner ddl --out "$OUT/spanner.sql" >/dev/null
echo "  $(grep -c '^CREATE TABLE' "$OUT/spanner.sql") tables, $(grep -c '^CREATE .*INDEX' "$OUT/spanner.sql") indexes, $(grep -c '^CREATE PROPERTY GRAPH' "$OUT/spanner.sql") property graph -> $OUT/spanner.sql"
sed -n '/^CREATE PROPERTY GRAPH/,/^  NODE TABLES/p' "$OUT/spanner.sql" | sed 's/^/    /'
if [ "${DEMO_SKIP_SPANNER:-}" = 1 ]; then
  echo "  (DEMO_SKIP_SPANNER=1: export skipped)"
else
  if ! "$PY" -c 'import socket,sys; h,p=sys.argv[1].rsplit(":",1); socket.create_connection((h,int(p)),1)' "$SPANNER_EMULATOR_HOST" 2>/dev/null; then
    echo "  starting the emulator (docker compose --profile spanner up -d spanner)"
    (cd "$ROOT" && docker compose --profile spanner up -d spanner >/dev/null 2>&1) || fail "could not start the Spanner emulator"
  fi
  sg spanner export --slug rate-limit --symbol "$TOUCHED_QUALNAME" | "${SHOW[@]}" spanner
fi

# --------------------------------------------------------------------------- 10
step "The agent's view: the MCP server on stdio" \
     "Claude Code reaches the same functions through 23 MCP tools; the guards hold there too."
"$PY" "$ROOT/demo/mcp_probe.py" "$SG" "$REPO" rate-limit "$AUTH_FILE" 2>"$OUT/mcp.stderr" | sed 's/^/  /' \
  || { cat "$OUT/mcp.stderr" >&2; fail "MCP probe"; }
MCP_RC=0; MCP_TESTS="$(cd "$ROOT" && "$PY" -m pytest -q -p no:cacheprovider tests/graph/test_mcp.py 2>&1)" || MCP_RC=$?
echo "  tests/graph/test_mcp.py: $(tail -1 <<<"$MCP_TESTS")"
[ "$MCP_RC" -eq 0 ] || { echo "$MCP_TESTS" >&2; fail "tests/graph/test_mcp.py"; }

printf '\n\033[1mDemo complete in %ss.\033[0m Outputs: %s\n' "$((SECONDS - START))" "$OUT"
