#!/usr/bin/env bash
# Scaffold: build the item-get fixture at state tests_frozen (tests/evals/_fixtures/build.sh).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE_CASE=implement-item-get-from-frozen exec bash "$HERE/../../_fixtures/build.sh" item-get tests_frozen
