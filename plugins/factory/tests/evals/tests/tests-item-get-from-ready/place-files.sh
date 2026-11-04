#!/usr/bin/env bash
# Scaffold: build the item-get fixture at state ready (tests/evals/_fixtures/build.sh).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE_CASE=tests-item-get-from-ready exec bash "$HERE/../../_fixtures/build.sh" item-get ready
