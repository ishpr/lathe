#!/usr/bin/env bash
# Prove a per-key rate limit against a running server, not a unit test:
# N requests with one key → 2xx until the limit, then 429 carrying X-RateLimit-* and Retry-After.
#
# Usage: bash smoke_rate_limit.sh BASE_URL API_KEY [N=101] [PATH=/] [HEADER=X-API-Key]
set -euo pipefail
BASE="${1:?base url, e.g. http://localhost:8000}"
KEY="${2:?api key}"
N="${3:-101}"
ROUTE="${4:-/}"
HEADER="${5:-X-API-Key}"

ok=0; limited=0; other=0; first429=""; last2xx=""
for _ in $(seq 1 "$N"); do
  out="$(curl -s -o /dev/null -D - -H "$HEADER: $KEY" "$BASE$ROUTE")"
  code="$(printf '%s' "$out" | head -1 | awk '{print $2}')"
  case "$code" in
    429) limited=$((limited + 1)); [ -z "$first429" ] && first429="$out" ;;
    2*)  ok=$((ok + 1)); last2xx="$out" ;;
    *)   other=$((other + 1)) ;;
  esac
done

echo "requests=$N 2xx=$ok 429=$limited other=$other"
echo "-- headers on the last 2xx:"; printf '%s\n' "$last2xx" | grep -iE '^x-ratelimit-' || echo "   (none)"
echo "-- headers on the first 429:"; printf '%s\n' "$first429" | grep -iE '^(x-ratelimit-|retry-after)' || echo "   (none)"
[ "$limited" -ge 1 ] || { echo "FAIL: no 429 after $N requests"; exit 1; }
printf '%s\n' "$first429" | grep -qiE '^x-ratelimit-remaining:\s*0' || { echo "FAIL: 429 without X-RateLimit-Remaining: 0"; exit 1; }
printf '%s\n' "$first429" | grep -qiE '^retry-after:' || { echo "FAIL: 429 without Retry-After"; exit 1; }
echo "PASS"
