#!/usr/bin/env bash
# Scaffold: build the item-get fixture at state implemented (tests/evals/_fixtures/build.sh).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE_CASE=verify-item-get-green exec bash "$HERE/../../_fixtures/build.sh" item-get implemented
