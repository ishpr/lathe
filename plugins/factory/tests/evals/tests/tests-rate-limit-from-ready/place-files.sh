#!/usr/bin/env bash
# Scaffold: build the rate-limit fixture at state ready (tests/evals/_fixtures/build.sh).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE_CASE=tests-rate-limit-from-ready exec bash "$HERE/../../_fixtures/build.sh" rate-limit ready
