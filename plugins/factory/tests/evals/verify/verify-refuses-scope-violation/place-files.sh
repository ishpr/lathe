#!/usr/bin/env bash
# Scaffold: build the item-get fixture at state implemented --plant scope (tests/evals/_fixtures/build.sh).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE_CASE=verify-refuses-scope-violation exec bash "$HERE/../../_fixtures/build.sh" item-get implemented --plant scope
