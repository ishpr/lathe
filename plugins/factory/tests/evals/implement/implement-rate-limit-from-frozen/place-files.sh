#!/usr/bin/env bash
# Scaffold: build the rate-limit fixture at state tests_frozen (tests/evals/_fixtures/build.sh).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE_CASE=implement-rate-limit-from-frozen exec bash "$HERE/../../_fixtures/build.sh" rate-limit tests_frozen
