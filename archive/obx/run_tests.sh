#!/bin/sh
# Builds the runtime if needed and runs the whole test suite.
# Extra arguments go to tests/run.py, e.g. ./run_tests.sh -k optional -v
set -e
cd "$(dirname "$0")"
[ -f runtime_text.bin ] || make -s
exec python3 tests/run.py "$@"
