#!/usr/bin/env bash
# Scaffold: build the rate-limit fixture at state verified --remote (tests/evals/_fixtures/build.sh).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE_CASE=ship-rate-limit-verified exec bash "$HERE/../../_fixtures/build.sh" rate-limit verified --remote
