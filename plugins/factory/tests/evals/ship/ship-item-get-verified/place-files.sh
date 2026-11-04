#!/usr/bin/env bash
# Scaffold: build the item-get fixture at state verified --remote (tests/evals/_fixtures/build.sh).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE_CASE=ship-item-get-verified exec bash "$HERE/../../_fixtures/build.sh" item-get verified --remote
