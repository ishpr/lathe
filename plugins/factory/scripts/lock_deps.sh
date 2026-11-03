#!/usr/bin/env bash
# lock_deps.sh: write a hash-pinned dependency lock for the repo's stack (REC-24).
#
# Usage: lock_deps.sh [--root DIR] [--stack python|node|go|cargo] [--out FILE] [--upgrade]
#                     [--min-age-days N] [--check] [--dry-run]
#
# Why: an unpinned SQLAlchemy 2.1 broke `postgresql://` and an agent can add a
# hallucinated package name. A hash-pinned lock plus `--require-hashes` in CI closes both.
#
# Per stack (detected from the files when --stack is omitted):
#   python  uv pip compile --generate-hashes (preferred) or pip-compile --generate-hashes, from
#           requirements.in, else pyproject.toml, else requirements.txt. Output requirements.lock
#           (override with --out). CI installs: pip install --require-hashes -r requirements.lock
#   node    npm install --package-lock-only --ignore-scripts (package-lock.json carries integrity
#           hashes); pnpm-lock.yaml -> pnpm install --lockfile-only; yarn.lock -> yarn install --mode=update-lockfile.
#   go      go mod tidy && go mod verify (go.sum holds the module hashes)
#   cargo   cargo generate-lockfile (Cargo.lock holds checksums); CI uses cargo build --locked.
# --upgrade re-resolves to the newest versions (python: --upgrade; cargo: cargo update).
# --min-age-days N (python, uv only): ignore releases newer than N days (uv --exclude-newer).
# --check writes nothing: python verifies every pinned line of the lock carries a hash; the other
#         stacks verify the lock file exists. Exit 1 when it does not.
# --dry-run prints the command it would run.
# Exit: 0 ok, 1 --check failed, 2 misuse / tool missing / stack not detected.
set -uo pipefail

die() { echo "lock_deps: $*" >&2; exit 2; }
ROOT="."; STACK=""; OUT=""; UPGRADE=0; AGE=""; CHECK=0; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    --root) [ $# -ge 2 ] || die "--root needs a value"; ROOT="$2"; shift 2 ;;
    --stack) [ $# -ge 2 ] || die "--stack needs a value"; STACK="$2"; shift 2 ;;
    --out) [ $# -ge 2 ] || die "--out needs a value"; OUT="$2"; shift 2 ;;
    --min-age-days) [ $# -ge 2 ] || die "--min-age-days needs a value"; AGE="$2"; shift 2 ;;
    --upgrade) UPGRADE=1; shift ;;
    --check) CHECK=1; shift ;;
    --dry-run) DRY=1; shift ;;
    *) die "unknown option $1" ;;
  esac
done
cd "$ROOT" 2>/dev/null || die "cannot enter $ROOT"
[ -z "$AGE" ] || [[ "$AGE" =~ ^[0-9]+$ ]] || die "--min-age-days must be an integer"

if [ -z "$STACK" ]; then
  if [ -f requirements.in ] || [ -f pyproject.toml ] || [ -f requirements.txt ]; then STACK=python
  elif [ -f package.json ]; then STACK=node
  elif [ -f go.mod ]; then STACK=go
  elif [ -f Cargo.toml ]; then STACK=cargo
  else die "cannot detect the stack (no requirements.in, pyproject.toml, requirements.txt, package.json, go.mod or Cargo.toml)"; fi
fi

# run CMD...; with --dry-run just print it
run() {
  if [ "$DRY" -eq 1 ]; then echo "[dry-run] (cd $PWD && $*)"; return 0; fi
  echo "+ $*"; "$@"
}
need() { [ "$DRY" -eq 1 ] || command -v "$1" >/dev/null 2>&1 || die "$1 not found: $2"; }

case "$STACK" in
  python)
    OUT="${OUT:-requirements.lock}"
    if [ "$CHECK" -eq 1 ]; then
      [ -f "$OUT" ] || { echo "lock_deps: $OUT missing"; exit 1; }
      python3 -I - "$OUT" <<'EOF'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read().replace("\\\n", " ")
bad = []
for line in text.splitlines():
    line = line.strip()
    if not line or line.startswith(("#", "-")):
        continue
    if "==" in line and "--hash=sha256:" not in line:
        bad.append(line.split()[0])
if bad:
    print("lock_deps: pinned without a hash: " + ", ".join(bad)); sys.exit(1)
print("lock_deps: every pinned requirement in %s carries a hash" % sys.argv[1])
EOF
      exit $?
    fi
    if [ -f requirements.in ]; then SRC=requirements.in
    elif [ -f pyproject.toml ]; then SRC=pyproject.toml
    else SRC=requirements.txt; fi
    if command -v uv >/dev/null 2>&1 || { [ "$DRY" -eq 1 ] && ! command -v pip-compile >/dev/null 2>&1; }; then
      ARGS=(uv pip compile "$SRC" --generate-hashes -o "$OUT")
      [ "$UPGRADE" -eq 1 ] && ARGS+=(--upgrade)
      if [ -n "$AGE" ]; then
        CUT="$(python3 -I -c 'import datetime,sys;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=int(sys.argv[1]))).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$AGE")"
        ARGS+=(--exclude-newer "$CUT")
      fi
      run "${ARGS[@]}" || exit $?
    else
      need pip-compile "install uv (https://docs.astral.sh/uv/) or pip-tools (pip install pip-tools)"
      [ -z "$AGE" ] || echo "lock_deps: --min-age-days needs uv; ignored with pip-compile" >&2
      ARGS=(pip-compile --generate-hashes --allow-unsafe --output-file "$OUT" "$SRC")
      [ "$UPGRADE" -eq 1 ] && ARGS+=(--upgrade)
      run "${ARGS[@]}" || exit $?
    fi
    echo "lock_deps: CI should install with: pip install --require-hashes -r $OUT"
    ;;
  node)
    if [ "$CHECK" -eq 1 ]; then
      for f in package-lock.json pnpm-lock.yaml yarn.lock; do [ -f "$f" ] && { echo "lock_deps: $f present"; exit 0; }; done
      echo "lock_deps: no node lock file"; exit 1
    fi
    if [ -f pnpm-lock.yaml ]; then need pnpm "install pnpm"; run pnpm install --lockfile-only --ignore-scripts
    elif [ -f yarn.lock ]; then need yarn "install yarn"; run yarn install --mode=update-lockfile --ignore-scripts
    else need npm "install Node.js"; run npm install --package-lock-only --ignore-scripts; fi
    ;;
  go)
    if [ "$CHECK" -eq 1 ]; then [ -f go.sum ] && { echo "lock_deps: go.sum present"; exit 0; }; echo "lock_deps: go.sum missing"; exit 1; fi
    need go "install Go"
    [ "$UPGRADE" -eq 1 ] && { run go get -u ./... || exit $?; }
    run go mod tidy || exit $?
    run go mod verify
    ;;
  cargo)
    if [ "$CHECK" -eq 1 ]; then [ -f Cargo.lock ] && { echo "lock_deps: Cargo.lock present"; exit 0; }; echo "lock_deps: Cargo.lock missing"; exit 1; fi
    need cargo "install Rust (rustup)"
    if [ "$UPGRADE" -eq 1 ] || [ ! -f Cargo.lock ]; then run cargo generate-lockfile; else run cargo update --workspace; fi
    ;;
  *) die "unknown stack '$STACK' (python, node, go, cargo)" ;;
esac
